"""
Local audio corpus manager.

Each validator maintains its own source-audio corpus on local disk instead of
reading from a shared S3 corpus bucket. The corpus is a flat directory of WAV
clips (English LibriVox, 20-25s, 22050 Hz mono PCM16). The producer loop keeps
it topped up and prunes the oldest clips so the corpus never exceeds
AUDIO_CORPUS_MAX_ENTRIES (default 10,000) — clip-count limited, FIFO eviction.

Producer:  run_corpus_manager()        -> continuous download/extract/prune loop
Consumer:  prepare_source_audio(id)    -> copy a random unused clip to /tmp for a round

This mirrors the previous owner-side SourceAudioDownloaderTask (LibriVox only,
English-only, 20-25s clips, prune-oldest) but writes to local disk and is run by
every validator independently. The LibriVox/ffmpeg helpers are reused unchanged.
"""

from __future__ import annotations

import asyncio
import os
import random
import shutil
import tempfile
import time
import uuid
from typing import List, Optional

from vocence.shared.logging import emit_log
from vocence.domain.config import (
    CORPUS_LOCAL_DIR,
    AUDIO_CORPUS_MAX_ENTRIES,
    AUDIO_SOURCE_MAX_DURATION_SEC,
    SOURCE_AUDIO_DOWNLOAD_INTERVAL,
    LIBRIVOX_CLIPS_PER_CHAPTER,
    LIBRIVOX_CLIP_MIN_SEC,
    LIBRIVOX_CLIP_MAX_SEC,
    USED_AUDIO_FILES,
    MAX_AUDIO_HISTORY,
)

# Reuse the (pure) LibriVox + ffmpeg helpers from the existing downloader so there
# is a single source of truth for how chapters are fetched and clips extracted.
from vocence.gateway.http.service.tasks.source_audio_downloader import (
    _pick_random_chapter_sync,
    _download_librivox_chapter_sync,
    _extract_clip_ffmpeg_sync,
    _playtime_sec,
    FFMPEG_DURATION_MARGIN_SEC,
)


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
        return 0

    book, section = chosen
    listen_url = section.get("listen_url")
    duration_sec = _playtime_sec(section)
    if not listen_url or duration_sec < min_chapter_sec:
        return 0

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        chapter_path = tmp.name
    written = 0
    try:
        if not _download_librivox_chapter_sync(listen_url, chapter_path):
            return 0
        time.sleep(0.5)

        cap = float(AUDIO_SOURCE_MAX_DURATION_SEC) - FFMPEG_DURATION_MARGIN_SEC
        for i in range(LIBRIVOX_CLIPS_PER_CHAPTER):
            clip_dur = min(rng.uniform(LIBRIVOX_CLIP_MIN_SEC, LIBRIVOX_CLIP_MAX_SEC), cap)
            max_start = duration_sec - clip_dur - 1
            if max_start <= 0:
                continue
            start_sec = rng.uniform(0, max_start)
            # Write to a temp name (not matching *.wav so readers ignore it), then
            # atomically rename into place so select_local_audio never sees a
            # partially written clip.
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
        return written
    finally:
        try:
            os.unlink(chapter_path)
        except OSError:
            pass


async def run_corpus_manager() -> None:
    """Continuous background loop: keep the local corpus topped up and pruned to cap.

    Mirrors the previous owner downloader cadence: one chapter per round every
    SOURCE_AUDIO_DOWNLOAD_INTERVAL seconds. Runs until cancelled.
    """
    _ensure_dir()
    emit_log(
        f"Local corpus manager starting (dir={CORPUS_LOCAL_DIR}, cap={AUDIO_CORPUS_MAX_ENTRIES} clips, "
        f"clips/round={LIBRIVOX_CLIPS_PER_CHAPTER}, interval={SOURCE_AUDIO_DOWNLOAD_INTERVAL}s, "
        f"current={corpus_count()})",
        "start",
    )
    while True:
        try:
            await asyncio.to_thread(_download_one_batch_local_sync)
            await asyncio.to_thread(_prune_to_limit)
        except asyncio.CancelledError:
            emit_log("Local corpus manager cancelled", "warn")
            raise
        except Exception as e:
            emit_log(f"Corpus round failed ({e}); retrying next interval", "warn")
        await asyncio.sleep(SOURCE_AUDIO_DOWNLOAD_INTERVAL)


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
