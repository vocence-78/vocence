"""
Pydantic schemas for Vocence data structures.

These schemas provide type safety and validation for data flowing through the system.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ChainCommitment(BaseModel):
    """A miner's blockchain commitment data."""
    
    hotkey: str
    model_name: str
    model_revision: str
    chute_id: str
    commit_block: int


class ParticipantInfo(BaseModel):
    """Validated participant information with status."""
    
    uid: int
    hotkey: str
    model_name: str = ""
    model_revision: str = ""
    chute_id: str = ""
    chute_slug: str = ""
    block: int = 0
    is_valid: bool = False
    invalid_reason: Optional[str] = None
    model_hash: str = ""
    chute_status: str = ""


class SourceAudioMetadata(BaseModel):
    """Metadata about a source audio file."""
    
    bucket: str
    key: str
    full_duration_seconds: float
    clip_start_seconds: float = 0.0  # optional when using full audio as task
    clip_duration_seconds: float = 0.0


class GeneratedPrompt(BaseModel):
    """Information about the generated prompt."""
    
    model: str = "gpt-4o"
    text: str


class AudioGenerationConfig(BaseModel):
    """Parameters used for audio generation."""
    
    sample_rate: int = 22050
    duration: float = 15.0
    format: str = "wav"
    fast: bool = True


class GenerationMetadata(BaseModel):
    """Information about the audio generation process."""
    
    model: str
    endpoint: str
    parameters: AudioGenerationConfig


class EvaluationOutcome(BaseModel):
    """Result of a GPT-4o forced-choice evaluation."""
    
    generated_wins: bool
    confidence: int = Field(ge=50, le=100)
    reasoning: str
    original_artifacts: List[str] = Field(default_factory=list)
    generated_artifacts: List[str] = Field(default_factory=list)
    presentation_order: str


class ParticipantResponse(BaseModel):
    """A participant's result for a single evaluation."""
    
    hotkey: str
    slug: str
    audio_filename: str
    evaluation: EvaluationOutcome


class EvaluationMetadata(BaseModel):
    """Complete metadata for an evaluation."""
    
    evaluation_id: str
    created_at: str
    source: SourceAudioMetadata
    prompt: GeneratedPrompt
    generation: GenerationMetadata
    participants: Dict[str, ParticipantResponse]
    files: List[str]


class ParticipantStats(BaseModel):
    """Aggregated statistics for a participant across all evaluations."""
    
    wins: int = 0
    total: int = 0
    win_rate: float = 0.0
    slug: str = "unknown"


class ComparisonResult(BaseModel):
    """Result from a forced-choice comparison."""
    
    original_won: bool
    generated_won: bool
    confidence: int = Field(ge=0, le=100)
    original_artifacts: List[str] = Field(default_factory=list)
    generated_artifacts: List[str] = Field(default_factory=list)
    reasoning: str
    presentation_order: str

