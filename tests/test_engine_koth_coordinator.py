"""Integration-style tests for the KOTH coordinator cycle (all deps faked)."""

import pytest

from vocence.domain.spec import load_spec
from vocence.ranking.koth import ReignMember
from vocence.pipeline.duel import CorpusSample
from vocence.pipeline.judges import (
    WhisperIntelligibilityJudge, AdherenceChecklistJudge, SpeechJudgeNaturalness,
)
from vocence.engine.koth_cycle import Candidate
from vocence.engine.koth_coordinator import Judges, run_cycle

SPEC = load_spec()
pytestmark = pytest.mark.asyncio


class FakeChain:
    def __init__(self, reign, candidates):
        self._reign = reign
        self._candidates = candidates
        self.set_calls = []

    async def current_block(self):
        return 12345

    async def resolve_reign(self):
        return self._reign

    async def list_candidates(self):
        return self._candidates

    async def set_weights(self, uids, weights):
        self.set_calls.append((uids, weights))
        return True


def _judges(chal_better=True):
    ii = WhisperIntelligibilityJudge(SPEC, transcriber=lambda a: "the quick brown fox")
    adh = AdherenceChecklistJudge(
        SPEC, answerer=lambda audio, qs: [1.0 if audio.startswith(b"C") else 0.5] * len(qs)
    )
    nat = SpeechJudgeNaturalness(SPEC, comparator=lambda t, k, c: 1.0 if chal_better else 0.0)
    return Judges(ii, adh, nat)


def _corpus(n=6):
    return [CorpusSample(f"s{i}", "the quick brown fox", {"gender": "female"}) for i in range(n)]


async def _gen_factory_for(repo, digest):
    # king repo starts "king", challenger "chal"; encode into audio prefix
    prefix = b"K" if "king" in repo else b"C"
    async def factory(r, d):
        return lambda text, traits: prefix + b"-audio"
    return factory


async def test_genesis_crowns_first_valid_challenger():
    chain = FakeChain(reign=[], candidates=[
        Candidate(uid=7, hotkey="hk7", repo="ns/vocence-prompttts-chal", digest="d", model_hash="m7", block=10),
    ])

    async def validate(c):
        return True, ""

    async def make_gen(repo, digest):
        return lambda text, traits: b"C-audio"

    report = await run_cycle(
        chain=chain, validate=validate, make_generator=make_gen,
        judges=_judges(), corpus=_corpus(), spec=SPEC,
    )
    assert report.coronated is True
    assert report.weights_uids[0] == 7
    assert chain.set_calls  # weights were set


async def test_no_challenger_keeps_reign():
    reign = [ReignMember(uid=1, hotkey="k", model_hash="m1", slot=1, repo="ns/king", digest="d")]
    # only candidate is the current king itself -> filtered out
    chain = FakeChain(reign=reign, candidates=[
        Candidate(uid=1, hotkey="k", repo="ns/king", digest="d", model_hash="m1"),
    ])

    async def validate(c):
        return True, ""

    async def make_gen(repo, digest):
        return lambda t, tr: b"x"

    report = await run_cycle(
        chain=chain, validate=validate, make_generator=make_gen,
        judges=_judges(), corpus=_corpus(), spec=SPEC,
    )
    assert report.coronated is False
    assert report.note == "no_challenger"
    assert report.weights_uids == [1]


async def test_challenger_beats_king_and_crowns():
    reign = [ReignMember(uid=1, hotkey="king", model_hash="mk", slot=1, repo="ns/king", digest="dk")]
    chain = FakeChain(reign=reign, candidates=[
        Candidate(uid=9, hotkey="new", repo="ns/chal", digest="dc", model_hash="mc", block=5),
    ])

    async def validate(c):
        return True, ""

    async def make_gen(repo, digest):
        return (lambda t, tr: b"K-audio") if "king" in repo else (lambda t, tr: b"C-audio")

    report = await run_cycle(
        chain=chain, validate=validate, make_generator=make_gen,
        judges=_judges(chal_better=True), corpus=_corpus(), spec=SPEC,
    )
    assert report.duel is not None and report.duel.challenger_won is True
    assert report.coronated is True
    assert report.weights_uids[0] == 9  # challenger to slot 1


async def test_invalid_challenger_is_skipped():
    reign = [ReignMember(uid=1, hotkey="king", model_hash="mk", slot=1, repo="ns/king", digest="dk")]
    chain = FakeChain(reign=reign, candidates=[
        Candidate(uid=9, hotkey="new", repo="ns/chal", digest="dc", model_hash="mc", block=5),
    ])

    async def validate(c):
        return False, "architecture"

    async def make_gen(repo, digest):
        return lambda t, tr: b"x"

    report = await run_cycle(
        chain=chain, validate=validate, make_generator=make_gen,
        judges=_judges(), corpus=_corpus(), spec=SPEC,
    )
    assert report.coronated is False
    assert report.note.startswith("invalid_challenger")
    assert report.weights_uids == [1]
