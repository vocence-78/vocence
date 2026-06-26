"""
Local audio corpus manager.

Each validator maintains its own source-audio corpus on local disk instead of
reading from a shared S3 corpus bucket. The corpus is a flat directory of WAV
clips (English LibriVox, 20-25s, 22050 Hz mono PCM16). The producer loop keeps
it topped up and prunes the oldest clips so the corpus never exceeds
AUDIO_CORPUS_MAX_ENTRIES (default 10,000) — clip-count limited, FIFO eviction.

Producer:  run_corpus_manager()        -> continuous download/extract/prune loop
Consumer:  prepare_source_audio(id)    -> copy a random unused clip to /tmp for a round

LibriVox-only, English-only, 20-25s clips, prune-oldest — run by every validator
independently against local disk (no shared S3 corpus bucket).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vocence.shared.logging import emit_log
from vocence.domain.config import (
    CORPUS_LOCAL_DIR,
    AUDIO_CORPUS_MAX_ENTRIES,
    AUDIO_SOURCE_MAX_DURATION_SEC,
    SOURCE_AUDIO_DOWNLOAD_INTERVAL,
    CORPUS_REFRESH_INTERVAL_SEC,
    CORPUS_RATE_LIMIT_BACKOFF_SEC,
    LIBRIVOX_CLIPS_PER_CHAPTER,
    LIBRIVOX_CLIP_MIN_SEC,
    LIBRIVOX_CLIP_MAX_SEC,
    USED_AUDIO_FILES,
    MAX_AUDIO_HISTORY,
)


class CorpusRateLimited(Exception):
    """Raised when LibriVox returns HTTP 429; carries an optional retry-after (seconds)."""

    def __init__(self, retry_after: Optional[float] = None):
        super().__init__("LibriVox rate limited (HTTP 429)")
        self.retry_after = retry_after

# ---------------------------------------------------------------------------
# LibriVox fetch + ffmpeg clip extraction (English-only, public domain audio).
# Self-contained here so the validator has no dependency on the old owner-side
# S3 corpus downloader.
# ---------------------------------------------------------------------------
LIBRIVOX_API = "https://librivox.org/api/feed/audiobooks"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
# Margin (seconds) under max so ffmpeg frame/sample alignment cannot push duration over the limit.
FFMPEG_DURATION_MARGIN_SEC = 0.05


def _extract_clip_ffmpeg_sync(src_path: str, start_sec: float, duration_sec: float, out_path: str) -> bool:
    """Extract one clip with ffmpeg (22050 Hz mono PCM16). Returns True on success.

    duration_sec is capped so output stays within AUDIO_SOURCE_MAX_DURATION_SEC.
    """
    cap = max(0.0, float(AUDIO_SOURCE_MAX_DURATION_SEC) - FFMPEG_DURATION_MARGIN_SEC)
    actual_dur = min(duration_sec, cap)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(start_sec), "-i", src_path,
        "-t", str(round(actual_dur, 2)),
        "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le",
        # Force the WAV muxer: out_path is written to a "<uuid>.wav.tmp" temp name
        # (atomic rename), and ffmpeg otherwise infers the format from the extension
        # — ".tmp" is unknown and would fail. -f wav makes the extension irrelevant.
        "-f", "wav",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0


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
    """Pick a random English book and chapter with at least min_duration_sec.

    Raises CorpusRateLimited if LibriVox returns HTTP 429 so the caller can back off.
    Other transient fetch errors just skip to the next attempt (different offset).
    """
    for _ in range(max_attempts):
        offset = rng.randint(0, 500) * 50
        try:
            books = _fetch_audiobooks_sync(limit=50, offset=offset)
            if not books and offset > 0:
                books = _fetch_audiobooks_sync(limit=50, offset=0)
        except HTTPError as e:
            if e.code == 429:
                retry_after = None
                try:
                    retry_after = float(e.headers.get("Retry-After")) if e.headers else None
                except (TypeError, ValueError):
                    retry_after = None
                raise CorpusRateLimited(retry_after) from e
            continue  # other HTTP error: try a different offset
        except Exception:
            continue  # transient network/parse error: try a different offset
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


def _ensure_dir() -> str:
    os.makedirs(CORPUS_LOCAL_DIR, exist_ok=True)
    return CORPUS_LOCAL_DIR


def _list_clips() -> List[str]:
    """Return absolute paths of all WAV clips currently in the local corpus."""
    d = CORPUS_LOCAL_DIR
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".wav")]


def corpus_count() -> int:
    return len(_list_clips())


def _prune_to_limit() -> int:
    """Remove oldest clips (by mtime) so the corpus holds at most AUDIO_CORPUS_MAX_ENTRIES.

    FIFO eviction: filesystem mtime is the insertion time, so the oldest clips go
    first. Returns the number of clips removed.
    """
    clips = _list_clips()
    overflow = len(clips) - AUDIO_CORPUS_MAX_ENTRIES
    if overflow <= 0:
        return 0
    clips.sort(key=lambda p: os.path.getmtime(p))  # oldest first
    removed = 0
    for path in clips[:overflow]:
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            emit_log(f"Corpus prune failed for {os.path.basename(path)}: {e}", "warn")
    if removed:
        emit_log(f"Pruned {removed} oldest corpus clips (cap {AUDIO_CORPUS_MAX_ENTRIES})", "info")
    return removed


def _download_one_batch_local_sync() -> int:
    """Download one LibriVox chapter, extract up to LIBRIVOX_CLIPS_PER_CHAPTER clips
    of 20-25s, and write each to the local corpus dir. Returns clips written.

    Synchronous (network + ffmpeg); call via asyncio.to_thread.
    """
    _ensure_dir()
    rng = random.Random()
    # Chapter must be long enough to yield the requested clips with headroom.
    min_chapter_sec = LIBRIVOX_CLIPS_PER_CHAPTER * LIBRIVOX_CLIP_MAX_SEC + 60
    chosen = _pick_random_chapter_sync(rng, min_chapter_sec)
    if not chosen:
        emit_log("Corpus round: no suitable chapter found", "warn")
        return 0

    book, section = chosen
    listen_url = section.get("listen_url")
    duration_sec = _playtime_sec(section)
    if not listen_url or duration_sec < min_chapter_sec:
        emit_log("Corpus round: chosen chapter unusable (no url / too short)", "warn")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        chapter_path = tmp.name
    written = 0
    try:
        if not _download_librivox_chapter_sync(listen_url, chapter_path):
            emit_log("Corpus round: chapter download failed", "warn")
            return 0
        time.sleep(0.5)

        cap = float(AUDIO_SOURCE_MAX_DURATION_SEC) - FFMPEG_DURATION_MARGIN_SEC
        for i in range(LIBRIVOX_CLIPS_PER_CHAPTER):
            clip_dur = min(rng.uniform(LIBRIVOX_CLIP_MIN_SEC, LIBRIVOX_CLIP_MAX_SEC), cap)
            max_start = duration_sec - clip_dur - 1
            if max_start <= 0:
                continue
            start_sec = rng.uniform(0, max_start)
            # Write to a temp name then atomic-rename so readers never see a partial clip.
            final_path = os.path.join(CORPUS_LOCAL_DIR, f"{uuid.uuid4().hex}.wav")
            tmp_path = final_path + ".tmp"
            if _extract_clip_ffmpeg_sync(chapter_path, start_sec, clip_dur, tmp_path):
                os.replace(tmp_path, final_path)  # atomic on same filesystem
                written += 1
            elif os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        if written:
            section_title = (section.get("title") or "chapter")[:30]
            emit_log(f"Corpus: added {written} clips from {section_title}", "success")
        else:
            emit_log("Corpus round: chapter downloaded but 0 clips extracted (ffmpeg)", "warn")
        return written
    finally:
        try:
            os.unlink(chapter_path)
        except OSError:
            pass


def _jittered(seconds: float) -> float:
    """Add up to +25% random jitter so independent validators don't sync their pulls."""
    return seconds * (1.0 + random.random() * 0.25)


async def run_corpus_manager() -> None:
    """Continuous background loop: keep the local corpus topped up and pruned to cap.

    Cadence is load-aware so we don't hammer LibriVox (a free service), which matters
    now that every validator runs this independently:
      - BELOW cap  -> pull every SOURCE_AUDIO_DOWNLOAD_INTERVAL s (fill fast).
      - AT cap     -> pull every CORPUS_REFRESH_INTERVAL_SEC s (slow freshness rotation).
      - On HTTP 429 -> exponential backoff up to CORPUS_RATE_LIMIT_BACKOFF_SEC.
    All sleeps are jittered. Runs until cancelled.
    """
    _ensure_dir()
    emit_log(
        f"Local corpus manager starting (dir={CORPUS_LOCAL_DIR}, cap={AUDIO_CORPUS_MAX_ENTRIES} clips, "
        f"fill_interval={SOURCE_AUDIO_DOWNLOAD_INTERVAL}s, refresh_interval={CORPUS_REFRESH_INTERVAL_SEC}s, "
        f"current={corpus_count()})",
        "start",
    )
    rate_limit_backoff = 0.0
    while True:
        at_cap = corpus_count() >= AUDIO_CORPUS_MAX_ENTRIES
        try:
            await asyncio.to_thread(_download_one_batch_local_sync)
            await asyncio.to_thread(_prune_to_limit)
            rate_limit_backoff = 0.0
        except asyncio.CancelledError:
            emit_log("Local corpus manager cancelled", "warn")
            raise
        except CorpusRateLimited as e:
            base = e.retry_after or (rate_limit_backoff * 2 if rate_limit_backoff else 60.0)
            rate_limit_backoff = min(max(base, 60.0), CORPUS_RATE_LIMIT_BACKOFF_SEC)
            emit_log(
                f"LibriVox rate-limited; backing off {rate_limit_backoff:.0f}s before next pull",
                "warn",
            )
            await asyncio.sleep(_jittered(rate_limit_backoff))
            continue
        except Exception as e:
            emit_log(f"Corpus round failed ({e}); retrying next interval", "warn")

        # Full corpus -> slow maintenance rotation; still filling -> fast cadence.
        interval = CORPUS_REFRESH_INTERVAL_SEC if at_cap else SOURCE_AUDIO_DOWNLOAD_INTERVAL
        await asyncio.sleep(_jittered(interval))


def select_local_audio() -> Optional[str]:
    """Pick a random clip from the local corpus, avoiding recently used ones.

    Returns the absolute path of the chosen clip, or None if the corpus is empty.
    Tracks recently-used basenames in USED_AUDIO_FILES (same history mechanism as
    the previous S3-backed selector).
    """
    clips = _list_clips()
    if not clips:
        emit_log("Local corpus is empty (no clips yet)", "warn")
        return None

    by_name = {os.path.basename(p): p for p in clips}
    available = [name for name in by_name if name not in USED_AUDIO_FILES]

    if not available:
        # All used recently: keep only the last few to still avoid the most recent.
        recent = USED_AUDIO_FILES[-5:] if len(USED_AUDIO_FILES) >= 5 else []
        USED_AUDIO_FILES.clear()
        USED_AUDIO_FILES.extend(recent)
        available = [name for name in by_name if name not in USED_AUDIO_FILES]
        # Tiny corpus where every clip is still in the kept-recent set: fall back to
        # the full set so we never call random.choice on an empty list.
        if not available:
            available = list(by_name.keys())

    chosen_name = random.choice(available)
    USED_AUDIO_FILES.append(chosen_name)
    if len(USED_AUDIO_FILES) > MAX_AUDIO_HISTORY:
        USED_AUDIO_FILES[:] = USED_AUDIO_FILES[-MAX_AUDIO_HISTORY:]
    return by_name[chosen_name]


async def prepare_source_audio(evaluation_id: str) -> Optional[str]:
    """Select a corpus clip and copy it to a per-round /tmp path for evaluation.

    A copy is made (not the original) so the generator's cleanup of the round file
    never deletes a corpus clip. Returns the /tmp path, or None if no clip is
    available.
    """
    src = select_local_audio()
    if not src:
        return None
    dest = f"/tmp/source_audio_{evaluation_id}.wav"
    await asyncio.to_thread(shutil.copyfile, src, dest)
    return dest
