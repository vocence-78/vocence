"""
Miner validation utilities for Vocence.

Provides functions for validating miners, fetching model hashes from HuggingFace,
and detecting duplicate/plagiarized models.
"""

import os
import asyncio
import hashlib
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import (
    EntryNotFoundError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)


class _TransientHFError(RuntimeError):
    """Transient infrastructure error during audit (HF fetch failed, DB unreachable).

    Caller should treat this as inconclusive — retry on the next validation cycle,
    do NOT cache the failure. Despite the name, this also covers transient DB errors
    raised during the audit-time tensor-collision check; using one class keeps the
    "retry next cycle" semantic in a single catch point.
    """


class _TensorCollisionError(RuntimeError):
    """Raised when a new commit's tensor fingerprint matches an existing DB row at
    or above TENSOR_NEAR_CLONE_THRESHOLD. The new commit must be rejected; the
    existing row is the claimant and stays untouched.
    """
    def __init__(self, matched_model: str, matched_revision: str, ratio: float):
        self.matched_model = matched_model
        self.matched_revision = matched_revision
        self.ratio = ratio
        super().__init__(
            f"tensor_clone_of_existing:{matched_model}@{matched_revision}:ratio={ratio:.3f}"
        )

from vocence.domain.config import (
    HF_AUTH_TOKEN,
    MODEL_FINGERPRINT_CACHE_TTL,
    BASE_MODEL_CHUTE_ID,
    BASE_MODEL_MODEL_NAME,
    BASE_MODEL_MODEL_REVISION,
    BASE_MODEL_WEIGHTS_HASH,
    REPO_FILE_MANIFEST,
    REPO_REQUIRED_FILES,
)
from vocence.shared.logging import emit_log
from vocence.domain.entities import ParticipantInfo
from vocence.adapters.chutes import fetch_chute_details, fetch_chute_code
from vocence.registry.wrapper_integrity import (
    check_wrapper_integrity,
    extract_approved_variables,
    is_valid_hf_revision,
)
from vocence.registry.source_audit import verify_miner_py_hash, verify_vocence_config

# Chute name must contain this substring (case-insensitive) for owner validation to pass.
# Checked against the chute name from Chutes API (e.g. vocence-parler-tts-010), not chute_id (UUID).
CHUTE_NAME_MAGIC_WORD = "vocence"

# Miners MUST ship weights as .safetensors. Pickle-based formats (.pt/.bin) are an RCE
# surface, and forcing one canonical format lets us fingerprint tensors deterministically
# (see audit_repo_artifacts). Below this floor a repo cannot hold a real PromptTTS model.
MIN_SAFETENSORS_BYTES = 50 * 1024 * 1024  # 50 MiB

# Cache for API blacklist
_api_blacklist_cache: Tuple[Set[str], float] = (set(), 0)
_API_BLACKLIST_CACHE_TTL = 300  # 5 minutes


def load_blacklist() -> set:
    """Load blacklisted hotkeys from the centralized API.
    
    Uses caching to avoid frequent API calls.
    
    Returns:
        Set of blacklisted hotkey addresses
    """
    global _api_blacklist_cache
    
    now = time.time()
    cached_blacklist, cached_at = _api_blacklist_cache
    
    # Return cached value if still valid
    if cached_blacklist and (now - cached_at) < _API_BLACKLIST_CACHE_TTL:
        return cached_blacklist
    
    try:
        # Run async fetch in sync context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't run async from sync in running loop, return cached
                return cached_blacklist
        except RuntimeError:
            pass
        
        blacklist = asyncio.run(_fetch_blacklist_from_api())
        _api_blacklist_cache = (blacklist, now)
        return blacklist
    except Exception as e:
        emit_log(f"Failed to fetch blacklist from API: {e}", "warn")
        return cached_blacklist  # Return stale cache on error


async def _fetch_blacklist_from_api() -> set:
    """Async fetch blacklist from API.
    
    Returns:
        Set of blacklisted miner hotkeys
    """
    from vocence.domain.config import API_URL as api_url
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{api_url}/blocklist/participants",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    hotkeys = await response.json()
                    return set(hotkeys)
                else:
                    emit_log(f"Blacklist API returned {response.status}", "warn")
                    return set()
        except Exception as e:
            emit_log(f"Blacklist API request failed: {e}", "warn")
            return set()


def _list_safetensors_files(siblings: List[Any]) -> List[Tuple[str, int]]:
    """Return [(filename, lfs_size_bytes), ...] for .safetensors files in the repo.

    Only LFS-tracked safetensors are counted (regular git blobs can't realistically
    hold a 50 MB+ model). Files without an LFS entry are skipped.
    """
    def _get_name(s: Any) -> str:
        return getattr(s, "rfilename", None) or getattr(s, "path", "") or ""

    out: List[Tuple[str, int]] = []
    for s in siblings:
        name = _get_name(s)
        if not name.endswith(".safetensors"):
            continue
        lfs = getattr(s, "lfs", None)
        if lfs is None:
            continue
        size = lfs.get("size") if isinstance(lfs, dict) else getattr(lfs, "size", None)
        if not size:
            continue
        try:
            out.append((name, int(size)))
        except (TypeError, ValueError):
            continue
    return out


async def _fetch_repo_info(model_id: str, revision: str) -> Optional[Any]:
    """Fetch HF RepoInfo for (model_id, revision). Returns None on transient failure."""
    def _fetch(token):
        return HfApi(token=token).repo_info(
            repo_id=model_id,
            repo_type="model",
            revision=revision,
            files_metadata=True,
        )
    try:
        return await asyncio.to_thread(_fetch, HF_AUTH_TOKEN or None)
    except Exception as e:
        emit_log(f"repo_info failed for {model_id}@{revision}: {e}", "warn")
        return None


def _list_all_files(siblings: List[Any]) -> List[str]:
    """Return all file paths from repo siblings."""
    out: List[str] = []
    for s in siblings:
        name = getattr(s, "rfilename", None) or getattr(s, "path", "") or ""
        if name:
            out.append(name)
    return out


async def get_safetensors_files(model_id: str, revision: str, repo_info: Optional[Any] = None) -> Optional[List[Tuple[str, int]]]:
    """Return [(filename, size)] of .safetensors files in an HF repo at a revision.

    Returns [] if the repo has none, or None on transient HF fetch failure.
    Accepts a pre-fetched repo_info to avoid redundant API calls.
    """
    if repo_info is None:
        repo_info = await _fetch_repo_info(model_id, revision)
    if repo_info is None:
        return None
    return _list_safetensors_files(getattr(repo_info, "siblings", None) or [])


async def get_repo_file_list(model_id: str, revision: str, repo_info: Optional[Any] = None) -> Optional[List[str]]:
    """Return the complete list of file paths in an HF repo at a revision.

    Returns None on transient HF fetch failure.
    """
    if repo_info is None:
        repo_info = await _fetch_repo_info(model_id, revision)
    if repo_info is None:
        return None
    return _list_all_files(getattr(repo_info, "siblings", None) or [])


def verify_repo_manifest(file_list: List[str]) -> Tuple[bool, Optional[str]]:
    """Check that the repo contains only allowed files and all required files are present."""
    file_set = set(file_list)
    extra = file_set - REPO_FILE_MANIFEST
    if extra:
        return False, f"extra_files:{','.join(sorted(extra))}"
    missing = REPO_REQUIRED_FILES - file_set
    if missing:
        return False, f"missing_required_files:{','.join(sorted(missing))}"
    return True, None


# Chunk size for streaming tensor bytes into the hash. 4 MiB is small enough to keep
# peak memory bounded on multi-GB models, large enough to avoid syscall overhead.
_TENSOR_HASH_CHUNK_BYTES = 4 * 1024 * 1024


def fingerprint_safetensors_file(path: str) -> Dict[str, str]:
    """Compute per-tensor SHA-256 hashes for one .safetensors file.

    Parses the safetensors header (8-byte little-endian length + JSON metadata)
    directly, then **streams** each tensor's raw byte range through the hash in
    fixed-size chunks tagged with (name, dtype, shape). Peak memory is bounded by
    `_TENSOR_HASH_CHUNK_BYTES` regardless of tensor size — multi-GB models work
    without OOM. No torch / safetensors dependency.

    Returns {tensor_name: hex_sha256}.
    """
    import json as _json
    out: Dict[str, str] = {}
    with open(path, "rb") as f:
        header_len = int.from_bytes(f.read(8), "little")
        header = _json.loads(f.read(header_len).decode("utf-8"))
        data_start = f.tell()
        for name, meta in header.items():
            if name == "__metadata__":
                continue
            offsets = meta.get("data_offsets")
            if not (isinstance(offsets, list) and len(offsets) == 2):
                continue
            dtype = meta.get("dtype", "")
            shape = tuple(meta.get("shape", []) or [])
            tensor_start = data_start + int(offsets[0])
            tensor_end = data_start + int(offsets[1])
            remaining = tensor_end - tensor_start
            if remaining < 0:
                continue
            f.seek(tensor_start)
            h = hashlib.sha256()
            h.update(f"{name}|{dtype}|{shape}|".encode())
            while remaining > 0:
                chunk = f.read(min(_TENSOR_HASH_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
            out[name] = h.hexdigest()
    return out


def _fetch_repo_text_file(model_id: str, revision: str, filename: str) -> Optional[str]:
    """Fetch a single text file from an HF repo at a pinned revision.

    Returns the file contents, or None if the file/repo/revision does not exist
    (permanent for this (model, revision) — safe to cache as invalid).

    Raises _TransientHFError on network / server failures so the caller can skip
    caching and retry on the next validation cycle.
    """
    try:
        path = hf_hub_download(
            repo_id=model_id,
            filename=filename,
            revision=revision,
            repo_type="model",
            token=HF_AUTH_TOKEN or None,
        )
    except (EntryNotFoundError, RepositoryNotFoundError, RevisionNotFoundError) as e:
        emit_log(f"hf file missing for {model_id}@{revision}/{filename}: {e}", "warn")
        return None
    except Exception as e:
        raise _TransientHFError(f"{model_id}@{revision}/{filename}: {e}") from e
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise _TransientHFError(f"read {model_id}@{revision}/{filename}: {e}") from e


# --- Per-repo artifact audit (immutable per pinned sha; cached for re-validation) ---


@dataclass(frozen=True)
class RepoArtifactAudit:
    """Result of the immutable repo-level checks: safetensors size + vocence_config + miner.py + tensor fingerprint."""
    is_valid: bool
    invalid_reason: Optional[str]
    safetensors_bytes: int
    # SHA-256 over the canonicalized tensor fingerprint dict — set when is_valid is True.
    # Equal across two repos iff every tensor in both has identical name + content. This is
    # the new model_hash used by detect_duplicates for fast exact-match dedup.
    model_hash: Optional[str] = None


# (model_id, revision) -> (audit_result, cached_at). Only successful or
# permanent-failure audits are cached; transient HF errors fall through so the
# next validation pass retries.
_repo_artifact_cache: Dict[Tuple[str, str], Tuple[RepoArtifactAudit, float]] = {}


def _model_hash_from_tensors(tensors: Dict[str, str]) -> str:
    """SHA-256 over the canonical (sorted, compact) JSON form of {tensor_name: sha256}.

    Same input → same output, independent of dict insertion order. Two repos that share
    every tensor name + content hash collide here; any difference yields a different hash.
    """
    import json as _json
    canonical = _json.dumps(tensors, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _compute_and_store_fingerprint(
    model_id: str,
    revision: str,
    safetensors_files: List[Tuple[str, int]],
    block: int = 0,
) -> Optional[str]:
    """Compute per-tensor fingerprint, persist to repo_tensor_fingerprints, return
    the combined model_hash.

    Returns None on permanent failure (parse error, missing file, empty tensors).
    Raises _TransientHFError on transient HF errors so the caller can retry without
    caching the failure. Raises _TensorCollisionError when the newly-computed
    fingerprint matches any existing DB row at >= TENSOR_NEAR_CLONE_THRESHOLD and the
    existing row has an earlier or equal commit block (earliest-block-wins).

    If the new commit has an earlier block than the colliding row, the existing row
    is evicted and the new fingerprint is stored instead.

    Skips the download if an entry for (model_id, revision) already exists.
    """
    from vocence.registry.persistence.repositories.repo_tensor_fingerprint_repository import (
        RepoTensorFingerprintRepository,
    )
    fp_repo = RepoTensorFingerprintRepository()
    existing = await fp_repo.get(model_id, revision)
    if existing is not None and existing:
        return _model_hash_from_tensors(existing)

    tensors: Dict[str, str] = {}
    total_bytes = 0
    # Download into a throwaway cache dir so the (multi-GB) safetensors are deleted
    # right after fingerprinting instead of accumulating in the global HF cache
    # forever. We only need each tensor's hash once; the result is persisted in the
    # DB and re-download is skipped on the early-return above, so a per-call temp
    # cache costs nothing and keeps validator disk usage bounded.
    with tempfile.TemporaryDirectory(prefix="vocence_fp_") as tmp_cache:
        for filename, size in safetensors_files:
            try:
                path = await asyncio.to_thread(
                    hf_hub_download,
                    repo_id=model_id,
                    filename=filename,
                    revision=revision,
                    repo_type="model",
                    token=HF_AUTH_TOKEN or None,
                    cache_dir=tmp_cache,
                )
            except (EntryNotFoundError, RepositoryNotFoundError, RevisionNotFoundError) as e:
                emit_log(f"safetensors {filename} missing in {model_id}@{revision}: {e}", "warn")
                return None
            except Exception as e:
                raise _TransientHFError(f"download {filename}: {e}") from e
            try:
                per_file = await asyncio.to_thread(fingerprint_safetensors_file, path)
            except Exception as e:
                emit_log(f"safetensors parse failed for {filename}: {e}", "warn")
                return None
            tensors.update(per_file)
            total_bytes += size

    if not tensors:
        emit_log(f"safetensors had no tensors for {model_id}@{revision}", "warn")
        return None

    # Audit-time DB collision check: scan every stored fingerprint. If the new
    # tensors match at >= TENSOR_NEAR_CLONE_THRESHOLD, compare commit blocks:
    # the miner who committed on-chain earlier wins. If the new commit is earlier,
    # evict the existing row and re-scan (there may be multiple collisions).
    # Fail-closed on DB errors.
    while True:
        try:
            collision = await fp_repo.find_collision(
                new_tensors=tensors,
                exclude_key=(model_id, revision),
                threshold=TENSOR_NEAR_CLONE_THRESHOLD,
            )
        except Exception as e:
            raise _TransientHFError(f"DB error during collision check for {model_id}@{revision}: {e}") from e
        if collision is None:
            break
        matched_model, matched_rev, ratio, existing_block = collision
        if block > 0 and existing_block > 0 and block < existing_block:
            emit_log(
                f"Tensor collision: {model_id}@{revision} (block={block}) has earlier "
                f"block than {matched_model}@{matched_rev[:12]} (block={existing_block}); "
                f"evicting existing entry",
                "warn",
            )
            try:
                await fp_repo.delete(matched_model, matched_rev)
            except Exception as e:
                raise _TransientHFError(f"DB error evicting {matched_model}@{matched_rev}: {e}") from e
            _repo_artifact_cache.pop((matched_model, matched_rev), None)
            continue
        emit_log(
            f"Tensor collision: {model_id}@{revision} (block={block}) matches existing "
            f"{matched_model}@{matched_rev[:12]} (block={existing_block}) at ratio={ratio:.3f}; rejecting",
            "warn",
        )
        raise _TensorCollisionError(matched_model, matched_rev, ratio)

    await fp_repo.upsert(model_id, revision, total_bytes, tensors, commit_block=block)
    return _model_hash_from_tensors(tensors)


async def audit_repo_artifacts(model_id: str, revision: str, block: int = 0) -> Optional[RepoArtifactAudit]:
    """Run all immutable per-repo checks once and cache by (model_id, revision).

    Repo content at a pinned HF sha cannot change, so once we've audited a (repo, sha)
    pair we never refetch it: process-level in-memory cache + DB-persisted tensor
    fingerprint mean each unique commit is downloaded at most once, ever.

    Steps:
      1. .safetensors files present and totaling >= MIN_SAFETENSORS_BYTES
      2. File manifest check (no extra files, all required files present)
      3. vocence_config.yaml declares model_name == on-chain model_name
      4. miner.py canonical hash check (must be byte-identical to locked version)
      5. tensor fingerprint computed and persisted to repo_tensor_fingerprints

    Returns None on transient HF errors so the caller can retry on the next cycle.
    """
    key = (model_id, revision)
    now = time.time()
    if key in _repo_artifact_cache:
        cached, cached_at = _repo_artifact_cache[key]
        if now - cached_at < MODEL_FINGERPRINT_CACHE_TTL:
            return cached

    # Fetch repo info once (shared across safetensors + file manifest checks).
    repo_info = await _fetch_repo_info(model_id, revision)
    if repo_info is None:
        return None  # transient

    # 1. Safetensors presence + minimum size.
    files = await get_safetensors_files(model_id, revision, repo_info=repo_info)
    if files is None:
        return None  # transient
    if not files:
        result = RepoArtifactAudit(False, "safetensors_missing", 0)
        _repo_artifact_cache[key] = (result, now)
        return result
    total_safetensors = sum(size for _, size in files)
    if total_safetensors < MIN_SAFETENSORS_BYTES:
        result = RepoArtifactAudit(
            False,
            f"safetensors_below_min_size:{total_safetensors}<{MIN_SAFETENSORS_BYTES}",
            total_safetensors,
        )
        _repo_artifact_cache[key] = (result, now)
        return result

    # 2. File manifest — only whitelisted files allowed, required files must exist.
    file_list = await get_repo_file_list(model_id, revision, repo_info=repo_info)
    if file_list is None:
        return None  # transient
    ok, reason = verify_repo_manifest(file_list)
    if not ok:
        result = RepoArtifactAudit(False, reason or "manifest_invalid", total_safetensors)
        _repo_artifact_cache[key] = (result, now)
        return result

    # 3. vocence_config.yaml.
    try:
        yaml_text = await asyncio.to_thread(_fetch_repo_text_file, model_id, revision, "vocence_config.yaml")
    except _TransientHFError as e:
        emit_log(f"transient HF error fetching vocence_config.yaml: {e}", "warn")
        return None
    if yaml_text is None:
        result = RepoArtifactAudit(False, "vocence_config_missing", total_safetensors)
        _repo_artifact_cache[key] = (result, now)
        return result
    ok, reason = verify_vocence_config(yaml_text, model_id)
    if not ok:
        result = RepoArtifactAudit(False, reason or "vocence_config_invalid", total_safetensors)
        _repo_artifact_cache[key] = (result, now)
        return result

    # 4. miner.py canonical hash check — must be byte-identical to the locked version.
    try:
        miner_src = await asyncio.to_thread(_fetch_repo_text_file, model_id, revision, "miner.py")
    except _TransientHFError as e:
        emit_log(f"transient HF error fetching miner.py: {e}", "warn")
        return None
    if miner_src is None:
        result = RepoArtifactAudit(False, "miner_py_missing", total_safetensors)
        _repo_artifact_cache[key] = (result, now)
        return result
    ok, reason = verify_miner_py_hash(miner_src)
    if not ok:
        result = RepoArtifactAudit(False, reason or "miner_py_hash_mismatch", total_safetensors)
        _repo_artifact_cache[key] = (result, now)
        return result

    # 5. Tensor fingerprint — compute, run audit-time DB collision check, then
    # persist. Earliest commit block wins; if the new commit is earlier than an
    # existing collision, the existing entry is evicted.
    try:
        model_hash = await _compute_and_store_fingerprint(model_id, revision, files, block=block)
    except _TransientHFError as e:
        emit_log(f"transient HF error computing tensor fingerprint: {e}", "warn")
        return None
    except _TensorCollisionError as e:
        result = RepoArtifactAudit(
            False,
            f"tensor_clone_of_existing:{e.matched_model}@{e.matched_revision[:12]}:ratio={e.ratio:.3f}",
            total_safetensors,
            None,
        )
        _repo_artifact_cache[key] = (result, now)
        return result
    if model_hash is None:
        result = RepoArtifactAudit(False, "tensor_fingerprint_failed", total_safetensors, None)
        _repo_artifact_cache[key] = (result, now)
        return result

    result = RepoArtifactAudit(True, None, total_safetensors, model_hash)
    _repo_artifact_cache[key] = (result, now)
    return result


async def validate_miner(
    session: aiohttp.ClientSession,
    uid: int,
    hotkey: str,
    model_name: str,
    model_revision: str,
    chute_id: str,
    block: int,
) -> ParticipantInfo:
    """Validate a single miner.
    
    Validation steps:
    1. Fetch chute info from Chutes API
    2. Check chute is running (hot)
    3. Fetch model info from HuggingFace
    4. Compute model hash for plagiarism detection
    
    Args:
        session: aiohttp client session
        uid: Miner UID
        hotkey: Miner hotkey address
        model_name: HuggingFace model repo (e.g., "user/repo")
        model_revision: Git commit hash
        chute_id: Chutes deployment UUID
        block: Block when miner committed
        
    Returns:
        ParticipantInfo with validation result
    """
    info = ParticipantInfo(
        uid=uid,
        hotkey=hotkey,
        model_name=model_name,
        model_revision=model_revision,
        chute_id=chute_id,
        block=block,
    )

    # Owner base-model chute: skip chute/wrapper/audit checks. The base model is shipped
    # by the owner from a special repo that doesn't go through the safetensors pipeline;
    # pin BASE_MODEL_WEIGHTS_HASH so detect_duplicates groups any miner who copies it.
    if chute_id == BASE_MODEL_CHUTE_ID:
        info.is_valid = True
        info.model_hash = BASE_MODEL_WEIGHTS_HASH
        emit_log(f"uid {uid} ({hotkey[:12]}...): base-model chute (BASE_MODEL_CHUTE_ID), always valid, pinned hash", "success")
        return info

    # Step 1: Fetch chute info (need slug to check magic word)
    chute = await fetch_chute_details(session, chute_id)
    if not chute:
        info.invalid_reason = "chute_fetch_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at chute_fetch", "warn")
        return info

    info.chute_slug = chute.get("slug", "")
    # Chute API may return "name" (display name) and/or "slug"; we check the name for the magic word
    chute_name = chute.get("name") or chute.get("slug") or ""

    # Step 1a: Chute name must contain the magic word "vocence" (e.g. vocence-parler-tts-010)
    if CHUTE_NAME_MAGIC_WORD not in (chute_name or "").lower():
        info.invalid_reason = "chute_name_missing_vocence"
        emit_log(
            f"uid {uid} ({hotkey[:12]}...): chute name must contain '{CHUTE_NAME_MAGIC_WORD}' (got {(chute_name or '')[:40]}...)",
            "warn",
        )
        return info
    # API may return hot: true or status: "hot"
    is_hot = chute.get("hot", False) or (chute.get("status") or "").lower() == "hot"
    info.chute_status = "hot" if is_hot else "cold"

    # Step 1b: Owner-side wrapper integrity — fetch deploy script from Chutes, hash vs canonical
    deployed_code = await fetch_chute_code(session, chute_id)
    if not deployed_code:
        info.invalid_reason = "chute_code_fetch_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at chute_code_fetch", "warn")
        return info
    valid_wrapper, wrapper_reason = check_wrapper_integrity(deployed_code)
    if not valid_wrapper:
        info.invalid_reason = wrapper_reason or "wrapper_hash_mismatch"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at wrapper_integrity ({wrapper_reason or 'wrapper_hash_mismatch'})", "warn")
        return info

    # Step 2: Check chute is running
    if not is_hot:
        info.invalid_reason = "chute_not_running"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at chute_hot (not running)", "warn")
        return info

    # Step 3: Cross-check on-chain commitments against the deploy script source.
    # The chute pulls miner.py at runtime using VOCENCE_REPO/VOCENCE_REVISION declared in
    # the deploy script, so those values — not the Chutes metadata — are what actually run.
    # (The Chutes /chutes/{id} response does not reliably expose `revision`, so the previous
    # `chute.get("revision")` check was a no-op and let miners deploy with VOCENCE_REVISION="main"
    # while committing a clean sha on chain.)
    deployed_vars = extract_approved_variables(deployed_code)
    wrapper_repo = deployed_vars["VOCENCE_REPO"]
    wrapper_revision = deployed_vars["VOCENCE_REVISION"]

    if not is_valid_hf_revision(wrapper_revision):
        info.invalid_reason = f"wrapper_revision_not_sha:{wrapper_revision or 'missing'}"
        emit_log(
            f"uid {uid} ({hotkey[:12]}...): failed at wrapper_revision_format "
            f"(VOCENCE_REVISION must be a 40-char hex sha, got {wrapper_revision!r})",
            "warn",
        )
        return info

    if wrapper_revision != model_revision:
        info.invalid_reason = f"revision_mismatch:wrapper={wrapper_revision}"
        emit_log(
            f"uid {uid} ({hotkey[:12]}...): failed at wrapper_revision_match "
            f"(chain={model_revision} wrapper={wrapper_revision})",
            "warn",
        )
        return info

    if wrapper_repo != model_name:
        info.invalid_reason = f"repo_mismatch:wrapper={wrapper_repo}"
        emit_log(
            f"uid {uid} ({hotkey[:12]}...): failed at wrapper_repo_match "
            f"(chain={model_name} wrapper={wrapper_repo})",
            "warn",
        )
        return info

    # Step 4: Immutable repo-content audit — safetensors presence + size,
    # vocence_config.yaml, miner.py source, per-tensor fingerprint, and model_hash.
    # Bundled and cached by (model, revision); each unique commit is audited once ever.
    # Revision existence is checked implicitly: any HF call inside the audit raises
    # RevisionNotFoundError if the pinned sha doesn't exist.
    audit = await audit_repo_artifacts(model_name, model_revision, block=block)
    if audit is None:
        info.invalid_reason = "repo_audit_fetch_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): transient HF fetch error, will retry next cycle", "warn")
        return info
    if not audit.is_valid:
        info.invalid_reason = audit.invalid_reason or "repo_audit_failed"
        emit_log(f"uid {uid} ({hotkey[:12]}...): failed at repo_audit ({audit.invalid_reason})", "warn")
        return info

    # A miner who commits the owner base-model HF repo from their own chute is pinned
    # to BASE_MODEL_WEIGHTS_HASH so detect_duplicates groups them with the owner.
    if model_name == BASE_MODEL_MODEL_NAME and model_revision == BASE_MODEL_MODEL_REVISION:
        info.model_hash = BASE_MODEL_WEIGHTS_HASH
    else:
        info.model_hash = audit.model_hash

    info.is_valid = True
    emit_log(
        f"uid {uid} ({hotkey[:12]}...): passed chute_fetch, wrapper_integrity, chute_hot, "
        f"wrapper_revision_match, wrapper_repo_match, repo_audit",
        "success",
    )
    return info


def detect_duplicates(miners: List[ParticipantInfo]) -> List[ParticipantInfo]:
    """Detect plagiarism by checking duplicate model hashes.
    
    For each unique model hash, only the miner with the earliest
    commit block is kept as valid. Later miners are marked as duplicates.
    
    Args:
        miners: List of validated miners
        
    Returns:
        Updated miners list with plagiarism detection applied
    """
    # Group valid miners by model hash
    hash_to_miners: Dict[str, List[Tuple[int, int, MinerDetails]]] = {}
    
    for miner in miners:
        if not miner.is_valid or not miner.model_hash:
            continue
        
        if miner.model_hash not in hash_to_miners:
            hash_to_miners[miner.model_hash] = []
        hash_to_miners[miner.model_hash].append((miner.block, miner.uid, miner))
    
    # Keep only earliest miner for each hash
    for model_hash, group in hash_to_miners.items():
        if len(group) <= 1:
            continue

        # Sort by block (earliest first), then by UID
        group.sort(key=lambda x: (x[0], x[1]))
        earliest_block, earliest_uid, _ = group[0]

        # Mark duplicates as invalid
        for block, uid, miner in group[1:]:
            if miner.is_valid:
                miner.is_valid = False
                miner.invalid_reason = f"duplicate_model:earliest_uid={earliest_uid}"
                emit_log(
                    f"Duplicate model detected: uid={uid} copied from uid={earliest_uid} "
                    f"(hash={model_hash[:16]}...)",
                    "warn"
                )

    return miners


# Fraction of per-tensor hashes that must match an earlier miner for the later one
# to be marked a duplicate. Below 1.0 catches partial-copy attacks (clone most layers,
# tweak a few); 1.0 alone would only catch exact clones with identical packaging.
TENSOR_NEAR_CLONE_THRESHOLD = 0.95


def _tensor_match_ratio(earlier: Dict[str, str], later: Dict[str, str]) -> float:
    """Fraction of tensors in `later` that are bit-identical to the same-name tensor in `earlier`.

    Denominator is len(later) so the ratio is robust to architecture mismatch — a
    legitimately different model with no shared tensor names yields 0.0, not divide-by-zero.
    """
    if not later:
        return 0.0
    matching = sum(1 for k, v in later.items() if earlier.get(k) == v)
    return matching / len(later)


async def detect_tensor_duplicates(miners: List[ParticipantInfo]) -> List[ParticipantInfo]:
    """Mark later-committed miners as duplicate if their per-tensor fingerprint
    overlaps a strictly earlier miner's by >= TENSOR_NEAR_CLONE_THRESHOLD.

    Catches the byte-level repackaging tricks the file-hash check misses
    (rename, re-shard, format conversion, non-LFS escape) and partial-copy attacks
    where the cheater replaces only a few layers. ε-noise on every tensor still
    slips through — that's the explicit accepted gap (see scoring docs).

    Fingerprints come from the repo_tensor_fingerprints table, populated by
    audit_repo_artifacts the first time each unique (model, revision) is seen.
    """
    from vocence.registry.persistence.repositories.repo_tensor_fingerprint_repository import (
        RepoTensorFingerprintRepository,
    )

    keys = [
        (m.model_name, m.model_revision)
        for m in miners
        if m.is_valid and m.model_name and m.model_revision
    ]
    if not keys:
        return miners

    fp_repo = RepoTensorFingerprintRepository()
    fingerprints = await fp_repo.get_many(keys)
    if not fingerprints:
        return miners

    # Only compare miners we have fingerprints for, in earliest-commit-first order.
    candidates = [
        m for m in miners
        if m.is_valid and (m.model_name, m.model_revision) in fingerprints
    ]
    candidates.sort(key=lambda m: ((m.block or 0), m.uid))

    for i, later in enumerate(candidates):
        if not later.is_valid:
            continue
        fp_later = fingerprints[(later.model_name, later.model_revision)]
        for earlier in candidates[:i]:
            if not earlier.is_valid:
                continue
            # Skip self-pair when two miners share a (model, revision); the
            # model_hash dedup pass above already handled that case.
            if (earlier.model_name, earlier.model_revision) == (later.model_name, later.model_revision):
                continue
            fp_earlier = fingerprints[(earlier.model_name, earlier.model_revision)]
            ratio = _tensor_match_ratio(fp_earlier, fp_later)
            if ratio >= 1.0:
                later.is_valid = False
                later.invalid_reason = f"tensor_clone_of:earliest_uid={earlier.uid}"
                emit_log(
                    f"Tensor clone: uid={later.uid} matches uid={earlier.uid} "
                    f"({len(fp_later)} tensors, ratio=1.00)",
                    "warn",
                )
                break
            if ratio >= TENSOR_NEAR_CLONE_THRESHOLD:
                later.is_valid = False
                later.invalid_reason = (
                    f"tensor_near_clone_of:earliest_uid={earlier.uid}:ratio={ratio:.3f}"
                )
                emit_log(
                    f"Tensor near-clone: uid={later.uid} matches uid={earlier.uid} "
                    f"at ratio={ratio:.3f}",
                    "warn",
                )
                break

    return miners

