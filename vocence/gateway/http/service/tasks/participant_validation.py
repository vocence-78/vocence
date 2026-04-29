"""
Participant validation background worker.

Periodically syncs metagraph and validates participants:
- Parses commitment data
- Verifies HuggingFace model exists
- Checks chute endpoint is responsive
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List

import aiohttp
import bittensor as bt

from vocence.shared.logging import emit_log, print_header
from vocence.domain.config import (
    SUBNET_ID,
    CHAIN_NETWORK,
    PARTICIPANT_VALIDATION_INTERVAL,
    OWNER_UID,
    OWNER_HOTKEY,
    BASE_MODEL_CHUTE_ID,
    BASE_MODEL_MODEL_NAME,
    BASE_MODEL_MODEL_REVISION,
    BASE_MODEL_COMMIT_BLOCK,
    COMMIT_LOCK_BLOCK,
    MAX_POST_CUTOVER_COMMITS,
)
from vocence.registry.persistence.repositories.miner_repository import MinerRepository
from vocence.registry.persistence.repositories.blocklist_repository import BlocklistRepository
from vocence.adapters.chain import parse_commitment, validate_commitment_fields
from vocence.domain.entities import ParticipantInfo
from vocence.registry.validation import validate_miner, detect_duplicates
from vocence.gateway.http.service.endpoints.status import record_last_sync


class ParticipantValidationTask:
    """Background worker for participant validation."""
    
    def __init__(self):
        self.participant_repo = MinerRepository()
        self.blocklist_repo = BlocklistRepository()
        self._running = False
    
    async def run(self) -> None:
        """Run the participant validation worker loop."""
        self._running = True
        emit_log(f"Participant validation worker starting (interval={PARTICIPANT_VALIDATION_INTERVAL}s)", "start")
        
        # Initial delay to let other services start
        await asyncio.sleep(5)
        
        while self._running:
            try:
                await self._validate_participants()
            except Exception as e:
                emit_log(f"Participant validation error: {e}, will retry in {PARTICIPANT_VALIDATION_INTERVAL}s", "error")
                import traceback
                traceback.print_exc()
            
            await asyncio.sleep(PARTICIPANT_VALIDATION_INTERVAL)
    
    def stop(self) -> None:
        """Stop the worker."""
        self._running = False
    
    async def _validate_participants(self) -> None:
        """Validate all participants from metagraph."""
        print_header("Participant Validation Sync")
        emit_log(
            "Owner checks per miner: chute_fetch, wrapper_integrity (deploy script hash vs canonical), chute_hot, revision_chute_match, model_fingerprint, revision_hf_match",
            "info",
        )

        # Connect to subtensor
        subtensor = bt.AsyncSubtensor(network=CHAIN_NETWORK)
        
        try:
            # Get current block and commitments
            current_block = await subtensor.get_current_block()
            commits = await subtensor.get_all_revealed_commitments(SUBNET_ID, block=current_block)
            
            if not commits:
                emit_log("No participant commitments found (will still inject owner base model if configured)", "warn")
            commits = commits or {}

            meta = await subtensor.metagraph(SUBNET_ID)
            
            # Get blocklist
            blocked_participants = await self.blocklist_repo.fetch_blocked_hotkeys()
            
            # Validate each participant
            validated_participants: List[Dict[str, Any]] = []
            participant_infos: List[ParticipantInfo] = []
            
            async with aiohttp.ClientSession() as session:
                for uid in range(len(meta.hotkeys)):
                    hotkey = meta.hotkeys[uid]
                    if hotkey in commits:
                        commit_data = commits[hotkey]
                    else:
                        continue

                    # Check blocklist
                    if hotkey in blocked_participants:
                        validated_participants.append({
                            "uid": uid,
                            "miner_hotkey": hotkey,
                            "is_valid": False,
                            "invalid_reason": "blocked",
                        })
                        continue
                    
                    # Enforce per-hotkey commit cap at/after COMMIT_LOCK_BLOCK (only field-valid commits consume a slot)
                    if COMMIT_LOCK_BLOCK > 0:
                        post_cutover = []
                        for b, v in commit_data:
                            if b < COMMIT_LOCK_BLOCK:
                                continue
                            if not validate_commitment_fields(parse_commitment(v))[0]:
                                continue
                            post_cutover.append((b, v))
                        if len(post_cutover) > MAX_POST_CUTOVER_COMMITS:
                            latest_block = post_cutover[-1][0]
                            validated_participants.append({
                                "uid": uid,
                                "miner_hotkey": hotkey,
                                "block": latest_block,
                                "is_valid": False,
                                "invalid_reason": (
                                    f"too_many_commits:{len(post_cutover)}_post_cutover_"
                                    f"max_{MAX_POST_CUTOVER_COMMITS}_after_block_{COMMIT_LOCK_BLOCK}"
                                ),
                            })
                            continue
                        commit_block, commit_value = post_cutover[-1] if post_cutover else commit_data[-1]
                    else:
                        commit_block, commit_value = commit_data[-1]
                    parsed = parse_commitment(commit_value)

                    # Validate commit fields
                    is_valid, reason = validate_commitment_fields(parsed)
                    if not is_valid:
                        validated_participants.append({
                            "uid": uid,
                            "miner_hotkey": hotkey,
                            "block": commit_block,
                            "is_valid": False,
                            "invalid_reason": reason,
                        })
                        continue
                    
                    # Full validation (HuggingFace, Chutes)
                    participant_info = await validate_miner(
                        session=session,
                        uid=uid,
                        hotkey=hotkey,
                        model_name=parsed["model_name"],
                        model_revision=parsed["model_revision"],
                        chute_id=parsed["chute_id"],
                        block=commit_block,
                    )

                    participant_infos.append(participant_info)

            # Inject owner as synthetic participant (owner never commits on chain; treated as committed at BASE_MODEL_COMMIT_BLOCK).
            # Data comes from config only; validate_miner marks them valid without running chute/HF checks.
            if not any(p.uid == OWNER_UID for p in participant_infos):
                owner_info = await validate_miner(
                    session=session,
                    uid=OWNER_UID,
                    hotkey=OWNER_HOTKEY,
                    model_name=BASE_MODEL_MODEL_NAME,
                    model_revision=BASE_MODEL_MODEL_REVISION,
                    chute_id=BASE_MODEL_CHUTE_ID,
                    block=BASE_MODEL_COMMIT_BLOCK,
                )
                participant_infos.append(owner_info)
                emit_log(f"Injected owner participant: uid={OWNER_UID}, hotkey={OWNER_HOTKEY[:12]}..., block={BASE_MODEL_COMMIT_BLOCK}", "info")

            # Apply duplicate detection on validated miners (same model_hash → only earliest block stays valid)
            if participant_infos:
                participant_infos = detect_duplicates(participant_infos)

            # Merge duplicate-filtered validated miners with earlier invalid/blocked entries
            for info in participant_infos:
                validated_participants.append({
                    "uid": info.uid,
                    "miner_hotkey": info.hotkey,
                    "block": info.block,
                    "model_name": info.model_name,
                    "model_revision": info.model_revision,
                    "model_hash": info.model_hash,
                    "chute_id": info.chute_id,
                    "chute_slug": info.chute_slug,
                    "is_valid": info.is_valid,
                    "invalid_reason": info.invalid_reason,
                })
            
            # Update database
            await self.participant_repo.bulk_upsert_miners(validated_participants)
            
            # Delete inactive participants
            active_uids = [p["uid"] for p in validated_participants]
            await self.participant_repo.remove_inactive_miners(active_uids)
            
            # Update health check
            record_last_sync(datetime.now(timezone.utc))
            
            # Log summary
            valid_count = sum(1 for p in validated_participants if p.get("is_valid"))
            invalid_count = len(validated_participants) - valid_count
            emit_log(f"Validated {len(validated_participants)} participants: {valid_count} valid, {invalid_count} invalid", "success")
            
        finally:
            await subtensor.close()

