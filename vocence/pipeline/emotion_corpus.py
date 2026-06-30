"""
Local emotional-audio corpus manager (EARS).

A testnet add-on to the neutral LibriVox corpus (see vocence.pipeline.corpus). A
fraction of sample rounds (EMOTION_TASK_FRACTION) draw their source clip from here
instead, so the already-scored `emotion` trait actually discriminates miners rather
than always being "neutral".

Source: EARS (Expressive Anechoic Recordings of Speech) — 48 kHz anechoic studio
recordings, per-speaker zips published on GitHub releases. We extract the emotional
`freeform` and `sentences` files for the emotions that map onto the pipeline's
`emotion` enum, cut each to a 15-25s window, and store them as 22050 Hz mono PCM16
WAV — the same on-disk format the validator already consumes.

Producer:  run_emotion_corpus_manager()        -> download/extract/prune loop
Consumer:  prepare_source_audio_emotion(id)     -> copy a random unused clip to /tmp

Self-contained: no scoring changes, no shared bucket. Each validator fills its own
EMOTION_CORPUS_LOCAL_DIR. The dataset spec (EARS, these emotions, 15-25s) must match
across honest validators for cross-validator win-rates to converge.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from typing import List, Optional, Tuple

from vocence.shared.logging import emit_log
from vocence.domain.config import (
    EMOTION_CORPUS_LOCAL_DIR,
    EMOTION_CORPUS_MAX_ENTRIES,
    EMOTION_SOURCE_MIN_DURATION_SEC,
    AUDIO_SOURCE_MAX_DURATION_SEC,
    EARS_MAX_SPEAKERS,
    EARS_RELEASE_BASE,
    CORPUS_REFRESH_INTERVAL_SEC,
    USED_AUDIO_FILES,
    MAX_AUDIO_HISTORY,
)

# We pull EVERY EARS emotional `freeform` file (all 22 emotion categories), not a
# hand-picked subset. Rationale: the validator's judge re-derives the source clip's
# `emotion` (one of the 8-enum) from the audio at scoring time, so we don't need to
# pre-map EARS labels — we only need expressive source clips. Pulling all 22 emotions
# maximizes the (finite) pool size and its emotional spread, which is what limits
# overfitting here. EARS `sentences` files are ~9-12s (below the 15s floor) so only
# `freeform` (~15-20s) is used.
_EARS_FREEFORM_RE = re.compile(r"^(p\d+)/emo_([a-z]+)_freeform\.wav$")

# Track which EARS speakers we've already extracted so restarts don't re-download.
_PROCESSED_MARKER = ".processed_speakers"

# Margin (s) under max so ffmpeg frame alignment can't push duration over the limit.
_FFMPEG_DURATION_MARGIN_SEC = 0.05

# Used-clip history (separate object would be cleaner, but reuse the shared one so an
# emotion clip and a LibriVox clip can't be picked back-to-back either).
_USED_EMOTION_FILES = USED_AUDIO_FILES


def _ensure_dir() -> str:
    os.makedirs(EMOTION_CORPUS_LOCAL_DIR, exist_ok=True)
    return EMOTION_CORPUS_LOCAL_DIR


def _list_clips() -> List[str]:
    d = EMOTION_CORPUS_LOCAL_DIR
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".wav")]


def emotion_corpus_count() -> int:
    return len(_list_clips())


def _processed_path() -> str:
    return os.path.join(EMOTION_CORPUS_LOCAL_DIR, _PROCESSED_MARKER)


def _load_processed() -> set:
    p = _processed_path()
    if not os.path.isfile(p):
        return set()
    try:
        with open(p) as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()


def _mark_processed(speaker: str) -> None:
    try:
        with open(_processed_path(), "a") as f:
            f.write(speaker + "\n")
    except OSError as e:
        emit_log(f"Emotion corpus: failed to mark {speaker} processed: {e}", "warn")


def _prune_to_limit() -> int:
    """FIFO eviction (oldest mtime first) so the corpus holds at most the cap."""
    clips = _list_clips()
    overflow = len(clips) - EMOTION_CORPUS_MAX_ENTRIES
    if overflow <= 0:
        return 0
    clips.sort(key=lambda p: os.path.getmtime(p))
    removed = 0
    for path in clips[:overflow]:
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            emit_log(f"Emotion corpus prune failed for {os.path.basename(path)}: {e}", "warn")
    if removed:
        emit_log(f"Pruned {removed} oldest emotion clips (cap {EMOTION_CORPUS_MAX_ENTRIES})", "info")
    return removed


def _probe_duration_sync(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            timeout=30,
        )
        return float(out.decode().strip() or 0.0)
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _cut_to_corpus_format_sync(src_path: str, out_path: str, rng: random.Random) -> bool:
    """Cut a 15-25s window from src and write 22050 Hz mono PCM16 WAV. Returns True on success.

    - dur < EMOTION_SOURCE_MIN_DURATION_SEC -> skip (caller drops it).
    - EMOTION_SOURCE_MIN .. MAX             -> use the whole file.
    - dur > MAX                             -> random MAX-second window.
    """
    dur = _probe_duration_sync(src_path)
    if dur < EMOTION_SOURCE_MIN_DURATION_SEC:
        return False
    cap = float(AUDIO_SOURCE_MAX_DURATION_SEC) - _FFMPEG_DURATION_MARGIN_SEC
    out_len = min(dur, cap)
    start = rng.uniform(0.0, dur - out_len) if dur > out_len else 0.0
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(round(start, 2)), "-i", src_path,
        "-t", str(round(out_len, 2)),
        "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "wav",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    return r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def _download_ears_speaker_sync(speaker: str, dest_zip: str) -> bool:
    """Stream one EARS speaker zip (~590MB) to disk. Returns True on success."""
    url = f"{EARS_RELEASE_BASE}/{speaker}.zip"
    # Streamed via curl to avoid buffering ~590MB in memory.
    try:
        r = subprocess.run(
            ["curl", "-sL", "--fail", "-o", dest_zip, url],
            capture_output=True, timeout=1800,
        )
    except subprocess.SubprocessError as e:
        emit_log(f"Emotion corpus: download {speaker} failed ({e})", "warn")
        return False
    if r.returncode != 0 or not os.path.isfile(dest_zip) or os.path.getsize(dest_zip) < 1_000_000:
        emit_log(f"Emotion corpus: download {speaker} failed (curl rc={r.returncode})", "warn")
        return False
    return True


def _extract_speaker_clips_sync(speaker: str, zip_path: str, rng: random.Random) -> int:
    """Extract mapped emotional clips from one speaker zip into the corpus dir.

    Returns the number of clips written. Each clip filename encodes the pipeline
    emotion label and speaker (for inspection only — selection is random and the
    judge re-derives the emotion).
    """
    written = 0
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        emit_log(f"Emotion corpus: {speaker} zip is corrupt", "warn")
        return 0
    # Every emo_*_freeform.wav for this speaker (all 22 EARS emotion categories).
    members = sorted(
        n for n in zf.namelist()
        if n.startswith(f"{speaker}/") and _EARS_FREEFORM_RE.match(n)
    )
    with tempfile.TemporaryDirectory() as tmpd:
        for member in members:
            ears_emo = _EARS_FREEFORM_RE.match(member).group(2)
            raw = os.path.join(tmpd, f"{ears_emo}.wav")
            try:
                with zf.open(member) as s, open(raw, "wb") as o:
                    shutil.copyfileobj(s, o, length=1024 * 1024)
            except (KeyError, OSError):
                continue
            # Filename keeps the EARS emotion token as provenance only; the scored
            # emotion comes from the judge re-labelling the audio at round time.
            final = os.path.join(EMOTION_CORPUS_LOCAL_DIR, f"{ears_emo}_{speaker}_{uuid.uuid4().hex}.wav")
            tmp_out = final + ".tmp"
            if _cut_to_corpus_format_sync(raw, tmp_out, rng):
                os.replace(tmp_out, final)  # atomic on same filesystem
                written += 1
            elif os.path.isfile(tmp_out):
                try:
                    os.remove(tmp_out)
                except OSError:
                    pass
    return written


def _process_one_speaker_sync() -> Tuple[Optional[str], int]:
    """Download + extract the next unprocessed EARS speaker. Returns (speaker, clips_written).

    Returns (None, 0) when every targeted speaker is already processed.
    """
    _ensure_dir()
    processed = _load_processed()
    speaker = None
    for i in range(1, EARS_MAX_SPEAKERS + 1):
        cand = f"p{i:03d}"
        if cand not in processed:
            speaker = cand
            break
    if speaker is None:
        return None, 0

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name
    rng = random.Random()
    try:
        if not _download_ears_speaker_sync(speaker, zip_path):
            return speaker, 0  # marked processed below to avoid hammering a bad URL
        written = _extract_speaker_clips_sync(speaker, zip_path, rng)
        _prune_to_limit()
        if written:
            emit_log(f"Emotion corpus: added {written} clips from EARS {speaker}", "success")
        else:
            emit_log(f"Emotion corpus: EARS {speaker} yielded 0 usable clips", "warn")
        return speaker, written
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass


async def run_emotion_corpus_manager() -> None:
    """Continuous loop: pull EARS speakers one at a time until EARS_MAX_SPEAKERS are
    processed, then idle (slow refresh). Each speaker is downloaded once; restarts skip
    already-processed speakers via the on-disk marker. Runs until cancelled.
    """
    _ensure_dir()
    emit_log(
        f"Emotion corpus manager starting (dir={EMOTION_CORPUS_LOCAL_DIR}, "
        f"cap={EMOTION_CORPUS_MAX_ENTRIES} clips, target_speakers={EARS_MAX_SPEAKERS}, "
        f"current={emotion_corpus_count()})",
        "start",
    )
    while True:
        try:
            speaker, _written = await asyncio.to_thread(_process_one_speaker_sync)
        except asyncio.CancelledError:
            emit_log("Emotion corpus manager cancelled", "warn")
            raise
        except Exception as e:
            emit_log(f"Emotion corpus round failed ({e}); retrying shortly", "warn")
            await asyncio.sleep(30)
            continue

        if speaker is None:
            # All targeted speakers processed — idle with slow freshness rotation.
            await asyncio.sleep(CORPUS_REFRESH_INTERVAL_SEC)
            continue

        # Mark processed even on 0 clips so we advance instead of retrying a bad speaker.
        await asyncio.to_thread(_mark_processed, speaker)
        # Short pause between heavy downloads so we don't saturate the link.
        await asyncio.sleep(5)


def select_local_emotion_audio() -> Optional[str]:
    """Pick a random emotion clip, avoiding recently-used ones. None if corpus empty."""
    clips = _list_clips()
    if not clips:
        return None
    by_name = {os.path.basename(p): p for p in clips}
    available = [name for name in by_name if name not in _USED_EMOTION_FILES]
    if not available:
        recent = _USED_EMOTION_FILES[-5:] if len(_USED_EMOTION_FILES) >= 5 else []
        _USED_EMOTION_FILES.clear()
        _USED_EMOTION_FILES.extend(recent)
        available = [name for name in by_name if name not in _USED_EMOTION_FILES]
        if not available:
            available = list(by_name.keys())
    chosen = random.choice(available)
    _USED_EMOTION_FILES.append(chosen)
    if len(_USED_EMOTION_FILES) > MAX_AUDIO_HISTORY:
        _USED_EMOTION_FILES[:] = _USED_EMOTION_FILES[-MAX_AUDIO_HISTORY:]
    return by_name[chosen]


async def prepare_source_audio_emotion(evaluation_id: str) -> Optional[Tuple[str, str]]:
    """Select an emotion clip and copy it to a per-round /tmp path. Mirrors
    corpus.prepare_source_audio so generation can use either source interchangeably.

    Returns (tmp_path, corpus_clip_basename), or None if no clip is available.
    """
    src = select_local_emotion_audio()
    if not src:
        return None
    dest = f"/tmp/source_audio_{evaluation_id}.wav"
    await asyncio.to_thread(shutil.copyfile, src, dest)
    return dest, os.path.basename(src)
