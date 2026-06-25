"""
Configuration settings for Vocence validator.

All configurable values are loaded from environment variables (.env or process env)
with sensible defaults defined here. Load .env first so imports see env vars.
"""

import os
from pathlib import Path
from typing import List

# Load .env from current working directory (or parents) so all config below sees it
def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path.cwd() / ".env")
        load_dotenv()  # also discover .env in cwd/parents
    except ImportError:
        pass


_load_env()

# Bittensor network configuration
# NETWORK / CHAIN_NETWORK: finney (mainnet), test (testnet), or local
# NETUID / SUBNET_ID: subnet id (default 78 mainnet; for testnet set NETWORK=test and NETUID=XXX in .env)
CHAIN_NETWORK = os.environ.get("CHAIN_NETWORK") or os.environ.get("NETWORK", "finney")
SUBNET_ID = int(os.environ.get("SUBNET_ID") or os.environ.get("NETUID", "78"))
CYCLE_LENGTH = int(os.environ.get("CYCLE_LENGTH", "150"))  # Set weights every 150 blocks (~30 minutes)
# Block % CYCLE_LENGTH == CYCLE_OFFSET_BLOCKS → run (e.g. 15 → blocks 165, 315, 465, ...)
CYCLE_OFFSET_BLOCKS = int(os.environ.get("CYCLE_OFFSET_BLOCKS", "15"))
# Allow execution when block is within ±N of the exact cycle/slot block (avoids missing due to transient failures)
CYCLE_BLOCK_TOLERANCE = int(os.environ.get("CYCLE_BLOCK_TOLERANCE", "2"))
SAMPLE_SLOT_BLOCK_TOLERANCE = int(os.environ.get("SAMPLE_SLOT_BLOCK_TOLERANCE", "2"))
# Timeout for subtensor RPC (get_current_block, metagraph, set_weights); prevents hanging forever if connection drops
SUBTENSOR_TIMEOUT_SEC = int(os.environ.get("SUBTENSOR_TIMEOUT_SEC", "60"))
QUERY_TIMEOUT = int(os.environ.get("QUERY_TIMEOUT", "300"))

# Assessment configuration
ASSESSMENT_INTERVAL = int(os.environ.get("ASSESSMENT_INTERVAL", "600"))  # Legacy; sample timing is block-based when subtensor is used
# Block-based sample slots: validators send tasks every SAMPLE_SLOT_INTERVAL_BLOCKS; offset staggers 5 validators (id 0->0, 1->30, 2->60, 3->90, 4->120).
VALIDATOR_ID = int(os.environ.get("VALIDATOR_ID", "1"))  # 0–4 for staggered slots; default 1
SAMPLE_SLOT_INTERVAL_BLOCKS = int(os.environ.get("SAMPLE_SLOT_INTERVAL_BLOCKS", "150"))
# Derived: block % INTERVAL == this value → run sample round
SAMPLE_SLOT_OFFSET_BLOCKS = (VALIDATOR_ID % 6) * 25  # 0, 25, 50, 75, 100, 125
MIN_EVALS_TO_COMPETE = int(os.environ.get("MIN_EVALS_TO_COMPETE", "40"))  # Miner must have more than this many evals in at least 3 validator buckets to be globally eligible
THRESHOLD_MARGIN = 0.02
# Block height after which on-chain commits are recognized at all. Any commit at a
# block < COMMIT_LOCK_BLOCK is ignored entirely — not counted toward the per-hotkey
# cap, not selectable as the miner's current commitment, treated as if it never
# happened. A hotkey with only pre-cutover commits is skipped (no participant entry).
# Per-hotkey cap of MAX_POST_CUTOVER_COMMITS field-valid commits applies to commits
# at/after this block; exceeding the cap marks the miner invalid.
# Set COMMIT_LOCK_BLOCK=0 to disable both the cutover and the cap (legacy behaviour).
COMMIT_LOCK_BLOCK = int(os.environ.get("COMMIT_LOCK_BLOCK", "8270310"))
MAX_POST_CUTOVER_COMMITS = int(os.environ.get("MAX_POST_CUTOVER_COMMITS", "2"))
# Most recent N evaluations used for scoring (validator S3 + owner metrics). Default 50.
MAX_EVALS_FOR_SCORING = int(os.environ.get("MAX_EVALS_FOR_SCORING", "50"))
# Exponent applied to validator stake to derive its weight in global scoring:
# weight = stake ** VALIDATOR_WEIGHT_EXPONENT. Lower exponent compresses the gap
# between large- and small-stake validators (0.5 = sqrt, 0.25 = fourth root,
# 1.0 = linear). Default 0.25 keeps stake ordering while tightening influence spread.
VALIDATOR_WEIGHT_EXPONENT = float(os.environ.get("VALIDATOR_WEIGHT_EXPONENT", "0.25"))

# Chutes API configuration
CHUTES_BASE_URL = os.environ.get("CHUTES_BASE_URL", "https://api.chutes.ai")
CHUTES_AUTH_KEY = os.environ.get("CHUTES_AUTH_KEY") or os.environ.get("CHUTES_API_KEY")

# Wallet configuration
COLDKEY_NAME = os.environ.get("COLDKEY_NAME") or os.environ.get("WALLET_NAME", "default")
HOTKEY_NAME = os.environ.get("HOTKEY_NAME", "default")

# Hippius S3 configuration (https://console.hippius.com/dashboard/settings)
#
# Owner (centralized service): one credential set for corpus bucket (upload source audio).
# Validator: two credential sets — corpus (read owner's bucket via owner-provided sub_key)
# and validator's own (samples bucket, DB-related storage).
#
# Validator identity: used to build the validator's samples bucket name (e.g. vocence-samples-rt21, vocence-samples-yuma).
# Set VALIDATOR_NAME in .env (e.g. rt21, yuma, rizzo, kraken); validators typically set this.
VALIDATOR_NAME = (os.environ.get("VALIDATOR_NAME", "default").strip().lower().replace("_", "-") or "default")

# Bucket names
# Validator's own bucket: derived from VALIDATOR_NAME unless overridden (e.g. vocence-samples-rt21, vocence-samples-yuma).
AUDIO_SAMPLES_BUCKET = os.environ.get("AUDIO_SAMPLES_BUCKET") or f"vocence-samples-{VALIDATOR_NAME}"

# Validator's own credentials (samples bucket, uploads, reading peer buckets).
HIPPIUS_VALIDATOR_ACCESS_KEY = os.environ.get("HIPPIUS_VALIDATOR_ACCESS_KEY") or os.environ.get("HIPPIUS_ACCESS_KEY", "")
HIPPIUS_VALIDATOR_SECRET_KEY = os.environ.get("HIPPIUS_VALIDATOR_SECRET_KEY") or os.environ.get("HIPPIUS_SECRET_KEY", "")

# OpenAI configuration (OPENAI_AUTH_KEY or OPENAI_API_KEY from .env)
OPENAI_AUTH_KEY = os.environ.get("OPENAI_AUTH_KEY") or os.environ.get("OPENAI_API_KEY")
# Model for audio-in description and comparison (transcription + traits, first/second choice).
# Hardcoded — must match across all honest validators for cross-validator scoring to converge.
GPT_AUDIO_MODEL = "gpt-audio-1.5"

# Audio generation placeholder model
PLACEHOLDER_TTS_ENDPOINT = os.environ.get(
    "PLACEHOLDER_TTS_ENDPOINT",
    "https://chutes-wan-2-2-tts-14b-fast.chutes.ai/synthesize"
)

# Audio processing settings
MIN_AUDIO_SIZE = int(os.environ.get("MIN_AUDIO_SIZE", "100000"))  # 0.1MB minimum
MAX_AUDIO_SIZE = int(os.environ.get("MAX_AUDIO_SIZE", "10000000"))  # 10MB maximum
CLIP_LENGTH_SECONDS = int(os.environ.get("CLIP_LENGTH_SECONDS", "20"))  # seconds

# Validator: required duration range for source audio from corpus (skip if outside range)
AUDIO_SOURCE_MIN_DURATION_SEC = int(os.environ.get("AUDIO_SOURCE_MIN_DURATION_SEC", "20"))
AUDIO_SOURCE_MAX_DURATION_SEC = int(os.environ.get("AUDIO_SOURCE_MAX_DURATION_SEC", "25"))

# Track recently used audio to avoid repeats (in-memory)
USED_AUDIO_FILES: List[str] = []
MAX_AUDIO_HISTORY = int(os.environ.get("MAX_AUDIO_HISTORY", "50"))

# Chute endpoint resolution
CHUTE_INFO_CACHE_TTL = int(os.environ.get("CHUTE_INFO_CACHE_TTL", "300"))  # 5 minutes
# Max concurrent requests to miners' chutes per round. Raise for more miners (e.g. 20); lower if you hit rate limits.
MAX_PARALLEL_MINERS = int(os.environ.get("MAX_PARALLEL_MINERS", "20"))
# Max concurrent OpenAI evaluations (forced-choice) per round. Lower if you hit OpenAI rate limits.
MAX_PARALLEL_EVALS = int(os.environ.get("MAX_PARALLEL_EVALS", "4"))
VALIDATOR_BUCKETS_JSON = os.environ.get("VALIDATOR_BUCKETS_JSON", "")
MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING = int(
    os.environ.get("MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING", "3")
)
MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY = int(
    os.environ.get("MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY", "3")
)
MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE = int(
    os.environ.get("MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE", "1")
)

# Validator: optional local copy of metadata before uploading to Hippius (default: disabled)
# Only when VALIDATOR_SAVE_LOCAL_SAMPLES is true AND VALIDATOR_LOCAL_SAMPLES_DIR is set do we create the dir and save.
_validator_save_local = (os.environ.get("VALIDATOR_SAVE_LOCAL_SAMPLES", "false").strip().lower() in ("1", "true", "yes"))
_validator_local_dir = (os.environ.get("VALIDATOR_LOCAL_SAMPLES_DIR", "").strip() or None)
VALIDATOR_SAVE_LOCAL_SAMPLES = _validator_save_local and bool(_validator_local_dir)
VALIDATOR_LOCAL_SAMPLES_DIR = _validator_local_dir if VALIDATOR_SAVE_LOCAL_SAMPLES else None

# Database configuration
DB_CONNECTION_STRING = os.environ.get("DB_CONNECTION_STRING")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "vocence")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "vocence")
DATABASE_ECHO = (os.environ.get("DATABASE_ECHO", "").lower() == "true")

# Owner/validator API client (validators and generator call owner API)
API_URL = os.environ.get("API_URL", "http://localhost:8000")
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "30"))
API_MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", "3"))
ACTIVE_VALIDATOR_WINDOW_HOURS = int(os.environ.get("ACTIVE_VALIDATOR_WINDOW_HOURS", "24"))

# HTTP service (owner API server)
SERVICE_HOST = os.environ.get("SERVICE_HOST", "0.0.0.0")
SERVICE_PORT = int(os.environ.get("SERVICE_PORT", "8063"))
SERVICE_RELOAD = (os.environ.get("SERVICE_RELOAD", "").lower() == "true")

# Serve process logs: daily .log files (UTC), one file per day. Set to empty to disable file logging.
LOG_DIR = os.environ.get("LOG_DIR", "logs")

# Background worker intervals (seconds)
PARTICIPANT_VALIDATION_INTERVAL = int(os.environ.get("PARTICIPANT_VALIDATION_INTERVAL", "3600"))
METRICS_CALCULATION_INTERVAL = int(os.environ.get("METRICS_CALCULATION_INTERVAL", "1800"))

# Local miner registry: validators validate miners themselves into a local SQLite DB
# instead of calling the owner API (set false to fall back to the API).
USE_LOCAL_REGISTRY = (os.environ.get("USE_LOCAL_REGISTRY", "true").lower() == "true")
REGISTRY_DB_PATH = os.environ.get("REGISTRY_DB_PATH", os.path.join(os.getcwd(), "data", "registry.sqlite"))
# Centralized blacklist cached on disk (fail to last-known on API outage).
BLOCKLIST_CACHE_PATH = os.environ.get("BLOCKLIST_CACHE_PATH", os.path.join(os.getcwd(), "data", "blocklist_cache.json"))
# Min stake for a peer validator's bucket to count as active at weight-set time.
ACTIVE_VALIDATOR_MIN_STAKE = float(os.environ.get("ACTIVE_VALIDATOR_MIN_STAKE", "0"))
# Block-aligned miner validation so all validators validate the same on-chain snapshot.
# Validate at each block boundary (block % INTERVAL == 0), pinning commitments+metagraph
# to that block. Offset is 0 for everyone (aligned, not staggered). MAX_LAG keeps the
# pinned block within node state-pruning (~256 blocks) so it stays queryable.
REGISTRY_VALIDATION_INTERVAL_BLOCKS = int(os.environ.get("REGISTRY_VALIDATION_INTERVAL_BLOCKS", "300"))
REGISTRY_VALIDATION_MAX_LAG_BLOCKS = int(os.environ.get("REGISTRY_VALIDATION_MAX_LAG_BLOCKS", "200"))

# Auth (owner API)
SIGNATURE_EXPIRY_SECONDS = int(os.environ.get("SIGNATURE_EXPIRY_SECONDS", "300"))
ADMIN_HOTKEYS = [x.strip() for x in os.environ.get("ADMIN_HOTKEYS", "").split(",") if x.strip()]

# Base model (owner-deployed) and burn key
# The owner never commits on chain; we inject them as a synthetic participant with uid=OWNER_UID (0), treated as
# committed at BASE_MODEL_COMMIT_BLOCK so miners must beat them by THRESHOLD_MARGIN to win. Replace mock values.
OWNER_UID = 0
OWNER_HOTKEY = "5Fk765B4CRBekwErwE5VxvveWhHztHSfsnsLt8cbDayDWsuk"  # Replace with owner's hotkey
BASE_MODEL_CHUTE_ID = "5e990736-9690-5b52-abe1-6b1e99751d1e"  # Chute ID for owner's base model (replace with real)
BASE_MODEL_MODEL_NAME = "concil859856/qwen3-voicedesign-base"  # HuggingFace model name for owner base model (replace with real)
BASE_MODEL_MODEL_REVISION = "9f2d4c9f23e66f6700b7ca1420d5a8acb7662e7f"  # Model revision for owner base model (replace with real)
# Pinned model_hash for the owner base model — overrides the tensor-fingerprint-derived
# model_hash in validate_miner so detect_duplicates groups any miner that commits the base
# model under their own chute with the owner participant.
BASE_MODEL_WEIGHTS_HASH = "bdd08e0d48fef836a5a941eb2ab666ebc85be056566376fc672c584c0346b125"
BASE_MODEL_COMMIT_BLOCK = 1000  # Block at which owner is treated as committed (never on chain)
# UID 0 is the burn key on Bittensor; when no miner is eligible, validators set weight 1 on UID 0 to burn incentives.
BURN_UID = 0

# ---------------------------------------------------------------------------
# Canonical miner.py enforcement
# ---------------------------------------------------------------------------
# SHA-256 of the locked miner.py that all miners must ship byte-for-byte.
# Miners fine-tune the Qwen3 1.7B 12Hz voice-design model; this fixed inference
# script prevents pre/post-processing, alternative models, and speaker-embedding
# injection through the inference path.
CANONICAL_MINER_PY_SHA256 = "4e57d5ee0151681931d6601503fc8e1d21b9d2f22b2e359778727b14ee6ea212"

# Exhaustive whitelist of files allowed in a miner's HF repo. Any file not in
# this set is rejected; miners cannot add new files.
REPO_FILE_MANIFEST = frozenset({
    ".gitattributes",
    ".gitignore",
    "README.md",
    "chute_config.yml",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "miner.py",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "vocence_config.yaml",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/config.json",
    "speech_tokenizer/configuration.json",
    "speech_tokenizer/preprocessor_config.json",
})

# Subset of REPO_FILE_MANIFEST that must be present. Metadata files like
# .gitattributes, .gitignore, and README.md are optional.
REPO_REQUIRED_FILES = frozenset({
    "config.json",
    "generation_config.json",
    "merges.txt",
    "miner.py",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "vocence_config.yaml",
    "chute_config.yml",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/config.json",
    "speech_tokenizer/configuration.json",
    "speech_tokenizer/preprocessor_config.json",
})

# HuggingFace configuration
HF_AUTH_TOKEN = os.environ.get("HF_AUTH_TOKEN")
MODEL_FINGERPRINT_CACHE_TTL = int(os.environ.get("MODEL_FINGERPRINT_CACHE_TTL", "3600"))  # 1 hour

# Local audio corpus (per-validator LibriVox source clips on disk)
AUDIO_CORPUS_MAX_ENTRIES = int(os.environ.get("AUDIO_CORPUS_MAX_ENTRIES", "10000"))  # Max clips in corpus; prune oldest when exceeded
# Local corpus directory: each validator maintains its own source-audio corpus on disk
# (English LibriVox clips, 20-25s) instead of reading from a shared S3 corpus bucket.
CORPUS_LOCAL_DIR = os.environ.get("CORPUS_LOCAL_DIR", os.path.join(os.getcwd(), "data", "corpus"))
# Download cadence. While BELOW the cap, pull a chapter every SOURCE_AUDIO_DOWNLOAD_INTERVAL
# seconds to fill quickly. Once AT the cap, switch to a slow freshness rotation every
# CORPUS_REFRESH_INTERVAL_SEC seconds so we don't hammer LibriVox (a free service) forever.
SOURCE_AUDIO_DOWNLOAD_INTERVAL = int(os.environ.get("SOURCE_AUDIO_DOWNLOAD_INTERVAL", "60"))  # fill mode (below cap)
CORPUS_REFRESH_INTERVAL_SEC = int(os.environ.get("CORPUS_REFRESH_INTERVAL_SEC", "3600"))  # maintenance mode (at cap)
# Backoff (seconds) applied when LibriVox rate-limits us (HTTP 429); doubles up to this cap.
CORPUS_RATE_LIMIT_BACKOFF_SEC = int(os.environ.get("CORPUS_RATE_LIMIT_BACKOFF_SEC", "900"))
LIBRIVOX_CLIPS_PER_CHAPTER = int(os.environ.get("LIBRIVOX_CLIPS_PER_CHAPTER", "10"))
LIBRIVOX_CLIP_MIN_SEC = int(os.environ.get("LIBRIVOX_CLIP_MIN_SEC", "20"))
LIBRIVOX_CLIP_MAX_SEC = int(os.environ.get("LIBRIVOX_CLIP_MAX_SEC", "25"))
