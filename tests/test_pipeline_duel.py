"""Tests for the duel orchestrator (fake generators + judges, no GPU)."""

from vocence.domain.spec import load_spec
from vocence.pipeline.duel import CorpusSample, run_duel
from vocence.pipeline.judges import (
    WhisperIntelligibilityJudge, AdherenceChecklistJudge, SpeechJudgeNaturalness,
)

SPEC = load_spec()


def _samples(n=10):
    return [CorpusSample(f"s{i}", "the quick brown fox", {"gender": "female", "emotion": "calm"})
            for i in range(n)]


def _judges(chal_better=True):
    # transcriber returns the target text for both -> perfect intelligibility
    intelligibility = WhisperIntelligibilityJudge(SPEC, transcriber=lambda a: "the quick brown fox")
    # adherence: challenger audio (b"C...") answers 1.0, king (b"K...") answers 0.6
    adherence = AdherenceChecklistJudge(
        SPEC, answerer=lambda audio, qs: [1.0 if audio.startswith(b"C") else 0.6] * len(qs)
    )
    naturalness = SpeechJudgeNaturalness(SPEC, comparator=lambda t, k, c: 1.0 if chal_better else 0.0)
    return intelligibility, adherence, naturalness


def test_run_duel_challenger_wins():
    ii, adh, nat = _judges(chal_better=True)
    res = run_duel(
        _samples(), king_generate=lambda t, tr: b"King-audio",
        challenger_generate=lambda t, tr: b"Chal-audio",
        intelligibility=ii, adherence=adh, naturalness=nat, spec=SPEC,
    )
    assert res.state == "succeeded"
    assert res.challenger_won is True
    assert res.scored_samples == 10


def test_run_duel_handles_generation_errors():
    ii, adh, nat = _judges()
    errors = []

    def bad_challenger(t, tr):
        raise RuntimeError("gen failed")

    res = run_duel(
        _samples(4), king_generate=lambda t, tr: b"King",
        challenger_generate=bad_challenger,
        intelligibility=ii, adherence=adh, naturalness=nat, spec=SPEC,
        on_error=lambda sid, e: errors.append(sid),
    )
    # all samples failed to score -> duel fails, no crash
    assert res.state == "failed"
    assert len(errors) == 4
