"""Tests for the judge adapters (with injected fake models — no GPU needed)."""

import pytest

from vocence.domain.spec import load_spec
from vocence.pipeline.judges.trait_questions import build_trait_questions, aggregate_ternary
from vocence.pipeline.judges import (
    WhisperIntelligibilityJudge,
    AdherenceChecklistJudge,
    SpeechJudgeNaturalness,
)

SPEC = load_spec()


# ---------------------------------------------------------------- trait questions
def test_trait_questions_deterministic_and_ordered():
    traits = {"emotion": "calm", "gender": "female"}
    q1 = build_trait_questions(traits, 50)
    q2 = build_trait_questions({"gender": "female", "emotion": "calm"}, 50)  # different order
    assert q1 == q2  # order of traits dict must not matter
    assert q1[0]["id"] == "q_01"
    # gender comes before emotion in TRAIT_ORDER
    assert q1[0]["trait"] == "gender"
    assert any(q["trait"] == "emotion" and "calm" in q["text"] for q in q1)
    assert any(q["trait"] == "consistency" for q in q1)


def test_trait_questions_capped():
    traits = {t: "x" for t in ("gender", "age", "tone", "emotion", "pace", "accent", "environment")}
    qs = build_trait_questions(traits, 5)
    assert len(qs) == 5


def test_aggregate_ternary():
    assert aggregate_ternary([1.0, 0.0, 0.5]) == 0.5
    assert aggregate_ternary([]) == 0.0
    assert aggregate_ternary([1.0, 1.0]) == 1.0


# ---------------------------------------------------------------- whisper gate
def test_whisper_gate_scores_and_gates():
    # fake transcriber returns fixed text
    judge = WhisperIntelligibilityJudge(SPEC, transcriber=lambda a: "the quick brown fox")
    score, wer, ok = judge.score_side("the quick brown fox", b"audio")
    assert wer == 0.0 and score == 1.0 and ok is True

    bad = WhisperIntelligibilityJudge(SPEC, transcriber=lambda a: "completely different words here")
    score, wer, ok = bad.score_side("the quick brown fox", b"audio")
    assert wer > SPEC.intelligibility_max_wer and ok is False


# ---------------------------------------------------------------- adherence
def test_adherence_pairwise_uses_same_questions():
    seen = {}

    def answerer(audio, questions):
        seen[audio] = [q["id"] for q in questions]
        # challenger (b"c") answers all yes; king (b"k") answers all partial
        return [1.0] * len(questions) if audio == b"c" else [0.5] * len(questions)

    judge = AdherenceChecklistJudge(SPEC, answerer=answerer)
    pair = judge.score_pair({"emotion": "angry", "gender": "male"}, b"k", b"c")
    assert pair.challenger == 1.0 and pair.king == 0.5
    assert seen[b"k"] == seen[b"c"]  # identical question set applied to both sides


# ---------------------------------------------------------------- naturalness
def test_speechjudge_pairwise():
    # comparator always prefers the challenger (2nd arg better)
    judge = SpeechJudgeNaturalness(SPEC, comparator=lambda t, k, c: 1.0)
    pair = judge.score_pair("hello", b"king", b"chal")
    assert pair.challenger == 1.0 and pair.king == 0.0


def test_speechjudge_voting_cancels_positional_bias():
    # a positionally-biased judge that always prefers the SECOND audio it is shown
    def biased(text, a, b):
        return 1.0  # always "second is better"

    judge = SpeechJudgeNaturalness(SPEC, comparator=biased, votes=2)
    pair = judge.score_pair("hello", b"king", b"chal")
    # vote0: prefers chal (1.0); vote1: order swapped -> 1 - 1.0 = 0.0 ; mean 0.5 -> tie
    assert pair.king == 0.5 and pair.challenger == 0.5


def test_speechjudge_rejects_zero_votes():
    with pytest.raises(ValueError):
        SpeechJudgeNaturalness(SPEC, comparator=lambda t, k, c: 1.0, votes=0)
