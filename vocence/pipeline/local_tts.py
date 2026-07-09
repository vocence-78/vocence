"""Validator-local TTS generation.

A validator downloads a submitted model and generates audio on its own GPU (no Chutes,
no team-granted key), then judges it. Because the fixed-architecture lock permits
exactly one Python file — the hash-locked canonical ``miner.py`` — generation loads and
calls that script. This function refuses to execute a ``miner.py`` whose hash does not
match the canonical one in ``vocence.toml``, so a validator never runs arbitrary miner
code.

Model/torch loading is lazy (GPU host only); the hash gate is pure and unit-tested.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable, Dict

from vocence.domain.spec import SubnetSpec
from vocence.adapters.model_store import file_sha256

# (target_text, traits) -> WAV bytes
GenerateFn = Callable[[str, Dict[str, object]], bytes]


class CanonicalScriptError(RuntimeError):
    """The model's miner.py is missing or does not match the canonical hash."""


def verify_canonical_script(model_dir: str | Path, spec: SubnetSpec) -> str:
    """Ensure ``miner.py`` matches the canonical hash. Returns its sha256, else raises."""
    name = spec.forbidden_py_except or "miner.py"
    sha = file_sha256(model_dir, name)
    if sha is None:
        raise CanonicalScriptError(f"{name} not found in {model_dir}")
    if spec.canonical_miner_py_sha256 and sha.lower() != spec.canonical_miner_py_sha256.lower():
        raise CanonicalScriptError(
            f"{name} sha256 {sha} != canonical {spec.canonical_miner_py_sha256}; refusing to execute"
        )
    return sha


def load_generator(model_dir: str | Path, spec: SubnetSpec) -> GenerateFn:  # pragma: no cover - GPU path
    """Build a generation fn from a downloaded model directory.

    Verifies the canonical script hash first, then imports the (locked) ``miner.py`` and
    binds its inference entrypoint. Requires torch/transformers/soundfile on a GPU host.
    """
    verify_canonical_script(model_dir, spec)
    model_dir = Path(model_dir)

    spec_name = spec.forbidden_py_except or "miner.py"
    module_path = model_dir / spec_name
    mod_spec = importlib.util.spec_from_file_location("vocence_canonical_miner", module_path)
    if mod_spec is None or mod_spec.loader is None:
        raise CanonicalScriptError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(module)

    # The canonical miner.py exposes a loader that returns an object with
    # `.speak(text, instruction) -> wav bytes` (the /speak contract). Support the two
    # names the template may use.
    factory = getattr(module, "load_model", None) or getattr(module, "build_engine", None)
    if factory is None:
        raise CanonicalScriptError(f"{spec_name} exposes no load_model()/build_engine() entrypoint")
    engine = factory(str(model_dir))

    def _generate(target_text: str, traits: Dict[str, object]) -> bytes:
        instruction = traits.get("instruction") if isinstance(traits, dict) else None
        if not instruction:
            instruction = _instruction_from_traits(traits)
        return engine.speak(target_text, instruction)

    return _generate


def _instruction_from_traits(traits: Dict[str, object]) -> str:
    """Render a natural-language voice instruction from structured traits."""
    parts = []
    for key in ("gender", "age", "tone", "emotion", "emotion_intensity", "pace", "pitch", "accent", "environment"):
        val = traits.get(key) if isinstance(traits, dict) else None
        if val:
            parts.append(f"{key.replace('_', ' ')}: {val}")
    return "; ".join(parts)
