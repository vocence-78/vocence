"""Tests for content-addressed model digests (pure filesystem logic)."""

import hashlib

from vocence.adapters.model_store import (
    build_manifest, compute_dir_digest, file_sha256, MANIFEST_NAME,
)


def _write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content if isinstance(content, bytes) else content.encode())


def test_manifest_and_digest_stable(tmp_path):
    _write(tmp_path, "config.json", '{"a":1}')
    _write(tmp_path, "model.safetensors", b"\x00\x01\x02")
    _write(tmp_path, "speech_tokenizer/config.json", "{}")

    m = build_manifest(tmp_path)
    assert set(m) == {"config.json", "model.safetensors", "speech_tokenizer/config.json"}
    assert m["config.json"] == hashlib.sha256(b'{"a":1}').hexdigest()

    d1 = compute_dir_digest(tmp_path)
    d2 = compute_dir_digest(tmp_path)
    assert d1 == d2 and d1.startswith("sha256:")


def test_digest_changes_on_content_change(tmp_path):
    _write(tmp_path, "config.json", '{"a":1}')
    before = compute_dir_digest(tmp_path)
    _write(tmp_path, "config.json", '{"a":2}')
    assert compute_dir_digest(tmp_path) != before


def test_manifest_excludes_manifest_file(tmp_path):
    _write(tmp_path, "config.json", "{}")
    _write(tmp_path, MANIFEST_NAME, "{}")
    assert MANIFEST_NAME not in build_manifest(tmp_path)


def test_file_sha256(tmp_path):
    _write(tmp_path, "miner.py", "print('hi')")
    assert file_sha256(tmp_path, "miner.py") == hashlib.sha256(b"print('hi')").hexdigest()
    assert file_sha256(tmp_path, "missing.py") is None
