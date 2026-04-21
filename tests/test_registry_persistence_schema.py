"""Tests for vocence.registry.persistence.schema."""
from vocence.registry.persistence.schema import (
    RegisteredMiner,
    ValidatorEvaluation,
    BlockedEntity,
    ValidatorRegistry,
)

def test_registered_miner_importable():
    assert RegisteredMiner is not None

def test_validator_evaluation_importable():
    assert ValidatorEvaluation is not None

def test_blocked_entity_importable():
    assert BlockedEntity is not None

def test_validator_registry_importable():
    assert ValidatorRegistry is not None
