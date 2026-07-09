"""Tests for reign resolution from chain state and the pinned eval corpus."""

import json
import pytest

from vocence.domain.spec import load_spec
from vocence.engine.reign import ChainEntry, resolve_reign, reign_from_chain, UidModel
from vocence.pipeline.eval_corpus import parse_corpus, corpus_hash

SPEC = load_spec()


def test_resolve_reign_orders_by_incentive_and_caps():
    entries = [ChainEntry(uid=i, hotkey=f"hk{i}", model_hash=f"m{i}", incentive=i / 10) for i in range(1, 8)]
    reign = resolve_reign(entries, SPEC)
    assert len(reign) == SPEC.court_size            # capped at 5
    assert [m.uid for m in reign] == [7, 6, 5, 4, 3]  # highest incentive -> slot 1
    assert [m.slot for m in reign] == [1, 2, 3, 4, 5]


def test_resolve_reign_drops_zero_incentive():
    entries = [ChainEntry(1, "a", "m1", 0.0), ChainEntry(2, "b", "m2", 0.5)]
    reign = resolve_reign(entries, SPEC)
    assert [m.uid for m in reign] == [2]


def test_reign_from_chain_joins_incentive_and_models():
    reign = reign_from_chain(
        incentive_by_uid={1: 0.9, 2: 0.1, 3: 0.0},
        model_by_uid={1: UidModel("hkA", "mA"), 2: UidModel("hkB", "mB")},
        spec=SPEC,
    )
    assert [m.uid for m in reign] == [1, 2]
    assert reign[0].hotkey == "hkA"


def test_parse_corpus_and_hash_verify():
    raw = json.dumps([
        {"sample_id": "s1", "target_text": "hello world", "traits": {"gender": "female"}},
        {"sample_id": "s2", "target_text": "another line", "traits": {"emotion": "calm"}},
    ]).encode()
    samples = parse_corpus(raw, expected_hash=corpus_hash(raw))
    assert len(samples) == 2
    assert samples[0].target_text == "hello world"
    assert samples[1].traits["emotion"] == "calm"


def test_parse_corpus_rejects_bad_hash():
    raw = json.dumps([{"sample_id": "s1", "target_text": "x"}]).encode()
    with pytest.raises(ValueError):
        parse_corpus(raw, expected_hash="sha256:" + "0" * 64)


def test_parse_corpus_rejects_empty_text_and_dupes():
    with pytest.raises(ValueError):
        parse_corpus(json.dumps([{"sample_id": "s1", "target_text": ""}]).encode())
    with pytest.raises(ValueError):
        parse_corpus(json.dumps([
            {"sample_id": "s1", "target_text": "a"},
            {"sample_id": "s1", "target_text": "b"},
        ]).encode())
