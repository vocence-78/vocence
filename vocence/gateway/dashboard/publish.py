"""Publish the dashboard JSON snapshot to Hippius.

Validators (or a lightweight aggregator) upload ``data/dashboard.json`` to a
public-read Hippius bucket; the static frontend fetches it. No dashboard server runs,
so the dashboard adds no centralized dependency to the subnet.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from minio import Minio

from vocence.adapters.storage import ensure_bucket_available
from vocence.shared.logging import emit_log

DASHBOARD_OBJECT_KEY = "data/dashboard.json"


async def publish_dashboard(
    client: Minio,
    bucket: str,
    data: Dict[str, Any],
    *,
    object_key: str = DASHBOARD_OBJECT_KEY,
) -> str:
    """Serialize ``data`` and upload it. Returns the object key written."""
    await ensure_bucket_available(client, bucket)
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    tmp = Path(tempfile.mkdtemp()) / "dashboard.json"
    tmp.write_bytes(payload)
    try:
        await asyncio.to_thread(
            client.fput_object, bucket, object_key, str(tmp),
            content_type="application/json",
        )
    finally:
        tmp.unlink(missing_ok=True)
    emit_log(f"Published dashboard ({len(payload)} bytes) to {bucket}/{object_key}", "success")
    return object_key
