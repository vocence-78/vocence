"""Tests for the king-caching duel runner."""

from vocence.domain.spec import load_spec
from vocence.pipeline.duel import CorpusSample
from vocence.pipeline.duel_runner import DuelRunner
from vocence.pipeline.judges import (
    WhisperIntelligibilityJudge, AdherenceChecklistJudge, SpeechJudgeNaturalness,
)

SPEC = load_spec()


def _runner(counter):
    def transcribe(a):
        return "the quick brown fox"

    def answerer(audio, qs):
        counter["adh"] += 1
        return [1.0 if audio.startswith(b"C") else 0.6] * len(qs)

    return DuelRunner(
        intelligibility=WhisperIntelligibilityJudge(SPEC, transcriber=transcribe),
        adherence=AdherenceChecklistJudge(SPEC, answerer=answerer),
        naturalness=SpeechJudgeNaturalness(SPEC, comparator=lambda t, k, c: 1.0),
        spec=SPEC,
    )


def _corpus(n=5):
    return [CorpusSample(f"s{i}", "the quick brown fox", {"gender": "female"}) for i in range(n)]


def test_king_side_cached_across_duels():
    counter = {"adh": 0}
    runner = _runner(counter)
    corpus = _corpus(5)
    king_gen = lambda t, tr: b"K-audio"

    r1 = runner.run(corpus, king_gen, "sha256:king", lambda t, tr: b"C1-audio")
    r2 = runner.run(corpus, king_gen, "sha256:king", lambda t, tr: b"C2-audio")

    assert r1.state == "succeeded" and r2.state == "succeeded"
    assert r1.challenger_won is True
    # king adherence answered once per sample (5); each duel adds 5 challenger calls.
    # Without caching it'd be 5 (king) * 2 duels + 10 challenger = 20; with caching: 5 + 10 = 15.
    assert counter["adh"] == 15
    assert runner.cache_hits == 5   # 2nd duel reused all 5 king samples
    assert runner.cache_misses == 5


def test_evict_king_clears_cache():
    runner = _runner({"adh": 0})
    runner.run(_corpus(3), lambda t, tr: b"K", "sha256:oldking", lambda t, tr: b"C")
    assert runner.evict_king("sha256:oldking") == 3
    assert not runner.king_cache


def test_runner_handles_generation_error():
    runner = _runner({"adh": 0})
    errs = []

    def bad_chal(t, tr):
        raise RuntimeError("boom")

    res = runner.run(_corpus(3), lambda t, tr: b"K", "sha256:k", bad_chal,
                     on_error=lambda sid, e: errs.append(sid))
    assert res.state == "failed"
    assert len(errs) == 3
