"""Tests for v7 reveal commitment parsing/formatting."""

import pytest

from vocence.adapters.chain import format_reveal, parse_reveal, immutable_ref

GOOD_DIGEST = "sha256:" + "a" * 64
REPO = "ns/vocence-prompttts-v1"


def test_format_reveal_roundtrip():
    reveal = format_reveal(REPO, GOOD_DIGEST)
    assert reveal == f"v7|{REPO}|{GOOD_DIGEST}"
    parsed = parse_reveal(reveal)
    assert parsed == {"version": "v7", "repo": REPO, "digest": GOOD_DIGEST}


def test_format_uppercases_digest_normalized():
    reveal = format_reveal(REPO, "SHA256:" + "A" * 64)
    assert parse_reveal(reveal)["digest"] == "sha256:" + "a" * 64


@pytest.mark.parametrize("bad", ["", "not-a-digest", "sha256:xyz", "sha1:" + "a" * 40])
def test_format_rejects_bad_digest(bad):
    with pytest.raises(ValueError):
        format_reveal(REPO, bad)


def test_format_rejects_pipe_in_repo():
    with pytest.raises(ValueError):
        format_reveal("ns|evil", GOOD_DIGEST)


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "v6|ns/repo|" + GOOD_DIGEST,          # wrong version
        "v7|ns/repo",                          # too few parts
        "v7|ns/repo|badhash",                  # bad digest
        '{"model_name":"x"}',                  # legacy JSON, not a reveal
    ],
)
def test_parse_reveal_rejects(value):
    assert parse_reveal(value) == {}


def test_immutable_ref():
    assert immutable_ref(REPO, GOOD_DIGEST) == f"{REPO}@{GOOD_DIGEST}"
