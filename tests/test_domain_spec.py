"""Tests for the vocence.toml subnet spec loader."""

import pytest

from vocence.domain.spec import load_spec, SubnetSpec


def test_loads_repo_spec():
    spec = load_spec()  # discovers repo-root vocence.toml
    assert isinstance(spec, SubnetSpec)
    assert spec.name == "Vocence"
    assert spec.win_margin == 0.03
    assert spec.court_size == 5
    assert spec.seed_repo.endswith("qwen3-voicedesign-base")
    assert "miner.py" in spec.required_files
    assert spec.canonical_miner_py_sha256  # non-empty


def test_facet_weights_sum_to_one():
    spec = load_spec()
    assert abs(sum(spec.facet_weights.values()) - 1.0) < 1e-6
    assert spec.facet_weight("adherence") == 0.45
    assert spec.facet_weight("missing") == 0.0


def test_judges_are_open_models():
    spec = load_spec()
    assert set(spec.judges) == {"intelligibility", "adherence", "naturalness"}
    assert "whisper" in spec.judges["intelligibility"].lower()


def test_rejects_bad_margin(tmp_path):
    bad = tmp_path / "vocence.toml"
    bad.write_text(
        'name="x"\n'  # placeholder; real content below
    )
    bad.write_text(
        '[chain]\nname="X"\nnetuid=1\nseed_repo="a/b"\nrepo_pattern="^.+$"\n'
        "[arch]\nlock_keys=[]\n[seed]\n[files]\n[preeval]\n"
        "[incentive]\nwin_margin=1.5\ncourt_size=5\n[eval]\n"
    )
    with pytest.raises(ValueError):
        load_spec(bad)


def test_rejects_facet_weights_not_summing_to_one(tmp_path):
    bad = tmp_path / "vocence.toml"
    bad.write_text(
        '[chain]\nname="X"\nnetuid=1\nseed_repo="a/b"\nrepo_pattern="^.+$"\n'
        "[arch]\nlock_keys=[]\n[seed]\n[files]\n[preeval]\n"
        "[incentive]\nwin_margin=0.03\ncourt_size=5\n"
        "[eval]\n[eval.facet_weights]\nintelligibility=0.5\nadherence=0.4\nnaturalness=0.4\n"
    )
    with pytest.raises(ValueError):
        load_spec(bad)
