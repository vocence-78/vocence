"""Tests for genesis-king seeding of an empty reign."""

import pytest

from vocence.domain.spec import load_spec
from vocence.engine.genesis import genesis_reign, genesis_digest
from vocence.engine.koth_cycle import Candidate
from vocence.engine.koth_coordinator import Judges, run_cycle
from vocence.pipeline.duel import CorpusSample
from vocence.pipeline.judges import (
    WhisperIntelligibilityJudge, AdherenceChecklistJudge, SpeechJudgeNaturalness,
)

SPEC = load_spec()
pytestmark = pytest.mark.asyncio


def test_genesis_reign_holds_base_model():
    reign = genesis_reign(SPEC, owner_uid=0, owner_hotkey="5Owner")
    assert len(reign) == 1
    assert reign[0].slot == 1
    assert reign[0].repo == SPEC.seed_repo
    assert reign[0].digest == genesis_digest(SPEC)
    assert genesis_digest(SPEC).startswith("sha256:")


class FakeChain:
    def __init__(self, cands):
        self._c = cands
        self.set_calls = []
    async def current_block(self): return 1
    async def resolve_reign(self): return []      # empty on-chain reign
    async def list_candidates(self): return self._c
    async def set_weights(self, u, w): self.set_calls.append((u, w)); return True


def _judges(chal_better):
    return Judges(
        WhisperIntelligibilityJudge(SPEC, transcriber=lambda a: "the quick brown fox"),
        AdherenceChecklistJudge(SPEC, answerer=lambda a, qs: [1.0 if a.startswith(b"C") else 0.5] * len(qs)),
        SpeechJudgeNaturalness(SPEC, comparator=lambda t, k, c: 1.0 if chal_better else 0.0),
    )


async def _run(chal_better):
    chain = FakeChain([Candidate(uid=9, hotkey="n", repo="ns/vocence-prompttts-x", digest="sha256:"+"c"*64, model_hash="mc", block=5)])
    async def validate(c): return True, ""
    async def make_gen(repo, digest):
        return (lambda t, tr: b"K-audio") if repo == SPEC.seed_repo else (lambda t, tr: b"C-audio")
    return await run_cycle(
        chain=chain, validate=validate, make_generator=make_gen, judges=_judges(chal_better),
        corpus=[CorpusSample(f"s{i}", "the quick brown fox", {"gender": "female"}) for i in range(6)],
        spec=SPEC, genesis_reign=genesis_reign(SPEC, owner_uid=0, owner_hotkey="5Owner"),
    )


async def test_challenger_must_beat_base_to_crown():
    # challenger loses the duel vs the genesis base -> NOT crowned (no free win on empty hill)
    report = await _run(chal_better=False)
    assert report.duel is not None            # a real duel happened (not auto-crown)
    assert report.coronated is False


async def test_challenger_beats_base_and_crowns():
    report = await _run(chal_better=True)
    assert report.coronated is True
    assert report.weights_uids[0] == 9
