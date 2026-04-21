"""
Score calculation service for Vocence.

Reads samples from the Hippius S3 bucket and calculates win rates per miner.
Uses only the most recent N evaluations (by evaluation_id) when max_evals is set.

Note: In normal operation, scores are calculated server-side from submitted
samples and stored in the rank_scores table.
"""

import json
import asyncio
from typing import Any, Dict, Optional, Set

from minio import Minio

from vocence.domain.config import AUDIO_SAMPLES_BUCKET
from vocence.pipeline.evaluation import PASS_THRESHOLD
from vocence.shared.logging import emit_log


def _evaluation_id_from_path(object_name: str) -> Optional[str]:
    """Extract evaluation_id from path like '2026-02-18_01-22-56/metadata.json'."""
    if not object_name.endswith("/metadata.json"):
        return None
    return object_name.rsplit("/", 1)[0].strip("/") or None


async def calculate_scores_from_storage(
    storage_client: Minio,
    bucket_name: Optional[str] = None,
    max_evals: Optional[int] = None,
    valid_hotkeys: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Read samples from the Hippius bucket and calculate win rates per miner.
    If max_evals is set, only the most recent max_evals evaluations are used
    (by evaluation_id, which is the directory name and chronological).
    If valid_hotkeys is set, only those miners are included in the returned scores.
    
    Args:
        storage_client: Minio client for Hippius S3
        max_evals: If set, use only the most recent this many evaluations; else use all.
        valid_hotkeys: If set, only include these hotkeys in returned scores (e.g. valid miners).
    
    Returns:
        Dict mapping hotkey to {"wins": int, "total": int, "score_sum": float,
        "win_rate": float, "slug": str}.
        A miner "wins" an eval when its continuous score >= PASS_THRESHOLD;
        `win_rate = wins / total` is the binary win rate aggregated across evals
        and is the ranking signal. `score_sum` is kept for diagnostics/dashboards.
    """
    scores: Dict[str, Dict[str, Any]] = {}
    
    try:
        target_bucket = bucket_name or AUDIO_SAMPLES_BUCKET

        # List all objects in samples bucket
        objects = await asyncio.to_thread(
            lambda: list(storage_client.list_objects(target_bucket, recursive=True))
        )
        
        # Find all metadata.json files and map to evaluation_id
        metadata_objects = [obj for obj in objects if obj.object_name.endswith("metadata.json")]
        # evaluation_id is the directory name (e.g. 2026-02-18_01-22-56); sort descending = most recent first
        with_id = [(obj, _evaluation_id_from_path(obj.object_name)) for obj in metadata_objects]
        with_id = [(o, eid) for o, eid in with_id if eid]
        with_id.sort(key=lambda x: x[1], reverse=True)
        
        if max_evals is not None and max_evals > 0:
            with_id = with_id[:max_evals]
        
        metadata_files = [obj for obj, _ in with_id]
        total_in_bucket = len(metadata_objects)
        emit_log(
            f"Using {len(metadata_files)} evaluations for scoring"
            + (f" (most recent of {total_in_bucket})" if max_evals and total_in_bucket > max_evals else ""),
            "info",
        )
        
        for obj in metadata_files:
            try:
                # Download and parse metadata
                response = await asyncio.to_thread(
                    storage_client.get_object, target_bucket, obj.object_name
                )
                try:
                    metadata_bytes = response.read()
                finally:
                    response.close()
                    response.release_conn()
                
                metadata = json.loads(metadata_bytes.decode("utf-8"))
                
                # Process each miner's result (metadata uses "participants" key)
                miners_data = metadata.get("participants", {}) or metadata.get("miners", {})
                for hotkey, miner_info in miners_data.items():
                    if valid_hotkeys is not None and hotkey not in valid_hotkeys:
                        continue
                    if hotkey not in scores:
                        scores[hotkey] = {
                            "wins": 0,
                            "total": 0,
                            "score_sum": 0.0,
                            "slug": miner_info.get("slug", "unknown"),
                        }

                    evaluation = miner_info.get("evaluation", {}) or {}
                    raw_score = evaluation.get("score")
                    if raw_score is None:
                        # Back-compat: old metadata only has binary generated_wins.
                        eval_score = 1.0 if evaluation.get("generated_wins", False) else 0.0
                    else:
                        try:
                            eval_score = max(0.0, min(1.0, float(raw_score)))
                        except (TypeError, ValueError):
                            eval_score = 0.0

                    scores[hotkey]["total"] += 1
                    scores[hotkey]["score_sum"] += eval_score
                    if eval_score >= PASS_THRESHOLD:
                        scores[hotkey]["wins"] += 1

                    # Update slug if we have a newer one
                    if miner_info.get("slug"):
                        scores[hotkey]["slug"] = miner_info["slug"]
                        
            except Exception as e:
                emit_log(f"Error reading sample {obj.object_name}: {e}", "warn")
                continue
        
        # win_rate is binary: wins / total, where a win = eval score >= PASS_THRESHOLD.
        # mean_score is the mean continuous score; kept for diagnostics only.
        for hotkey, data in scores.items():
            if data["total"] > 0:
                data["win_rate"] = data["wins"] / data["total"]
                data["mean_score"] = data["score_sum"] / data["total"]
            else:
                data["win_rate"] = 0.0
                data["mean_score"] = 0.0
        
    except Exception as e:
        emit_log(f"Error calculating scores from S3: {e}", "error")
    
    return scores


async def calculate_scores_from_samples(
    storage_client: Minio,
    bucket_name: Optional[str] = None,
    max_evals: Optional[int] = None,
    valid_hotkeys: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Calculate win rates per miner from S3 samples.
    Uses only the most recent max_evals evaluations when set; only includes
    valid_hotkeys in the result when set (e.g. valid miners from owner API).
    
    Args:
        storage_client: Minio client for Hippius S3
        max_evals: If set, use only the most recent this many evaluations.
        valid_hotkeys: If set, only include these hotkeys in returned scores.
    
    Returns:
        Dict mapping hotkey to {"wins": int, "total": int, "win_rate": float, "slug": str}
    """
    return await calculate_scores_from_storage(
        storage_client,
        bucket_name=bucket_name,
        max_evals=max_evals,
        valid_hotkeys=valid_hotkeys,
    )
