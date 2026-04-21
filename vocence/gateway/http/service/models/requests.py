"""
Request and response models for Vocence Service API.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field


# Participant models
class ParticipantResponse(BaseModel):
    """Participant information response."""
    
    uid: int
    hotkey: str
    model_name: Optional[str] = None
    model_revision: Optional[str] = None
    model_hash: Optional[str] = None
    chute_id: Optional[str] = None
    chute_slug: Optional[str] = None
    is_valid: bool
    invalid_reason: Optional[str] = None
    block: Optional[int] = None
    last_validated_at: Optional[datetime] = None


class ParticipantsListResponse(BaseModel):
    """List of participants response."""
    
    participants: List[ParticipantResponse]
    total: int
    valid_count: int


class ActiveValidatorsResponse(BaseModel):
    """Active validators response."""

    validators: List[str]
    count: int
    threshold_hours: int


# Live evaluation started (dashboard status bar: "pending")
class LiveEvaluationStartedRequest(BaseModel):
    """Request body for POST /evaluations/live — validator notifies that evaluation has started."""

    evaluation_id: str
    prompt_summary: Optional[str] = None
    miner_hotkeys: List[str] = Field(default_factory=list)


class LiveEvaluationCancelRequest(BaseModel):
    """Request body for POST /evaluations/live/cancel — validator clears pending when no results will be submitted."""

    evaluation_id: str


class WeightSettingStartedRequest(BaseModel):
    """Request body for POST /graph/weights/start."""

    cycle_block: int
    target_validator_hotkeys: List[str] = Field(default_factory=list)
    phase: str = "starting"


class WeightSettingFinishedRequest(BaseModel):
    """Request body for POST /graph/weights/end."""

    cycle_block: int
    result: str = "success"
    winner_hotkey: Optional[str] = None


# Evaluation models
class EvaluationSubmission(BaseModel):
    """Evaluation submission request."""

    evaluation_id: str
    participant_hotkey: str
    s3_bucket: str
    s3_prefix: str
    wins: bool
    prompt: Optional[str] = None
    confidence: Optional[int] = Field(None, ge=0, le=100)
    reasoning: Optional[str] = None
    original_audio_url: Optional[str] = None
    generated_audio_url: Optional[str] = None
    score: Optional[float] = Field(None, ge=0.0, le=1.0)
    element_scores: Optional[Dict[str, float]] = None


class EvaluationResponse(BaseModel):
    """Evaluation response."""

    id: int
    evaluation_id: str
    participant_hotkey: str
    prompt: Optional[str] = None
    s3_bucket: str
    s3_prefix: str
    wins: bool
    confidence: Optional[int] = None
    reasoning: Optional[str] = None
    original_audio_url: Optional[str] = None
    generated_audio_url: Optional[str] = None
    score: Optional[float] = None
    element_scores: Optional[Dict[str, float]] = None
    evaluated_at: datetime


# Blocklist models
class BlocklistEntry(BaseModel):
    """Blocklist entry request."""
    
    hotkey: str
    reason: Optional[str] = None


class BlocklistResponse(BaseModel):
    """Blocklist entry response."""
    
    hotkey: str
    reason: Optional[str] = None
    added_by: Optional[str] = None
    created_at: datetime


# Status models
class ServiceStatusResponse(BaseModel):
    """Service status response."""
    
    status: str
    version: str
    database: bool
    metagraph_synced: bool
    last_sync: Optional[datetime] = None
