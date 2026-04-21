"""
Source audio downloader background worker.

Continuously downloads audio from LibriVox (1 chapter → 10 clips of 10–40s per round),
uploads to the corpus bucket, and prunes oldest entries when over threshold.
Manifest (order of generated audio) is persisted to JSON for restart safety.
"""

from __future__ import annotations

import asyncio
import json
import os
import random

# When run as __main__, load .env from project root so HIPPIUS_* etc. are set (same as pytest conftest)
if __name__ == "__main__":
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
    _env_path = _root / ".env"
    if _env_path.exists():
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    _k, _v = _k.strip(), _v.strip()
                    if _k:
                        if len(_v) >= 2 and _v[0] == _v[-1] and _v[0] in "\"'":
                            _v = _v[1:-1]
                        os.environ.setdefault(_k, _v)
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from minio import Minio

from vocence.shared.logging import emit_log, print_header
from vocence.domain.config import (
    AUDIO_SOURCE_BUCKET,
    AUDIO_SOURCE_MAX_DURATION_SEC,
    AUDIO_CORPUS_MAX_ENTRIES,
    AUDIO_CORPUS_MANIFEST_PATH,
    SOURCE_AUDIO_DOWNLOAD_INTERVAL,
    LIBRIVOX_CLIPS_PER_CHAPTER,
    LIBRIVOX_CLIP_MIN_SEC,
    LIBRIVOX_CLIP_MAX_SEC,
)
from vocence.adapters.storage import create_corpus_storage_client, ensure_bucket_available


# Margin (seconds) under validator max so FFmpeg frame/sample alignment cannot push duration over the limit
FFMPEG_DURATION_MARGIN_SEC = 0.05


def _extract_clip_ffmpeg_sync(src_path: str, start_sec: float, duration_sec: float, out_path: str) -> bool:
    """Extract one clip using ffmpeg. Returns True on success.
    duration_sec is capped so output stays within validator's AUDIO_SOURCE_MAX_DURATION_SEC.
    """
    cap = max(0.0, float(AUDIO_SOURCE_MAX_DURATION_SEC) - FFMPEG_DURATION_MARGIN_SEC)
    actual_dur = min(duration_sec, cap)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(start_sec), "-i", src_path,
        "-t", str(round(actual_dur, 2)),
        "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0


# --- LibriVox (from audio_scraper/download_librivox_clips.py) ---
LIBRIVOX_API = "https://librivox.org/api/feed/audiobooks"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch_audiobooks_sync(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Fetch one page of LibriVox audiobooks with sections."""
    url = f"{LIBRIVOX_API}/?limit={limit}&offset={offset}&format=json&extended=1"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    out = json.loads(data)
    return out.get("books") or []


def _playtime_sec(section: Dict[str, Any]) -> float:
    try:
        return float(section.get("playtime", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _pick_random_chapter_sync(
    rng: random.Random,
    min_duration_sec: float,
    max_attempts: int = 20,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Pick a random English book and chapter with at least min_duration_sec."""
    for _ in range(max_attempts):
        offset = rng.randint(0, 500) * 50
        books = _fetch_audiobooks_sync(limit=50, offset=offset)
        if not books and offset > 0:
            books = _fetch_audiobooks_sync(limit=50, offset=0)
        if not books:
            continue
        books_en = [b for b in books if (b.get("language") or "").strip().lower() == "english"]
        if not books_en:
            continue
        book = rng.choice(books_en)
        sections = book.get("sections") or []
        long_enough = [s for s in sections if _playtime_sec(s) >= min_duration_sec]
        if not long_enough:
            continue
        section = rng.choice(long_enough)
        return (book, section)
    return None


def _download_librivox_chapter_sync(listen_url: str, path: str) -> bool:
    """Download chapter MP3 to path. Returns True on success."""
    try:
        req = Request(listen_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 1000:
            return False
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


# --- Manifest ---
def _manifest_path() -> str:
    return os.path.abspath(AUDIO_CORPUS_MANIFEST_PATH)


def _load_manifest() -> List[Dict[str, Any]]:
    """Load manifest from disk. Returns list of {object_key, source, added_at}."""
    path = _manifest_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("entries", [])
    except Exception:
        return []


def _save_manifest(entries: List[Dict[str, Any]]) -> None:
    """Save manifest to disk."""
    path = _manifest_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = {"entries": entries, "updated_at": datetime.now(timezone.utc).isoformat()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# --- Task ---
class SourceAudioDownloaderTask:
    """Background worker that downloads LibriVox audio and maintains corpus manifest."""

    def __init__(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        emit_log(
            f"Source audio downloader starting (LibriVox only, interval={SOURCE_AUDIO_DOWNLOAD_INTERVAL}s, max_entries={AUDIO_CORPUS_MAX_ENTRIES})",
            "start",
        )
        await asyncio.sleep(15)  # Let other services start first

        storage_client = create_corpus_storage_client()
        await ensure_bucket_available(storage_client, AUDIO_SOURCE_BUCKET)

        while self._running:
            try:
                await self._run_one_round(storage_client)
            except asyncio.CancelledError:
                break
            except Exception as e:
                emit_log(f"Source audio downloader error: {e}", "error")
                import traceback
                traceback.print_exc()
            await asyncio.sleep(SOURCE_AUDIO_DOWNLOAD_INTERVAL)

    def stop(self) -> None:
        self._running = False

    async def _run_one_round(self, storage_client: Minio) -> None:
        try:
            entries = _load_manifest()
            new_keys = await self._download_one_librivox_batch(storage_client)

            if not new_keys:
                return

            now = datetime.now(timezone.utc).isoformat()
            for key, source in new_keys:
                entries.append({"object_key": key, "source": source, "added_at": now})

            # Prune if over threshold (remove oldest first)
            if len(entries) > AUDIO_CORPUS_MAX_ENTRIES:
                to_remove = len(entries) - AUDIO_CORPUS_MAX_ENTRIES
                for _ in range(to_remove):
                    old = entries.pop(0)
                    obj_key = old.get("object_key")
                    if obj_key:
                        try:
                            await asyncio.to_thread(storage_client.remove_object, AUDIO_SOURCE_BUCKET, obj_key)
                            emit_log(f"Pruned old corpus object: {obj_key}", "info")
                        except Exception as e:
                            emit_log(f"Failed to remove {obj_key}: {e}", "warn")

            _save_manifest(entries)
            emit_log(f"Manifest updated: {len(entries)} entries (LibriVox)", "info")
        except Exception as e:
            emit_log(f"LibriVox round failed ({e}), will retry next interval", "warn")

    async def _download_one_librivox_batch(self, storage_client: Minio) -> List[Tuple[str, str]]:
        """Download one LibriVox chapter, extract 10 clips of 10–40s each, upload. Returns [(object_key, 'librivox'), ...]."""
        rng = random.Random()
        min_chapter_sec = LIBRIVOX_CLIPS_PER_CHAPTER * LIBRIVOX_CLIP_MAX_SEC + 60
        chosen = await asyncio.to_thread(_pick_random_chapter_sync, rng, min_chapter_sec)
        if not chosen:
            return []

        book, section = chosen
        book_id = book.get("id", "unknown")
        section_title = (section.get("title") or section.get("section_number") or "chapter")[:40]
        listen_url = section.get("listen_url")
        duration_sec = _playtime_sec(section)
        if not listen_url or duration_sec < min_chapter_sec:
            return []

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            chapter_path = tmp.name
        try:
            ok = await asyncio.to_thread(_download_librivox_chapter_sync, listen_url, chapter_path)
            if not ok:
                return []
            time.sleep(0.5)

            date_prefix = datetime.now().strftime("%Y/%m")
            results: List[Tuple[str, str]] = []

            for i in range(LIBRIVOX_CLIPS_PER_CHAPTER):
                clip_dur = rng.uniform(LIBRIVOX_CLIP_MIN_SEC, LIBRIVOX_CLIP_MAX_SEC)
                # Cap so validator (max 25s) never sees longer; leave small margin for FFmpeg
                clip_dur = min(clip_dur, float(AUDIO_SOURCE_MAX_DURATION_SEC) - FFMPEG_DURATION_MARGIN_SEC)
                max_start = duration_sec - clip_dur - 1
                if max_start <= 0:
                    continue
                start_sec = rng.uniform(0, max_start)
                out_path = chapter_path + f"_clip_{i}.wav"
                if not await asyncio.to_thread(_extract_clip_ffmpeg_sync, chapter_path, start_sec, clip_dur, out_path):
                    continue
                try:
                    # Globally unique key: date prefix + UUID (safe for 100M+ objects)
                    object_key = f"source/librivox/{date_prefix}/{uuid.uuid4().hex}.wav"
                    await asyncio.to_thread(
                        storage_client.fput_object,
                        AUDIO_SOURCE_BUCKET,
                        object_key,
                        out_path,
                        content_type="audio/wav",
                    )
                    results.append((object_key, "librivox"))
                finally:
                    try:
                        os.unlink(out_path)
                    except OSError:
                        pass

            if results:
                emit_log(f"Uploaded {len(results)} LibriVox clips from {section_title[:30]}", "success")
            return results
        finally:
            try:
                os.unlink(chapter_path)
            except OSError:
                pass


async def run_source_audio_downloader_standalone(
    *,
    rounds: Optional[int] = None,
    initial_delay_sec: float = 2.0,
) -> None:
    """Run the source audio downloader as a standalone process.

    Preferred entry: CLI command `vocence corpus source-downloader` (or run this
    module with `python -m ...`).

    Args:
        rounds: If set, run this many rounds then exit. If None, run until cancelled.
        initial_delay_sec: Seconds to wait before first round (default 2.0).
    """
    from vocence.shared.logging import print_header

    print_header("Source Audio Downloader (standalone test)")
    emit_log(f"Manifest: {_manifest_path()}", "info")
    emit_log(f"Max entries: {AUDIO_CORPUS_MAX_ENTRIES}", "info")

    task = SourceAudioDownloaderTask()
    storage_client = create_corpus_storage_client()
    await ensure_bucket_available(storage_client, AUDIO_SOURCE_BUCKET)

    await asyncio.sleep(initial_delay_sec)
    task._running = True

    round_count = 0
    try:
        while task._running:
            await task._run_one_round(storage_client)
            round_count += 1
            if rounds is not None and round_count >= rounds:
                emit_log(f"Completed {rounds} round(s). Exiting.", "success")
                break
            await asyncio.sleep(SOURCE_AUDIO_DOWNLOAD_INTERVAL)
    except asyncio.CancelledError:
        emit_log("Stopped by user", "warn")
    finally:
        task.stop()


def main() -> None:
    """CLI entry point when run as script. Prefer: vocence corpus source-downloader."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the source audio downloader in isolation (no HTTP service).",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Run N rounds then exit (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Initial delay in seconds before first round (default: 2.0)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            run_source_audio_downloader_standalone(
                rounds=args.rounds,
                initial_delay_sec=args.delay,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()


# python vocence/gateway/http/service/tasks/source_audio_downloader.py --rounds 2
