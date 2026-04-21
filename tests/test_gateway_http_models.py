"""Tests for vocence.gateway.http.service.models."""
from vocence.gateway.http.service.models.requests import (
    ParticipantResponse,
    EvaluationSubmission,
    ServiceStatusResponse,
    BlocklistEntry,
)

def test_participant_response_model():
    r = ParticipantResponse(uid=0, hotkey="0x1", is_valid=True)
    assert r.hotkey == "0x1"

def test_service_status_response():
    r = ServiceStatusResponse(status="ok", version="0.1.0", database=True, metagraph_synced=False, last_sync=None)
    assert r.status == "ok"

def test_blocklist_entry():
    e = BlocklistEntry(hotkey="0x2", reason="test")
    assert e.hotkey == "0x2"

def test_evaluation_submission():
    s = EvaluationSubmission(evaluation_id="e1", participant_hotkey="0x3", s3_bucket="b", s3_prefix="p", wins=True)
    assert s.wins is True
