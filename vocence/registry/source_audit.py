"""
Owner-side miner.py + vocence_config.yaml audit.

Mirrors the static checks that the canonical wrapper template runs at chute startup
(see canonical_wrapper_template.jinja2). The wrapper is the hash-locked backstop —
this module exists so the owner can reject non-compliant miners at registration time
without waiting for a chute boot.

If you change a rule here, change it in the wrapper template too. The audit must
stay byte-for-byte equivalent in behavior across both sides.
"""

import ast
from typing import Any, Tuple

import yaml

# Last-component call names that are forbidden anywhere in miner.py.
_BANNED_CALL_NAMES = frozenset({
    "snapshot_download", "hf_hub_download", "cached_download",
    "pipeline", "eval", "exec", "compile", "__import__", "import_module",
})

# Fully-dotted calls banned even when the last component is innocuous (e.g. `load`).
_BANNED_DOTTED_CALLS = frozenset({
    "torch.hub.load",
})

# Top-level (or dotted-prefix) imports banned in miner.py.
_BANNED_IMPORT_PREFIXES: Tuple[str, ...] = (
    "requests", "urllib", "urllib2", "urllib3", "httpx", "aiohttp",
    "socket", "huggingface_hub", "importlib", "torch.hub",
)

# Variable name miner.py must use for the declared model.
_MODEL_ID_VAR = "model_id"


def _call_dotted_name(call: ast.Call) -> str:
    parts: list[str] = []
    node: Any = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _import_banned(name: str) -> bool:
    return any(name == p or name.startswith(p + ".") for p in _BANNED_IMPORT_PREFIXES)


def verify_miner_source(source: str) -> Tuple[bool, str | None]:
    """Static check of miner.py source.

    Returns (True, None) if compliant, (False, reason) otherwise. Mirror of
    vocence_verify_miner_source in the canonical wrapper template.
    """
    if not source or not source.strip():
        return False, "miner_py_empty"
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"miner_py_syntax_error:{e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _import_banned(alias.name):
                    return False, f"banned_import:{alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _import_banned(mod):
                return False, f"banned_import_from:{mod}"
        elif isinstance(node, ast.Call):
            dotted = _call_dotted_name(node)
            last = dotted.rsplit(".", 1)[-1] if dotted else ""
            if dotted in _BANNED_DOTTED_CALLS or last in _BANNED_CALL_NAMES:
                return False, f"banned_call:{dotted or last}"
            if last == "from_pretrained":
                arg: Any = node.args[0] if node.args else None
                if arg is None:
                    for kw in node.keywords:
                        if kw.arg == "pretrained_model_name_or_path":
                            arg = kw.value
                            break
                if not isinstance(arg, ast.Name) or arg.id != _MODEL_ID_VAR:
                    return False, "from_pretrained_must_use_model_id"
    return True, None


def verify_vocence_config(yaml_text: str, expected_model_id: str) -> Tuple[bool, str | None]:
    """Parse vocence_config.yaml and verify model_id matches the on-chain repo.

    Returns (True, None) if compliant, (False, reason) otherwise.
    """
    if not yaml_text or not yaml_text.strip():
        return False, "vocence_config_empty"
    try:
        cfg = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        return False, f"vocence_config_parse_error:{e}"
    if not isinstance(cfg, dict):
        return False, "vocence_config_not_a_mapping"
    declared = str(cfg.get("model_id") or "").strip()
    if not declared:
        return False, "vocence_config_missing_model_id"
    if declared != expected_model_id:
        return False, f"model_id_mismatch:yaml={declared}"
    return True, None
