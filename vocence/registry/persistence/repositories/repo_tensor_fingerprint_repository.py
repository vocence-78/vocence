"""
Repository for the repo_tensor_fingerprints table.

Tensor-level fingerprint of an HF repo at a pinned revision. One row per unique
(model_name, model_revision); rows are immutable once written (the underlying HF
commit content cannot change). Reads are O(1) primary-key lookups; the table is
small (a few KB per miner).
"""

import json
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from vocence.registry.persistence.connection import acquire_session
from vocence.registry.persistence.schema import RepoTensorFingerprint
from vocence.shared.logging import emit_log


class RepoTensorFingerprintRepository:
    """Read/write the repo_tensor_fingerprints table."""

    async def get(self, model_name: str, model_revision: str) -> Optional[Dict[str, str]]:
        """Return {tensor_name: sha256_hex} for a (model, revision), or None if absent."""
        try:
            async with acquire_session() as session:
                row = await session.get(RepoTensorFingerprint, (model_name, model_revision))
                if row is None:
                    return None
                try:
                    return json.loads(row.tensors) or {}
                except json.JSONDecodeError as e:
                    emit_log(f"corrupt tensor fingerprint row for {model_name}@{model_revision}: {e}", "warn")
                    return None
        except Exception as e:
            emit_log(f"tensor fingerprint get failed for {model_name}@{model_revision}: {e}", "warn")
            return None

    async def upsert(
        self,
        model_name: str,
        model_revision: str,
        total_bytes: int,
        tensors: Dict[str, str],
        commit_block: int = 0,
    ) -> None:
        """Insert or replace the fingerprint row for (model, revision)."""
        payload = json.dumps(tensors, sort_keys=True, separators=(",", ":"))
        try:
            async with acquire_session() as session:
                existing = await session.get(RepoTensorFingerprint, (model_name, model_revision))
                if existing is None:
                    session.add(RepoTensorFingerprint(
                        model_name=model_name,
                        model_revision=model_revision,
                        total_bytes=int(total_bytes),
                        tensor_count=len(tensors),
                        tensors=payload,
                        commit_block=commit_block,
                    ))
                else:
                    existing.total_bytes = int(total_bytes)
                    existing.tensor_count = len(tensors)
                    existing.tensors = payload
                    existing.commit_block = commit_block
        except Exception as e:
            emit_log(f"tensor fingerprint upsert failed for {model_name}@{model_revision}: {e}", "warn")

    async def get_many(self, keys: List[tuple[str, str]]) -> Dict[tuple[str, str], Dict[str, str]]:
        """Bulk-load fingerprints for a list of (model_name, revision) keys.

        Skips rows that are missing or unparseable. Returns {key: tensors_dict}.
        """
        if not keys:
            return {}
        out: Dict[tuple[str, str], Dict[str, str]] = {}
        try:
            async with acquire_session() as session:
                # SQLAlchemy lacks a tuple-IN for composite PKs portably; iterate.
                for k in keys:
                    row = await session.get(RepoTensorFingerprint, k)
                    if row is None:
                        continue
                    try:
                        out[k] = json.loads(row.tensors) or {}
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            emit_log(f"tensor fingerprint bulk get failed: {e}", "warn")
        return out

    async def find_collision(
        self,
        new_tensors: Dict[str, str],
        exclude_key: Tuple[str, str],
        threshold: float,
    ) -> Optional[Tuple[str, str, float, int]]:
        """Find the first stored fingerprint whose tensor match ratio against
        `new_tensors` is at or above `threshold`.

        Used as the audit-time block: a new (model, revision) whose tensors collide
        with anything already stored — regardless of which miner owns the existing
        row or whether that miner is still active — gets rejected unless the new
        commit has an earlier block (earlier block wins).

        Ratio is computed as `count(matching tensor hashes) / len(new_tensors)`. Rows
        keyed by `exclude_key` (the new commit itself, in case it's already stored)
        and rows with empty tensor dicts are skipped.

        Returns (matched_model_name, matched_revision, ratio, commit_block) on the
        first match, or None if no match.
        """
        if not new_tensors:
            return None
        async with acquire_session() as session:
            result = await session.execute(select(RepoTensorFingerprint))
            for row in result.scalars():
                key = (row.model_name, row.model_revision)
                if key == exclude_key:
                    continue
                try:
                    existing = json.loads(row.tensors) or {}
                except json.JSONDecodeError:
                    continue
                if not existing:
                    continue
                matching = sum(1 for k, v in new_tensors.items() if existing.get(k) == v)
                ratio = matching / len(new_tensors)
                if ratio >= threshold:
                    return (row.model_name, row.model_revision, ratio, row.commit_block or 0)
        return None

    async def delete(self, model_name: str, model_revision: str) -> None:
        """Remove a fingerprint row. Used when an earlier-block miner evicts an existing entry."""
        try:
            async with acquire_session() as session:
                row = await session.get(RepoTensorFingerprint, (model_name, model_revision))
                if row is not None:
                    await session.delete(row)
        except Exception as e:
            emit_log(f"tensor fingerprint delete failed for {model_name}@{model_revision}: {e}", "warn")
