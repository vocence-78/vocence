"""
Repository for the repo_tensor_fingerprints table.

Tensor-level fingerprint of an HF repo at a pinned revision. One row per unique
(model_name, model_revision); rows are immutable once written (the underlying HF
commit content cannot change). Reads are O(1) primary-key lookups; the table is
small (a few KB per miner).
"""

import json
from typing import Dict, List, Optional

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
                    ))
                else:
                    existing.total_bytes = int(total_bytes)
                    existing.tensor_count = len(tensors)
                    existing.tensors = payload
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
