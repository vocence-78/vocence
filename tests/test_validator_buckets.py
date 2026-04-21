"""Tests for validator bucket config loading."""

import json
import importlib

import pytest

import vocence.validator_buckets as validator_buckets_module


def _reload_module_with_env(monkeypatch, value: str):
    monkeypatch.setenv("VALIDATOR_BUCKETS_JSON", value)
    return importlib.reload(validator_buckets_module)


def test_load_validator_bucket_configs_from_list(monkeypatch):
    module = _reload_module_with_env(
        monkeypatch,
        json.dumps(
            [
                {
                    "hotkey": "5A",
                    "bucket_name": "bucket-a",
                    "access_key": "access-a",
                    "secret_key": "secret-a",
                },
                {
                    "hotkey": "5B",
                    "bucket_name": "bucket-b",
                    "access_key": "access-b",
                    "secret_key": "secret-b",
                },
            ]
        ),
    )

    configs = module.load_validator_bucket_configs()
    assert [cfg.hotkey for cfg in configs] == ["5A", "5B"]
    assert configs[0].bucket_name == "bucket-a"


def test_load_validator_bucket_configs_from_wrapped_object(monkeypatch):
    module = _reload_module_with_env(
        monkeypatch,
        json.dumps(
            {
                "validators": [
                    {
                        "hotkey": "5A",
                        "bucket_name": "bucket-a",
                        "access_key": "access-a",
                        "secret_key": "secret-a",
                    }
                ]
            }
        ),
    )

    configs = module.load_validator_bucket_configs()
    assert len(configs) == 1
    assert configs[0].hotkey == "5A"


def test_load_validator_bucket_configs_rejects_duplicate_hotkeys(monkeypatch):
    module = _reload_module_with_env(
        monkeypatch,
        json.dumps(
            [
                {
                    "hotkey": "5A",
                    "bucket_name": "bucket-a",
                    "access_key": "access-a",
                    "secret_key": "secret-a",
                },
                {
                    "hotkey": "5A",
                    "bucket_name": "bucket-b",
                    "access_key": "access-b",
                    "secret_key": "secret-b",
                },
            ]
        ),
    )

    with pytest.raises(ValueError, match="duplicate validator hotkey"):
        module.load_validator_bucket_configs()


def test_load_validator_bucket_configs_requires_env(monkeypatch):
    module = _reload_module_with_env(monkeypatch, "")
    with pytest.raises(ValueError, match="VALIDATOR_BUCKETS_JSON"):
        module.load_validator_bucket_configs()
