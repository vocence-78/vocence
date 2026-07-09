"""Content-addressed model storage on Hippius.

Miners upload a model directory here and commit its digest on chain (``v7`` reveal);
validators download by digest and verify. The digest is a deterministic hash of a
manifest of every file's SHA-256, so the same directory yields the same digest on the
miner and on every validator after download — that is what makes the on-chain
commitment immutable and the submission content-addressed.

Digest functions are pure (filesystem only) and unit-testable. Upload/download use the
Minio client from :mod:`vocence.adapters.storage`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from minio import Minio

from vocence.adapters.storage import ensure_bucket_available
from vocence.shared.logging import emit_log

MANIFEST_NAME = "vocence_manifest.json"
_CHUNK = 1 << 20  # 1 MiB
_UPLOAD_MAX_RETRIES = 3


async def _fput_with_retry(client: Minio, bucket: str, object_name: str, local_path: str) -> None:
    """Upload one object to an explicit bucket with retry/backoff."""
    last_error: Optional[Exception] = None
    for attempt in range(_UPLOAD_MAX_RETRIES):
        try:
            await asyncio.to_thread(client.fput_object, bucket, object_name, local_path)
            return
        except Exception as exc:
            last_error = exc
            if attempt < _UPLOAD_MAX_RETRIES - 1:
                emit_log(f"model upload failed for {object_name}, retrying... ({exc})", "warn")
                await asyncio.sleep(2 ** attempt)
    raise last_error or RuntimeError(f"model upload failed: {object_name}")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(local_dir: str | Path) -> Dict[str, str]:
    """Map each file's repo-relative POSIX path to its SHA-256 hex. The manifest file
    itself is excluded so a re-upload is idempotent."""
    root = Path(local_dir)
    manifest: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != MANIFEST_NAME:
            rel = path.relative_to(root).as_posix()
            manifest[rel] = _sha256_file(path)
    return manifest


def compute_dir_digest(local_dir: str | Path) -> str:
    """Deterministic ``sha256:<hex>`` content digest of a model directory."""
    manifest = build_manifest(local_dir)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def file_sha256(local_dir: str | Path, relpath: str) -> Optional[str]:
    """SHA-256 hex of one file in the dir (e.g. the canonical ``miner.py``), or None."""
    path = Path(local_dir) / relpath
    return _sha256_file(path) if path.is_file() else None


def _object_prefix(repo: str) -> str:
    return f"models/{repo.strip('/')}"


async def upload_model(client: Minio, bucket: str, repo: str, local_dir: str | Path) -> str:
    """Upload every file under ``local_dir`` to ``bucket`` under the repo prefix, write
    the manifest, and return the content digest the miner should commit on chain."""
    root = Path(local_dir)
    digest = compute_dir_digest(root)
    manifest = build_manifest(root)

    await ensure_bucket_available(client, bucket)
    prefix = _object_prefix(repo)
    for rel in manifest:  # sorted keys
        await _fput_with_retry(client, bucket, f"{prefix}/{rel}", str(root / rel))

    # Write the manifest (with the digest) alongside the files.
    payload = json.dumps({"digest": digest, "files": manifest}, sort_keys=True).encode("utf-8")
    tmp = root / MANIFEST_NAME
    tmp.write_bytes(payload)
    try:
        await _fput_with_retry(client, bucket, f"{prefix}/{MANIFEST_NAME}", str(tmp))
    finally:
        tmp.unlink(missing_ok=True)

    emit_log(f"Uploaded model {repo} ({len(manifest)} files) digest={digest}", "success")
    return digest


def list_model_files(client: Minio, bucket: str, repo: str) -> List[str]:
    """Repo-relative file paths present under the model prefix (excludes the manifest)."""
    prefix = _object_prefix(repo) + "/"
    names = []
    for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
        rel = obj.object_name[len(prefix):]
        if rel and rel != MANIFEST_NAME:
            names.append(rel)
    return sorted(names)


async def download_model(
    client: Minio,
    bucket: str,
    repo: str,
    dest_dir: str | Path,
    *,
    expected_digest: Optional[str] = None,
) -> Path:
    """Download the model to ``dest_dir`` and verify its recomputed digest.

    Raises ValueError if ``expected_digest`` is given and does not match — a validator
    must never evaluate content that differs from what the miner committed on chain.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    prefix = _object_prefix(repo) + "/"

    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    for obj in objects:
        rel = obj.object_name[len(prefix):]
        if not rel or rel == MANIFEST_NAME:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(client.fget_object, bucket, obj.object_name, str(target))

    actual = compute_dir_digest(dest)
    if expected_digest and actual != expected_digest.strip().lower():
        raise ValueError(
            f"digest mismatch for {repo}: committed {expected_digest}, downloaded {actual}"
        )
    return dest
