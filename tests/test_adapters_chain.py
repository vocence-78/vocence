"""Tests for vocence.adapters.chain."""

import json
import pytest

from vocence.adapters.chain import parse_commitment, validate_commitment_fields


class TestParseCommitment:
    """Tests for parse_commitment."""

    def test_parse_valid_json(self, sample_commitment_dict):
        s = json.dumps(sample_commitment_dict)
        assert parse_commitment(s) == sample_commitment_dict

    def test_parse_empty_string_returns_empty_dict(self):
        assert parse_commitment("") == {}
        assert parse_commitment(None) == {}

    def test_parse_invalid_json_returns_empty_dict(self):
        assert parse_commitment("not json") == {}
        assert parse_commitment("{") == {}

    def test_parse_non_dict_json_returns_empty_dict(self):
        assert parse_commitment("[1,2,3]") == {}
        assert parse_commitment('"string"') == {}


class TestValidateCommitmentFields:
    """Tests for validate_commitment_fields."""

    def test_valid_commitment(self, sample_commitment_dict):
        valid, err = validate_commitment_fields(sample_commitment_dict)
        assert valid is True
        assert err is None

    def test_missing_model_name(self, sample_commitment_dict):
        d = {**sample_commitment_dict, "model_name": ""}
        valid, err = validate_commitment_fields(d)
        assert valid is False
        assert err == "missing_model_name"

    def test_missing_model_revision(self, sample_commitment_dict):
        d = {**sample_commitment_dict, "model_revision": ""}
        valid, err = validate_commitment_fields(d)
        assert valid is False
        assert err == "missing_model_revision"

    def test_missing_chute_id(self, sample_commitment_dict):
        d = {**sample_commitment_dict, "chute_id": ""}
        valid, err = validate_commitment_fields(d)
        assert valid is False
        assert err == "missing_chute_id"

    def test_empty_dict(self):
        valid, err = validate_commitment_fields({})
        assert valid is False
        assert err in ("missing_model_name", "missing_model_revision", "missing_chute_id")
