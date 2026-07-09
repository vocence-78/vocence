"""Tests for the pure parsing/message-building of the HTTP judge backends."""

import json

from vocence.pipeline.judges.serving import (
    parse_ternary_answers, parse_pairwise, build_answer_messages, build_compare_messages, b64,
)


def test_parse_ternary_answers_maps_and_snaps():
    raw = json.dumps({"answers": [
        {"id": "q_01", "score": 1}, {"id": "q_02", "score": 0.5}, {"id": "q_03", "score": 0},
        {"id": "q_04", "score": 0.8},  # snaps to 1.0
    ]})
    out = parse_ternary_answers(raw, ["q_01", "q_02", "q_03", "q_04", "q_05"])
    assert out == [1.0, 0.5, 0.0, 1.0, 0.0]  # q_05 missing -> 0.0


def test_parse_ternary_answers_handles_prose_wrapping():
    raw = 'Sure! Here is the JSON:\n{"answers":[{"id":"q_01","score":1}]}\nThanks'
    assert parse_ternary_answers(raw, ["q_01"]) == [1.0]


def test_parse_ternary_answers_garbage():
    assert parse_ternary_answers("no json here", ["q_01"]) == [0.0]


def test_parse_pairwise():
    assert parse_pairwise('{"winner":"B"}') == 1.0        # challenger
    assert parse_pairwise('{"winner":"A"}') == 0.0        # king
    assert parse_pairwise('{"winner":"tie"}') == 0.5
    assert parse_pairwise("unparseable") == 0.5


def test_message_builders_include_audio_and_questions():
    msgs = build_answer_messages(b64(b"AUDIO"), [{"id": "q_01", "text": "Is it female?"}])
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert any(part.get("type") == "input_audio" for part in user)
    assert any("q_01" in json.dumps(part) for part in user)

    cmp_msgs = build_compare_messages(b64(b"K"), b64(b"C"), "hello world")
    audio_parts = [p for p in cmp_msgs[1]["content"] if p.get("type") == "input_audio"]
    assert len(audio_parts) == 2  # A (king) and B (challenger)
