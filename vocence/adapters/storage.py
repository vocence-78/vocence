"""
Hippius S3 storage utilities for Vocence.

- Validator's samples bucket: use create_validator_storage_client().
- Reading peer validator buckets: use create_custom_storage_client(access, secret).
"""

import os
import json
import asyncio
from typing import Any, Dict

from minio import Minio

from vocence.domain.config import (
    AUDIO_SAMPLES_BUCKET,
    HIPPIUS_VALIDATOR_ACCESS_KEY,
    HIPPIUS_VALIDATOR_SECRET_KEY,
)
from vocence.shared.logging import emit_log

HIPPIUS_ENDPOINT = "s3.hippius.com"


def _minio_client(access_key: str, secret_key: str) -> Minio:
    """Build Minio client for Hippius."""
    return Minio(
        HIPPIUS_ENDPOINT,
        access_key=access_key or "",
        secret_key=secret_key or "",
        secure=True,
        region="decentralized",
    )


def create_custom_storage_client(access_key: str, secret_key: str) -> Minio:
    """Create a Minio client from explicit credentials."""
    return _minio_client(access_key, secret_key)


def create_validator_storage_client() -> Minio:
    """Create a Minio client with validator's own credentials (samples bucket, etc.).

    Use on validator for uploading samples and any validator-owned storage.
    Env: HIPPIUS_VALIDATOR_ACCESS_KEY, HIPPIUS_VALIDATOR_SECRET_KEY (or legacy HIPPIUS_ACCESS_KEY, HIPPIUS_SECRET_KEY).
    """
    return _minio_client(HIPPIUS_VALIDATOR_ACCESS_KEY, HIPPIUS_VALIDATOR_SECRET_KEY)


async def ensure_bucket_available(storage_client: Minio, bucket_name: str) -> None:
    """Create bucket if it doesn't exist.
    
    Args:
        storage_client: The Minio client instance
        bucket_name: Name of the bucket to ensure exists
    """
    exists = await asyncio.to_thread(storage_client.bucket_exists, bucket_name)
    if not exists:
        await asyncio.to_thread(storage_client.make_bucket, bucket_name)
        emit_log(f"Created bucket: {bucket_name}", "success")


S3_UPLOAD_MAX_RETRIES = 3


async def _fput_object_with_retry(
    storage_client: Minio, object_name: str, local_path: str
) -> None:
    """Upload one object, retrying transient failures with exponential backoff.

    A finished sample has already cost real compute (miner queries + GPT scoring),
    so we don't want a momentary S3 hiccup to discard it. Raises the last error if
    all attempts fail (caller's round-level handler logs and moves on).
    """
    last_error: Exception | None = None
    for attempt in range(S3_UPLOAD_MAX_RETRIES):
        try:
            await asyncio.to_thread(
                storage_client.fput_object, AUDIO_SAMPLES_BUCKET, object_name, local_path
            )
            return
        except Exception as e:  # minio raises various S3Error/network types
            last_error = e
            if attempt < S3_UPLOAD_MAX_RETRIES - 1:
                emit_log(f"S3 upload failed for {object_name}, retrying... ({e})", "warn")
                await asyncio.sleep(2 ** attempt)
    raise last_error or Exception(f"S3 upload failed: {object_name}")


async def upload_sample_data(
    storage_client: Minio,
    sample_id: str,
    files: Dict[str, str],
    metadata: Dict[str, Any],
) -> str:
    """Upload a complete sample to the audio-samples bucket.

    Args:
        storage_client: The Minio client instance
        sample_id: Unique identifier for the sample
        files: Dict mapping filename to local file path
        metadata: Metadata dictionary to upload as JSON

    Returns:
        The sample prefix (same as sample_id)
    """
    prefix = sample_id

    # Upload each file
    for filename, local_path in files.items():
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            object_name = f"{prefix}/{filename}"
            await _fput_object_with_retry(storage_client, object_name, local_path)
            size_kb = os.path.getsize(local_path) / 1024
            emit_log(f"Uploaded: {object_name} ({size_kb:.1f} KB)", "info")

    # Upload metadata as JSON
    metadata_json = json.dumps(metadata, indent=2)
    metadata_path = f"/tmp/metadata_{sample_id}.json"
    with open(metadata_path, "w") as f:
        f.write(metadata_json)

    object_name = f"{prefix}/metadata.json"
    try:
        await _fput_object_with_retry(storage_client, object_name, metadata_path)
        emit_log(f"Uploaded: {object_name}", "info")
    finally:
        # Cleanup temp metadata file even if the upload ultimately failed.
        if os.path.exists(metadata_path):
            os.remove(metadata_path)

    return prefix
