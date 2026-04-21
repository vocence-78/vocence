"""
SQLAlchemy ORM schema for Vocence database.

Defines the database schema using SQLAlchemy 2.0 declarative style.

Centralized Service Architecture Models:
- RegisteredMiner: Centrally validated miners (synced from metagraph)
- ValidatorEvaluation: Evaluations submitted by validators
- BlockedEntity: Blocked entities
- ValidatorRegistry: Registered validators
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    Text,
    DateTime,
    Index,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)
from sqlalchemy.sql import func


class BaseModel(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class RegisteredMiner(BaseModel):
    """Centrally validated miners.
    
    Stores miner validation state synced from metagraph with HuggingFace
    and Chutes endpoint verification.
    """
    __tablename__ = "registered_miners"
    
    uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    miner_hotkey: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    block: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Model info (from commitment)
    model_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    model_revision: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # Chutes info
    chute_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    chute_slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Validation state
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    invalid_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    
    __table_args__ = (
        Index("idx_registered_miners_is_valid", "is_valid"),
        Index("idx_registered_miners_hotkey", "miner_hotkey"),
    )
    
    def __repr__(self) -> str:
        return f"<RegisteredMiner(uid={self.uid}, hotkey='{self.miner_hotkey[:8]}...', is_valid={self.is_valid})>"


class ValidatorEvaluation(BaseModel):
    """Evaluation submitted by a validator.
    
    Stores evaluation results from validators. Metrics are calculated
    from this table by the metrics aggregation task.
    """
    __tablename__ = "validator_evaluations"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_hotkey: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    miner_hotkey: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # Evaluation info
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_prefix: Mapped[str] = mapped_column(String(512), nullable=False)
    
    # Evaluation result
    wins: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Continuous weighted score in [0, 1]; `wins` = (score >= PASS_THRESHOLD).
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # JSON-encoded dict of per-element raw scores, e.g. {"script": 0.89, "naturalness": 1.0, ...}.
    element_scores: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pre-signed audio URLs (validator sends; used by dashboard for playback)
    original_audio_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_audio_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamp
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    
    __table_args__ = (
        Index("idx_validator_evaluations_validator", "validator_hotkey"),
        Index("idx_validator_evaluations_miner", "miner_hotkey"),
        Index("idx_validator_evaluations_eval", "evaluation_id"),
        Index("idx_validator_evaluations_date", "evaluated_at"),
        Index("idx_validator_evaluations_unique", "validator_hotkey", "evaluation_id", "miner_hotkey", unique=True),
    )
    
    def __repr__(self) -> str:
        return f"<ValidatorEvaluation(validator='{self.validator_hotkey[:8]}...', eval='{self.evaluation_id}', wins={self.wins})>"


class BlockedEntity(BaseModel):
    """Blocked miner hotkeys.
    
    Centralized blacklist managed by subnet admins.
    """
    __tablename__ = "blocked_entities"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hotkey: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    added_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # admin hotkey
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    
    def __repr__(self) -> str:
        return f"<BlockedEntity(hotkey='{self.hotkey[:8]}...')>"


class ValidatorRegistry(BaseModel):
    """Registered validators.
    
    Tracks validators that submit evaluations to the service.
    """
    __tablename__ = "validator_registry"
    
    uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotkey: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    stake: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    
    # Optional S3 bucket for validator's samples
    s3_bucket: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Activity tracking
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    
    def __repr__(self) -> str:
        return f"<ValidatorRegistry(uid={self.uid}, hotkey='{self.hotkey[:8]}...', stake={self.stake})>"


class LiveEvaluationPending(BaseModel):
    """Live evaluation "started" notice for dashboard status bar.

    Validators POST to /evaluations/live after generating the prompt (before/during
    miner evaluation). When POST /evaluations (batch) is received for the same
    (validator_hotkey, evaluation_id), the row is removed so the bar shows win/lose.
    """
    __tablename__ = "live_evaluation_pending"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_hotkey: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_summary: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    miner_hotkeys: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array of hotkeys
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_live_eval_pending_validator", "validator_hotkey"),
        Index("idx_live_eval_pending_eval", "evaluation_id"),
        Index("idx_live_eval_pending_unique", "validator_hotkey", "evaluation_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<LiveEvaluationPending(validator='{self.validator_hotkey[:8]}...', eval='{self.evaluation_id}')>"


class GlobalScoringSnapshot(BaseModel):
    """Persisted global scoring snapshot for dashboard rendering."""

    __tablename__ = "global_scoring_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    winner_hotkey: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    snapshot_data: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_global_scoring_snapshots_latest", "is_latest"),
        Index("idx_global_scoring_snapshots_generated_at", "generated_at"),
    )

    def __repr__(self) -> str:
        winner = (self.winner_hotkey or "")[:8]
        return f"<GlobalScoringSnapshot(id={self.id}, latest={self.is_latest}, winner='{winner}...')>"


class GraphActivityLease(BaseModel):
    """Short-lived graph activity lease for live subnet visualization."""

    __tablename__ = "graph_activity_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    activity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    validator_hotkey: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_graph_activity_leases_status", "status"),
        Index("idx_graph_activity_leases_expires_at", "expires_at"),
        Index("idx_graph_activity_leases_validator", "validator_hotkey"),
    )

    def __repr__(self) -> str:
        return (
            f"<GraphActivityLease(type='{self.activity_type}', validator='{self.validator_hotkey[:8]}...', "
            f"status='{self.status}')>"
        )
