"""
Prompt generation service for Vocence.

Continuously generates samples by:
1. Downloading random audio from corpus (full file, 20–25s)
2. Getting transcription + voice traits from GPT audio model (full audio, no segments)
3. Querying miner TTS models with that task prompt
4. Scoring via GPT audio: which of two full audios is more natural
5. Uploading results to Hippius
"""

import json
import os
import random
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Awaitable, Callable

import aiohttp
from minio import Minio
from openai import AsyncOpenAI

from vocence.domain.config import (
    API_URL,
    CHUTES_AUTH_KEY,
    AUDIO_SOURCE_BUCKET,
    AUDIO_SAMPLES_BUCKET,
    CLIP_LENGTH_SECONDS,
    AUDIO_SOURCE_MIN_DURATION_SEC,
    AUDIO_SOURCE_MAX_DURATION_SEC,
    GPT_AUDIO_MODEL,
    USED_AUDIO_FILES,
    MAX_AUDIO_HISTORY,
    MAX_PARALLEL_MINERS,
    MAX_PARALLEL_EVALS,
    QUERY_TIMEOUT,
    COLDKEY_NAME,
    HOTKEY_NAME,
    VALIDATOR_SAVE_LOCAL_SAMPLES,
    VALIDATOR_LOCAL_SAMPLES_DIR,
    VALIDATOR_ID,
    SAMPLE_SLOT_INTERVAL_BLOCKS,
    SAMPLE_SLOT_OFFSET_BLOCKS,
    SAMPLE_SLOT_BLOCK_TOLERANCE,
)
from vocence.shared.logging import emit_log, print_header
from vocence.adapters.storage import ensure_bucket_available, upload_sample_data
from vocence.adapters.media import get_audio_duration
from vocence.pipeline.evaluation import (
    get_transcription_and_traits_async,
    format_task_prompt_for_tts,
    score_miner_against_spec_async,
)
from vocence.adapters.chutes import fetch_chute_details, construct_chute_endpoint
from vocence.domain.entities import ParticipantInfo

# ~1 block every 12s on typical chains; used when waiting for next block
SECONDS_PER_BLOCK = 12

# Track last sample slot we executed so we only run once per slot when block is in tolerance window
_last_executed_slot_block: int | None = None


async def submit_sample_to_api(
    sample_id: str,
    metadata: Dict[str, Any],
    participant_results: Dict[str, Dict[str, Any]],
) -> bool:
    """Submit sample metadata to centralized API.
    
    Args:
        sample_id: Unique sample identifier
        metadata: Sample metadata dict
        participant_results: Dict of hotkey -> result
        
    Returns:
        True if submitted successfully, False otherwise
    """
    try:
        from vocence.adapters.api import create_service_client_from_wallet
        
        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME,
            hotkey_name=HOTKEY_NAME,
            api_url=API_URL,
        )
        
        prompt_text = metadata.get("prompt", {}).get("text", "")
        
        try:
            # Build one batch per sample — the owner API rate-limits writes
            # per hotkey, so per-miner POSTs would burn the quota fast.
            batch: list[dict] = []
            for hotkey, result in participant_results.items():
                evaluation = result.get("evaluation", {})
                breakdown = evaluation.get("breakdown") or {}
                element_scores = {
                    k: float(v.get("score", 0.0))
                    for k, v in breakdown.items()
                    if isinstance(v, dict) and v.get("score") is not None
                } or None
                payload = {
                    "evaluation_id": sample_id,
                    "participant_hotkey": hotkey,
                    "s3_bucket": AUDIO_SAMPLES_BUCKET,
                    "s3_prefix": sample_id,
                    "wins": bool(evaluation.get("generated_wins", False)),
                    "prompt": prompt_text,
                    "confidence": evaluation.get("confidence"),
                    "reasoning": evaluation.get("reasoning"),
                    "original_audio_url": result.get("original_audio_url"),
                    "generated_audio_url": result.get("generated_audio_url"),
                    "score": evaluation.get("score"),
                    "element_scores": element_scores,
                }
                # Drop None values so server-side optional fields stay optional.
                payload = {k: v for k, v in payload.items() if v is not None}
                batch.append(payload)

            if batch:
                await client.submit_evaluations_batch(batch)

            emit_log(f"Sample {sample_id} submitted to API ({len(batch)} results)", "info")
            return True
        finally:
            await client.close()
            
    except Exception as e:
        emit_log(f"Failed to submit sample to API: {e}", "warn")
        return False


async def _cancel_live_evaluation_safe(evaluation_id: str) -> None:
    """Clear pending live evaluation at the API (fire-and-forget). Log and ignore failures."""
    try:
        from vocence.adapters.api import create_service_client_from_wallet
        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME,
            hotkey_name=HOTKEY_NAME,
            api_url=API_URL,
        )
        try:
            await client.cancel_live_evaluation(evaluation_id)
            emit_log(f"Live evaluation cancelled: eval_id={evaluation_id}", "info")
        finally:
            await client.close()
    except Exception as e:
        emit_log(f"Cancel live evaluation failed (continuing): {e}", "warn")


async def submit_sample_metadata(
    sample_id: str,
    metadata: Dict[str, Any],
    participant_results: Dict[str, Dict[str, Any]],
) -> bool:
    """Submit sample metadata to the API.
    
    All validators must use API mode to submit samples.
    
    Args:
        sample_id: Unique sample identifier
        metadata: Sample metadata dict
        participant_results: Dict of hotkey -> result
        
    Returns:
        True if saved successfully, False otherwise
    """
    return await submit_sample_to_api(sample_id, metadata, participant_results)


async def select_random_audio(corpus_client: Minio) -> str | None:
    """Pick a random audio file from the corpus bucket, avoiding recently used ones.
    
    Uses corpus credentials (owner-provided sub_key); validator must set HIPPIUS_CORPUS_*.
    
    Args:
        corpus_client: Minio client for the corpus bucket (create_corpus_storage_client)
        
    Returns:
        Object name of the selected audio, or None if no suitable audio found
    """
    global USED_AUDIO_FILES
    
    # List all audio files in corpus bucket
    objects = await asyncio.to_thread(
        lambda: list(corpus_client.list_objects(AUDIO_SOURCE_BUCKET, recursive=True))
    )
    
    # Filter for WAV files only (duration is checked after download)
    audio_objects = [obj for obj in objects if obj.object_name.endswith(".wav")]
    
    if not audio_objects:
        emit_log("No suitable audio files found in source bucket", "warn")
        return None
    
    # Filter out recently used audio files
    available = [obj for obj in audio_objects if obj.object_name not in USED_AUDIO_FILES]
    
    # Reset history if all audio files used
    if not available:
        emit_log("All audio files used, resetting history...", "info")
        # Save last 5 items before clearing to avoid re-using most recent audio
        recent_audio = USED_AUDIO_FILES[-5:] if len(USED_AUDIO_FILES) >= 5 else []
        USED_AUDIO_FILES.clear()
        USED_AUDIO_FILES.extend(recent_audio)
        available = [obj for obj in audio_objects if obj.object_name not in USED_AUDIO_FILES]
    
    # Pick random audio file
    chosen = random.choice(available)
    USED_AUDIO_FILES.append(chosen.object_name)
    if len(USED_AUDIO_FILES) > MAX_AUDIO_HISTORY:
        USED_AUDIO_FILES[:] = USED_AUDIO_FILES[-MAX_AUDIO_HISTORY:]
    
    return chosen.object_name


def _prompt_to_speak_payload(prompt: str) -> Dict[str, Any]:
    """Convert task prompt (transcription | traits) to /speak payload: text, instruction (VocenceSpeakRequest)."""
    prompt = (prompt or "").strip() or "Hello."
    if " | " in prompt:
        text_part, _, rest = prompt.partition(" | ")
        text_part = text_part.strip()
        rest = rest.strip()
        # First segment is traits (e.g. "gender: unknown") not a sentence to speak
        if not text_part or ":" in text_part:
            text = "Hello."
            instruction = prompt
        else:
            text = text_part
            instruction = rest or "Neutral tone."
    else:
        text = prompt
        instruction = "Neutral tone."
    return {
        "text": text,
        "instruction": instruction,
    }


def _save_sample_locally(
    evaluation_id: str,
    metadata: Dict[str, Any],
) -> None:
    """Save metadata.json to local dir before upload only when VALIDATOR_SAVE_LOCAL_SAMPLES is enabled. Audio files are not saved locally. Default: disabled (no dir created)."""
    if not VALIDATOR_SAVE_LOCAL_SAMPLES or not VALIDATOR_LOCAL_SAMPLES_DIR:
        return
    sample_dir = os.path.join(VALIDATOR_LOCAL_SAMPLES_DIR, evaluation_id)
    os.makedirs(sample_dir, exist_ok=True)
    metadata_path = os.path.join(sample_dir, "metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    emit_log(f"Saved metadata to {metadata_path}", "info")


async def synthesize_audio_for_participant(
    session: aiohttp.ClientSession,
    endpoint: str,
    prompt: str,
) -> tuple[bytes | None, str | None]:
    """Generate audio using a participant's TTS endpoint (POST /speak: text, instruction).
    
    Args:
        session: aiohttp client session
        endpoint: Participant's chute endpoint URL (e.g. .../speak)
        prompt: Task prompt (transcription | gender: x | emotion: ...)
        
    Returns:
        Tuple of (audio_bytes, error_message). On success: (bytes, None).
    """
    payload = _prompt_to_speak_payload(prompt)
    try:
        async with session.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {CHUTES_AUTH_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=QUERY_TIMEOUT),
        ) as resp:
            if resp.status == 200:
                return await resp.read(), None
            elif resp.status == 429:
                return None, "rate limited"
            elif resp.status == 503:
                return None, "service unavailable"
            else:
                try:
                    body = await resp.text()
                    if body and len(body) <= 300:
                        return None, f"status {resp.status}: {body}"
                except Exception:
                    pass
                return None, f"status {resp.status}"
    except asyncio.TimeoutError:
        return None, f"timeout after {QUERY_TIMEOUT}s"
    except Exception as e:
        return None, str(e)


async def synthesize_audio_for_participants(
    session: aiohttp.ClientSession,
    participants: Dict[str, Dict[str, Any]],
    prompt: str,
) -> Dict[str, tuple[bytes | None, str | None, str | None]]:
    """Generate audio from all participants concurrently.
    
    Args:
        session: aiohttp client session
        participants: Dict of hotkey -> {"chute_id": str, "block": int}
        prompt: Text prompt for synthesis
        
    Returns:
        Dict of hotkey -> (audio_bytes, error_message, endpoint)
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL_MINERS)
    
    async def process_participant(hotkey: str, participant_info: Dict[str, Any]):
        async with semaphore:
            chute_id = participant_info["chute_id"]
            hotkey_short = hotkey[:8]
            
            chute = await fetch_chute_details(session, chute_id)
            if not chute:
                return hotkey, (None, f"chute_id {chute_id} not found", None)
            
            if not chute.get("hot", False):
                return hotkey, (None, "chute not running", None)
            
            slug = chute.get("slug")
            if not slug:
                return hotkey, (None, "chute has no slug", None)
            
            endpoint = construct_chute_endpoint(slug)
            emit_log(f"Calling participant {hotkey_short} at {slug}...", "info")
            
            audio_bytes, error = await synthesize_audio_for_participant(
                session, endpoint, prompt
            )
            
            if audio_bytes:
                emit_log(f"Participant {hotkey_short}: {len(audio_bytes):,} bytes", "success")
            else:
                emit_log(f"Participant {hotkey_short}: {error}", "warn")
            
            return hotkey, (audio_bytes, error, endpoint)
    
    tasks = [process_participant(hk, info) for hk, info in participants.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    output = {}
    for result in results:
        if isinstance(result, Exception):
            emit_log(f"Participant task failed: {result}", "error")
            continue
        hotkey, data = result
        output[hotkey] = data
    
    return output


async def get_valid_participants_from_api() -> list[ParticipantInfo]:
    """Get valid participants from the centralized API.
    
    The API service validates participants (HuggingFace, Chutes, plagiarism)
    so we don't need to re-validate here.
    
    Returns:
        List of valid ParticipantInfo objects
    """
    try:
        from vocence.adapters.api import create_service_client_from_wallet
        
        client = create_service_client_from_wallet(
            wallet_name=COLDKEY_NAME,
            hotkey_name=HOTKEY_NAME,
            api_url=API_URL,
        )
        
        try:
            miners = await client.get_valid_miners()
            return miners
        finally:
            await client.close()
    except Exception as e:
        emit_log(f"Failed to get miners from API: {e}", "warn")
        return []


async def generate_samples_continuously(
    corpus_client: Minio,
    validator_client: Minio,
    openai_client: AsyncOpenAI,
    get_current_block: Callable[[], Awaitable[int]],
) -> None:
    """Continuously generate evaluations: wait for block slot, then download audio from corpus, query participants, score, upload.
    
    Sample rounds run when (current_block % SAMPLE_SLOT_INTERVAL_BLOCKS) == SAMPLE_SLOT_OFFSET_BLOCKS, so 5 validators
    (id 0–4) are staggered at 0, 30, 60, 90, 120 every 150 blocks.
    
    Args:
        corpus_client: Minio client for corpus bucket (create_corpus_storage_client)
        validator_client: Minio client for validator's samples bucket (create_validator_storage_client)
        openai_client: AsyncOpenAI client for GPT-4o
        get_current_block: Async callable returning current chain block number (e.g. subtensor.get_current_block)
    """
    # Ensure validator's samples bucket exists
    await ensure_bucket_available(validator_client, AUDIO_SAMPLES_BUCKET)
    emit_log(
        f"Sample generation loop starting (validator_id={VALIDATOR_ID}, slot every {SAMPLE_SLOT_INTERVAL_BLOCKS} blocks at offset {SAMPLE_SLOT_OFFSET_BLOCKS})",
        "start",
    )
    emit_log(f"Using API for valid miners: {API_URL}", "info")

    global _last_executed_slot_block
    round_num = 0
    while True:
        # Wait until we're in our block slot window (target ± SAMPLE_SLOT_BLOCK_TOLERANCE); execute at most once per slot
        while True:
            try:
                block = await get_current_block()
            except asyncio.TimeoutError:
                emit_log("get_current_block timed out (subtensor connection?), retrying in 12s", "warn")
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            except Exception as e:
                emit_log(f"get_current_block failed: {e}, retrying in 12s", "warn")
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            k = (block - SAMPLE_SLOT_OFFSET_BLOCKS) // SAMPLE_SLOT_INTERVAL_BLOCKS
            slot_block = SAMPLE_SLOT_OFFSET_BLOCKS + k * SAMPLE_SLOT_INTERVAL_BLOCKS
            in_window = (slot_block - SAMPLE_SLOT_BLOCK_TOLERANCE <= block <= slot_block + SAMPLE_SLOT_BLOCK_TOLERANCE)
            if in_window and _last_executed_slot_block != slot_block:
                break
            if not in_window:
                remaining = (SAMPLE_SLOT_OFFSET_BLOCKS - block % SAMPLE_SLOT_INTERVAL_BLOCKS) % SAMPLE_SLOT_INTERVAL_BLOCKS
                if remaining == 0:
                    remaining = SAMPLE_SLOT_INTERVAL_BLOCKS
                wait_s = remaining * SECONDS_PER_BLOCK
                emit_log(f"Block {block}: waiting for sample slot window (target {slot_block} ±{SAMPLE_SLOT_BLOCK_TOLERANCE}), ~{int(wait_s)}s", "info")
            await asyncio.sleep(SECONDS_PER_BLOCK)

        _last_executed_slot_block = slot_block
        round_num += 1
        round_start = time.time()
        try:
            live_notified = False
            print_header(f"Sample Generation Round #{round_num}")
            emit_log(f"Current block: {block}", "info")

            # 1. Get valid participants from centralized API
            try:
                valid_participants = await get_valid_participants_from_api()
            except Exception as e:
                emit_log(f"Failed to get participants from API: {e}", "error")
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue

            if not valid_participants:
                emit_log("No valid participants found", "warn")
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            
            emit_log(f"Found {len(valid_participants)} valid participants from API", "info")
            
            # Convert to dict for audio generation
            participants: Dict[str, Dict[str, Any]] = {
                p.hotkey: {
                    "chute_id": p.chute_id,
                    "model_name": p.model_name,
                    "model_revision": p.model_revision,
                    "slug": p.chute_slug,
                    "block": p.block,
                    "model_hash": p.model_hash,
                }
                for p in valid_participants
            }
            
            # 2. Download random audio from corpus bucket (owner's bucket, corpus credentials)
            audio_key = await select_random_audio(corpus_client)
            if not audio_key:
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            
            emit_log(f"Selected audio: {audio_key}", "info")
            
            evaluation_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            audio_path = f"/tmp/source_audio_{evaluation_id}.wav"
            
            await asyncio.to_thread(
                corpus_client.fget_object, AUDIO_SOURCE_BUCKET, audio_key, audio_path
            )
            emit_log(f"Downloaded audio: {os.path.getsize(audio_path):,} bytes", "info")
            
            # 3. Check duration min/max (validator expects 20–25s source audio); use full file as task
            duration = await get_audio_duration(audio_path)
            if duration < AUDIO_SOURCE_MIN_DURATION_SEC:
                emit_log(f"Audio too short ({duration:.1f}s < {AUDIO_SOURCE_MIN_DURATION_SEC}s), skipping", "warn")
                os.remove(audio_path)
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            if duration > AUDIO_SOURCE_MAX_DURATION_SEC:
                emit_log(f"Audio too long ({duration:.1f}s > {AUDIO_SOURCE_MAX_DURATION_SEC}s), skipping", "warn")
                os.remove(audio_path)
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            
            # 4. Extract transcription + voice traits from the source audio (task spec for this round)
            source_traits = await get_transcription_and_traits_async(openai_client, audio_path)
            description = format_task_prompt_for_tts(source_traits)
            prompt_preview = description[:200] + ("..." if len(description) > 200 else "")
            emit_log(f"Generated prompt: {prompt_preview}", "info")

            # 4b. Notify owner API that evaluation started (dashboard live status bar)
            try:
                from vocence.adapters.api import create_service_client_from_wallet
                client = create_service_client_from_wallet(
                    wallet_name=COLDKEY_NAME,
                    hotkey_name=HOTKEY_NAME,
                    api_url=API_URL,
                )
                try:
                    await client.submit_live_evaluation_started(
                        evaluation_id=evaluation_id,
                        prompt_summary=description[:512] if description else None,
                        miner_hotkeys=list(participants.keys()),
                    )
                    live_notified = True
                    emit_log(f"Live evaluation notified: eval_id={evaluation_id}, miners={len(participants)}", "info")
                finally:
                    await client.close()
            except Exception as e:
                emit_log(f"Live evaluation notify failed (continuing): {e}", "warn")

            # 5. Generate audio for each participant via their chute endpoint
            async with aiohttp.ClientSession() as session:
                emit_log(f"Generating audio for {len(participants)} participants...", "info")
                participant_audio = await synthesize_audio_for_participants(
                    session, participants, description
                )
            
            # Filter to participants with successful generation
            successful_participants = {
                hk: info for hk, info in participants.items()
                if participant_audio.get(hk, (None, None, None))[0] is not None
            }
            
            if not successful_participants:
                emit_log("No participants generated audio, skipping evaluation", "warn")
                if live_notified:
                    await _cancel_live_evaluation_safe(evaluation_id)
                os.remove(audio_path)
                await asyncio.sleep(SECONDS_PER_BLOCK)
                continue
            
            emit_log(f"{len(successful_participants)}/{len(participants)} participants generated audio", "info")
            
            # 6. Write participant audio files, then score each (original vs generated) concurrently via OpenAI
            participant_results: Dict[str, Dict[str, Any]] = {}
            files_to_upload = {"original.wav": audio_path}
            eval_semaphore = asyncio.Semaphore(MAX_PARALLEL_EVALS)

            async def evaluate_one(
                hotkey: str,
                participant_info: Dict[str, Any],
                participant_audio_path: str,
                audio_filename: str,
                endpoint: str | None,
            ) -> tuple[str, Dict[str, Any], str, str, str | None, Dict[str, Any]]:
                async with eval_semaphore:
                    comparison = await score_miner_against_spec_async(
                        openai_client,
                        participant_audio_path,
                        source_traits,
                        source_audio_path=audio_path,
                        task_description=description,
                    )
                    return hotkey, participant_info, participant_audio_path, audio_filename, endpoint, comparison

            # Write all participant files first
            eval_tasks = []
            for hotkey, participant_info in successful_participants.items():
                hotkey_short = hotkey[:8]
                audio_bytes, _, endpoint = participant_audio[hotkey]
                participant_audio_path = f"/tmp/participant_{hotkey_short}_{evaluation_id}.wav"
                with open(participant_audio_path, "wb") as f:
                    f.write(audio_bytes)
                audio_filename = f"participant_{hotkey_short}.wav"
                files_to_upload[audio_filename] = participant_audio_path
                eval_tasks.append(
                    evaluate_one(hotkey, participant_info, participant_audio_path, audio_filename, endpoint)
                )

            eval_results = await asyncio.gather(*eval_tasks, return_exceptions=True)

            for result in eval_results:
                if isinstance(result, Exception):
                    emit_log(f"Evaluation error: {result}", "warn")
                    continue
                hotkey, participant_info, participant_audio_path, audio_filename, endpoint, comparison = result
                hotkey_short = hotkey[:8]
                participant_results[hotkey] = {
                    "hotkey": hotkey,
                    "chute_id": participant_info["chute_id"],
                    "endpoint": endpoint,
                    "audio_filename": audio_filename,
                    "evaluation": {
                        "score": comparison["score"],
                        "generated_wins": comparison["generated_wins"],
                        "confidence": comparison["confidence"],
                        "reasoning": comparison["reasoning"],
                        "breakdown": comparison["breakdown"],
                        "extracted_traits": comparison["extracted_traits"],
                        "naturalness": comparison.get("naturalness"),
                        "original_artifacts": comparison["original_artifacts"],
                        "generated_artifacts": comparison["generated_artifacts"],
                    },
                }
                result_str = "PASS" if comparison["generated_wins"] else "FAIL"
                reasoning_short = (comparison.get("reasoning") or "")[:80].replace("\n", " ")
                emit_log(f"Participant {hotkey_short}: {result_str} score={comparison['score']:.3f}",
                    "success" if comparison["generated_wins"] else "info")
                emit_log(f"Eval: {result_str} | score={comparison['score']:.3f} | {reasoning_short}", "info")

                breakdown = comparison.get("breakdown") or {}
                script_info = breakdown.get("script")
                if script_info is not None:
                    script_wer = max(0.0, 1.0 - float(script_info.get("score", 0.0)))
                    emit_log(f"  [{hotkey_short}] script: WER={script_wer:.3f} (score={script_info.get('score', 0.0):.2f})", "info")
                trait_parts = []
                for key in ("gender", "pitch", "speed", "age_group", "emotion", "tone", "accent"):
                    info = breakdown.get(key)
                    if info is None:
                        continue
                    trait_parts.append(f"{key}: {info.get('expected')}→{info.get('actual')} ({info.get('score', 0.0):.2f})")
                if trait_parts:
                    emit_log(f"  [{hotkey_short}] traits: {' | '.join(trait_parts)}", "info")
                nat_info = breakdown.get("naturalness")
                if nat_info is not None:
                    emit_log(f"  [{hotkey_short}] naturalness: {nat_info.get('actual')} ({nat_info.get('score', 0.0):.2f})", "info")
            
            # 7. Build metadata (source traits are the task spec miners were scored against)
            from vocence.pipeline.evaluation import ELEMENT_WEIGHTS
            metadata = {
                "evaluation_id": evaluation_id,
                "created_at": datetime.now().isoformat(),
                "source": {
                    "bucket": AUDIO_SOURCE_BUCKET,
                    "key": audio_key,
                    "full_duration_seconds": duration,
                },
                "prompt": {
                    "model": GPT_AUDIO_MODEL,
                    "text": description,
                    "spec": source_traits,
                    "element_weights": ELEMENT_WEIGHTS,
                },
                "participants": participant_results,
                "files": list(files_to_upload.keys()) + ["metadata.json"],
            }
            
            # 7b. Save metadata.json locally before upload (if VALIDATOR_LOCAL_SAMPLES_DIR set)
            _save_sample_locally(evaluation_id, metadata)
            
            # 8. Upload sample to validator's Hippius bucket
            emit_log("Uploading evaluation to Hippius...", "info")
            await upload_sample_data(validator_client, evaluation_id, files_to_upload, metadata)
            
            # 8b. Generate pre-signed URLs for original and generated audio (for dashboard playback)
            try:
                original_obj = f"{evaluation_id}/original.wav"
                original_url = await asyncio.to_thread(
                    validator_client.presigned_get_object,
                    AUDIO_SAMPLES_BUCKET,
                    original_obj,
                    expires=timedelta(days=7),
                )
                for hotkey, pr in participant_results.items():
                    pr["original_audio_url"] = original_url
                    gen_obj = f"{evaluation_id}/{pr['audio_filename']}"
                    gen_url = await asyncio.to_thread(
                        validator_client.presigned_get_object,
                        AUDIO_SAMPLES_BUCKET,
                        gen_obj,
                        expires=timedelta(days=7),
                    )
                    pr["generated_audio_url"] = gen_url
            except Exception as e:
                emit_log(f"Presigned URLs failed (continuing without): {e}", "warn")
            
            # 9. Save to database (if configured), or clear pending when no results
            if participant_results:
                await submit_sample_metadata(evaluation_id, metadata, participant_results)
            else:
                if live_notified:
                    await _cancel_live_evaluation_safe(evaluation_id)
            
            # Summary
            round_duration = time.time() - round_start
            n = len(participant_results)
            passes = sum(1 for p in participant_results.values() if p["evaluation"]["generated_wins"])
            mean_score = (
                sum(float(p["evaluation"].get("score", 0.0)) for p in participant_results.values()) / n
                if n else 0.0
            )
            emit_log(
                f"Round #{round_num} complete: {passes}/{n} pass, mean_score={mean_score:.3f}, {round_duration:.1f}s total",
                "success",
            )
            emit_log(f"Evaluation {evaluation_id} uploaded to s3://{AUDIO_SAMPLES_BUCKET}/{evaluation_id}/", "success")
            
        except Exception as e:
            emit_log(f"Sample generation error: {e}", "error")
            import traceback
            traceback.print_exc()
            if live_notified:
                await _cancel_live_evaluation_safe(evaluation_id)
        finally:
            # Cleanup temp files (always runs, even on exception)
            if 'audio_path' in dir() and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            if 'files_to_upload' in dir():
                for filename, path in files_to_upload.items():
                    if filename.startswith("participant_") and os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
        
        # Leave current block slot so next loop iteration waits for the next slot (~150 blocks later)
        await asyncio.sleep(SECONDS_PER_BLOCK)

