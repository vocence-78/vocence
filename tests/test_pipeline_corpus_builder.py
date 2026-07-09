"""Tests for the deterministic eval-corpus generator."""

import pytest

from vocence.pipeline.corpus_builder import build_corpus, serialize_corpus, TRAITS
from vocence.pipeline.eval_corpus import parse_corpus, corpus_hash


def test_build_is_deterministic():
    a = serialize_corpus(build_corpus(128))
    b = serialize_corpus(build_corpus(128))
    assert a == b
    assert corpus_hash(a) == corpus_hash(b)


def test_build_count_and_shape():
    samples = build_corpus(128)
    assert len(samples) == 128
    assert samples[0]["sample_id"] == "vocence-00000"
    for s in samples:
        assert s["target_text"]
        assert set(s["traits"]) == set(TRAITS)
        for k, v in s["traits"].items():
            assert v in TRAITS[k]


def test_output_parses_as_corpus():
    raw = serialize_corpus(build_corpus(130))
    samples = parse_corpus(raw, expected_hash=corpus_hash(raw))
    assert len(samples) == 130
    assert samples[5].traits["gender"] in ("male", "female")


def test_covers_multiple_trait_values():
    samples = build_corpus(128)
    emotions = {s["traits"]["emotion"] for s in samples}
    genders = {s["traits"]["gender"] for s in samples}
    assert len(emotions) >= 4 and genders == {"male", "female"}


def test_rejects_zero():
    with pytest.raises(ValueError):
        build_corpus(0)
