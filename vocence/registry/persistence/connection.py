"""
Database connection management for Vocence.

Provides async connection pool management using SQLAlchemy with asyncpg.
"""

from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)

from vocence.shared.logging import emit_log
from vocence.domain.config import (
    DB_CONNECTION_STRING,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    POSTGRES_DB,
    DATABASE_ECHO,
)


# Global engine and session factory
_db_engine: Optional[AsyncEngine] = None
_db_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


def build_connection_string() -> str:
    """Build database connection URL from config (env with defaults).
    
    Supports both DB_CONNECTION_STRING and individual components.
    
    Returns:
        PostgreSQL connection URL with asyncpg driver
    """
    if DB_CONNECTION_STRING:
        db_url = DB_CONNECTION_STRING
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return db_url
    return f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


async def establish_connection(connection_string: Optional[str] = None) -> AsyncEngine:
    """Initialize async database connection pool.
    
    Creates a singleton engine instance with connection pooling.
    
    Args:
        connection_string: Optional database URL override
        
    Returns:
        SQLAlchemy async engine
    """
    global _db_engine, _db_session_maker
    
    if _db_engine is not None:
        return _db_engine
    
    if connection_string is None:
        connection_string = build_connection_string()
    
    emit_log(f"Establishing database connection: {connection_string.split('@')[-1]}", "info")
    
    _db_engine = create_async_engine(
        connection_string,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        pool_pre_ping=True,
        echo=DATABASE_ECHO,
    )
    
    _db_session_maker = async_sessionmaker(
        _db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    emit_log("Database connection pool established", "success")
    return _db_engine


async def terminate_connection() -> None:
    """Close database connection pool."""
    global _db_engine, _db_session_maker
    
    if _db_engine is not None:
        await _db_engine.dispose()
        _db_engine = None
        _db_session_maker = None
        emit_log("Database connection pool terminated", "info")


def get_connection_engine() -> AsyncEngine:
    """Get current database engine.
    
    Returns:
        SQLAlchemy async engine
        
    Raises:
        RuntimeError: If database not initialized
    """
    if _db_engine is None:
        raise RuntimeError("Database not initialized. Call establish_connection() first.")
    return _db_engine


@asynccontextmanager
async def acquire_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session with automatic cleanup.
    
    Usage:
        async with acquire_session() as session:
            result = await session.execute(query)
    
    Yields:
        AsyncSession for database operations
        
    Raises:
        RuntimeError: If database not initialized
    """
    if _db_session_maker is None:
        raise RuntimeError("Database not initialized. Call establish_connection() first.")
    
    session = _db_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def ensure_evaluation_audio_columns() -> None:
    """Add original_audio_url and generated_audio_url to validator_evaluations if missing.
    Safe for existing DBs created before these columns were added."""
    from sqlalchemy import text
    engine = get_connection_engine()
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE validator_evaluations ADD COLUMN IF NOT EXISTS original_audio_url TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE validator_evaluations ADD COLUMN IF NOT EXISTS generated_audio_url TEXT"
        ))
    emit_log("Evaluation audio URL columns ensured", "info")


async def ensure_evaluation_score_columns() -> None:
    """Add continuous score + per-element score columns to validator_evaluations if missing.
    Safe for existing DBs created before these columns were added."""
    from sqlalchemy import text
    engine = get_connection_engine()
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE validator_evaluations ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION"
        ))
        await conn.execute(text(
            "ALTER TABLE validator_evaluations ADD COLUMN IF NOT EXISTS element_scores TEXT"
        ))
    emit_log("Evaluation score columns ensured", "info")


async def initialize_schema() -> None:
    """Create all database tables.
    
    Uses SQLAlchemy metadata to create tables if they don't exist.
    For production, use Alembic migrations instead.
    """
    from vocence.registry.persistence.schema import BaseModel
    
    engine = get_connection_engine()
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    
    await ensure_evaluation_audio_columns()
    await ensure_evaluation_score_columns()
    emit_log("Database schema initialized", "success")


async def drop_schema() -> None:
    """Drop all database tables.
    
    WARNING: This will delete all data. Use with caution.
    """
    from vocence.registry.persistence.schema import BaseModel
    
    engine = get_connection_engine()
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.drop_all)
    
    emit_log("Database schema dropped", "warn")

