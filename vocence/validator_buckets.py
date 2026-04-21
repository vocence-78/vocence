"""Validator bucket configuration loader from environment JSON."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ValidatorBucketConfig:
    """Readonly access details for one validator's samples bucket."""

    hotkey: str
    bucket_name: str
    access_key: str
    secret_key: str


def _normalize_entries(raw: object) -> list[dict]:
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict):
        validators = raw.get("validators")
        if isinstance(validators, list):
            return [entry for entry in validators if isinstance(entry, dict)]
    raise ValueError("validator bucket config must be a list or an object with a 'validators' list")


def _validate_and_build(entries: Iterable[dict]) -> list[ValidatorBucketConfig]:
    configs: list[ValidatorBucketConfig] = []
    seen_hotkeys: set[str] = set()
    seen_buckets: set[str] = set()

    for idx, entry in enumerate(entries):
        hotkey = str(entry.get("hotkey", "")).strip()
        bucket_name = str(entry.get("bucket_name", "")).strip()
        access_key = str(entry.get("access_key", "")).strip()
        secret_key = str(entry.get("secret_key", "")).strip()

        if not hotkey or not bucket_name or not access_key or not secret_key:
            raise ValueError(
                f"validator bucket config entry {idx} must include hotkey, bucket_name, access_key, secret_key"
            )
        if hotkey in seen_hotkeys:
            raise ValueError(f"duplicate validator hotkey in bucket config: {hotkey}")
        if bucket_name in seen_buckets:
            raise ValueError(f"duplicate bucket_name in bucket config: {bucket_name}")

        seen_hotkeys.add(hotkey)
        seen_buckets.add(bucket_name)
        configs.append(
            ValidatorBucketConfig(
                hotkey=hotkey,
                bucket_name=bucket_name,
                access_key=access_key,
                secret_key=secret_key,
            )
        )

    return configs


def load_validator_bucket_configs() -> List[ValidatorBucketConfig]:
    """Load validator bucket configs from VALIDATOR_BUCKETS_JSON."""
    raw_json = os.environ.get("VALIDATOR_BUCKETS_JSON", "").strip()
    if not raw_json:
        raise ValueError("VALIDATOR_BUCKETS_JSON is empty or not set")

    raw = json.loads(raw_json)
    entries = _normalize_entries(raw)
    configs = _validate_and_build(entries)
    if not configs:
        raise ValueError("VALIDATOR_BUCKETS_JSON does not contain any validator entries")
    return configs
