# Vocence Miner Guide

Vocence is a voice intelligence subnet (PromptTTS, STT, STS, cloning, etc.). The current implementation (Q1) focuses on **PromptTTS**. Deploy a PromptTTS model as a Chute using the **canonical wrapper** template. Your engine and config live in a Hugging Face repo; you render the template with your repo/revision and Chutes user/name, then build and deploy.

---

## 1. Prerequisites

- Chutes developer account and API key.
- Hugging Face account (to host the miner repo).
- GPU recommended (e.g. 1 GPU, 16GB+ VRAM for typical PromptTTS models).

---

## 2. Repo contents (Hugging Face)

Your HF repo must include:

| File | Required | Description |
|------|----------|-------------|
| `miner.py` | Yes | PromptTTS engine: class `Miner`, `__init__(path_hf_repo: Path)`, `warmup()`, `generate_wav(instruction, text)` → `(waveform, sample_rate)`. |
| `chute_config.yml` | Yes | Image (base, pip), NodeSelector (GPU), Chute (tagline, scaling). Used at build time. |
| `vocence_config.yaml` | Yes | **Must declare `model_id` matching your committed `VOCENCE_REPO`** (see section 4). May also carry runtime/generation options read by your engine. |
| `*.safetensors` weight files | Yes | Weights MUST be shipped as `.safetensors`. The combined size of all `.safetensors` files must be at least **50 MiB**. Pickle-format weights (`.bin`/`.pt`/`.pth`/`.ckpt`) are not accepted. |

All engine logic must live in `miner.py`; only stdlib and site-packages may be imported (no other repo files). See **section 4** for the exact rules on what `miner.py` is allowed to do.

---

## 3. Engine contract

- **Constructor:** `Miner(path_hf_repo: Path)` — load config/weights from this path only.
- **warmup()** — Optional; one short `generate_wav` so the first request does not time out.
- **generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]** — Return mono float32 PCM and sample rate.

The wrapper has already downloaded your repo and the HF cache is populated before your `__init__` runs, so `from_pretrained(model_id)` resolves from disk without hitting the network.

---

## 4. `miner.py` rules

These rules are enforced **twice**: owner-side at registration (early rejection) and at chute startup by the canonical wrapper (hash-locked, can't be bypassed). They constrain **how** you load your model, not **what** your model is — every miner ships their own unique `miner.py`, with their own architecture, sampling, post-processing, and any other engine code they want.

### 4a. `vocence_config.yaml` must declare `model_id`

The file must contain a top-level `model_id` field whose value equals `VOCENCE_REPO` (the HF repo ID you commit on chain):

```yaml
model_id: "your-hf-user/your-repo-name"
# ... your other runtime/generation settings below
```

If `model_id` is missing, malformed, or doesn't match `VOCENCE_REPO`, the chute refuses to start and the owner marks the miner invalid (`model_id_mismatch:yaml=...`).

### 4b. `from_pretrained` must use the `model_id` variable

Every call to `from_pretrained` in `miner.py` must take the bare `model_id` variable as its first positional argument (or as the `pretrained_model_name_or_path` keyword). No string literals, no other variables, no expressions like `model_id + "-large"`.

✅ Allowed:
```python
import yaml
from transformers import AutoModel, AutoProcessor, AutoTokenizer

class Miner:
    def __init__(self, repo_path):
        cfg = yaml.safe_load((repo_path / "vocence_config.yaml").open())
        model_id = cfg["model_id"]
        self.tok       = AutoTokenizer.from_pretrained(model_id)
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model     = AutoModel.from_pretrained(model_id)
        # Subfolders within your own repo are fine:
        self.vocoder   = AutoModel.from_pretrained(model_id, subfolder="vocoder")
```

❌ Rejected:
```python
self.m = AutoModel.from_pretrained("other-user/their-model")   # hardcoded string
self.m = AutoModel.from_pretrained(model_id + "-large")        # expression
self.m = AutoModel.from_pretrained(some_other_var)             # wrong variable
```

You may load any model files from your own repo path directly — `torch.load(repo_path / "voice_latents.pt")`, `open(repo_path / "config.json")`, etc. are all unaffected.

### 4c. Banned function calls

These calls are banned anywhere in `miner.py`:

| Call | Why |
|------|-----|
| `snapshot_download(...)` | Pulls weights from arbitrary HF repos. |
| `hf_hub_download(...)` | Same. |
| `cached_download(...)` | Same. |
| `pipeline(...)` | Loads model from arbitrary `model=` arg, bypassing the `from_pretrained` check. |
| `torch.hub.load(...)` | Loads model from arbitrary GitHub/HF source. |
| `eval(...)`, `exec(...)`, `compile(...)` | Defeat static analysis. |
| `__import__("...")`, `importlib.import_module(...)` | Dynamic imports defeat static analysis. |

### 4d. Banned imports

These top-level modules (and any submodule of them) cannot be imported by `miner.py`:

| Module prefix | Why |
|---------------|-----|
| `requests`, `urllib`, `urllib2`, `urllib3`, `httpx`, `aiohttp` | Network egress. |
| `socket` | Raw network. |
| `huggingface_hub` | Direct HF API; bypasses `from_pretrained` rule. |
| `importlib` | Dynamic-import escape. |
| `torch.hub` | Arbitrary remote model loading. |

Other imports (`torch`, `transformers`, `numpy`, `yaml`, `tokenizers`, any other library you `pip install` in `chute_config.yml`) are fine.

### 4e. What stays completely up to you

The rules above are the entire surface. Within them, you choose:

- Model architecture (Parler-TTS, XTTS, Tortoise, your own transformer, anything).
- Training data, fine-tuning, weight precision.
- Inference logic: sampling strategy, beam search, classifier-free guidance, your own scheduler.
- Pre- and post-processing: text normalization, prosody tagging, waveform filtering, loudness normalization.
- Multiple sub-models loaded via `from_pretrained(model_id, subfolder=...)` for things like vocoder + acoustic model + tokenizer.
- Additional files in your repo: `torch.load(...)` voice embeddings, JSON configs, lookup tables, etc.

Two miners with two completely different `miner.py` files both pass as long as they follow 4a–4d.

---

## 5. Approved template variables (only these)

The canonical wrapper is generated from the template in `chute_template/`. You may change **only**:

| Variable | Meaning |
|---------|--------|
| `VOCENCE_REPO` | Hugging Face repo ID (e.g. `your-username/your-tts-repo`). |
| `VOCENCE_REVISION` | Repo revision; **commit hash strongly recommended**. |
| `VOCENCE_CHUTES_USER` | Chutes username. |
| `VOCENCE_CHUTE_ID` | Chute name you give when deploying (e.g. `vocence-tts-001`). **Must contain `vocence`** (case-insensitive); the owner checks the chute **name** from the Chutes API, not the chute_id (UUID) on chain. |

All other wrapper code is fixed. Changing anything else causes **wrapper integrity** to fail (see below).

`VOCENCE_REPO` must equal the `model_id` field in your `vocence_config.yaml` (see section 4a).

---

## 6. Render, build, deploy

1. Copy the template from `chute_template/vocence_chute.py.jinja2` and replace the placeholders:
   - `{{ huggingface_repository_name }}` → your repo ID  
   - `{{ huggingface_repository_revision }}` → your commit hash  
   - `{{ chute_username }}` → your Chutes user  
   - `{{ chute_name }}` → your **chute name** (the name you give the deployment in Chutes; must contain **vocence** somewhere, case-insensitive; e.g. `vocence-parler-tts-010`, `my-vocence-tts`). This is the deployment name in Chutes, not the chute_id (UUID) you commit on chain.  

2. Build: `chutes build <your_module>:chute --local` (or `--wait` for remote).

3. Deploy: `chutes deploy <your_module>:chute --accept-fee`.

4. Commit on chain: `model_name`, `model_revision`, `chute_id` (the UUID from Chutes; via CLI or your flow). The **chute name** (step 1) is what must contain "vocence"; the chute_id on chain is a UUID and is not checked for the magic word.

---

## 7. Owner validation (when your miner is registered)

**The owner** (central service) — not validators — runs the following checks against your registration. Any one failure marks you invalid until you fix it and re-register. Validators just call `/health` and `/speak`; they do **not** repeat these checks.

Validation runs on a **1-hour cycle**. Each new `(model, revision)` is audited once when it's first seen and the result is cached in the owner's database — subsequent cycles only re-check the chute-side state (still hot? still owned?). The expensive checks (download weights, compute tensor fingerprint) never run twice for the same commit.

| # | Check | Failure reason |
|---|-------|----------------|
| 1 | Chute exists in Chutes API | `chute_fetch_failed` |
| 2 | Chute name (Chutes-side) contains `vocence` | `chute_name_missing_vocence` |
| 3 | Wrapper integrity: deploy script hash matches canonical (masking the 4 approved vars) | `wrapper_hash_mismatch` / `chute_code_fetch_failed` |
| 4 | Chute is hot | `chute_not_running` |
| 5 | `VOCENCE_REVISION` is a 40-char hex sha (not `main`/tag) | `wrapper_revision_not_sha:...` |
| 6 | `VOCENCE_REPO`/`VOCENCE_REVISION` in the wrapper match what's committed on chain | `repo_mismatch:...` / `revision_mismatch:wrapper=...` |
| 7 | HF revision round-trips (committed sha equals what HF resolves) | `revision_mismatch:hf=...` |
| 8 | HF model fingerprint is computable | `hf_model_fetch_failed` |
| 9 | Repo contains `.safetensors` files | `safetensors_missing` |
| 10 | Combined size of `.safetensors` files ≥ **50 MiB** | `safetensors_below_min_size:<actual><threshold>` |
| 11 | `vocence_config.yaml` exists and `model_id` equals `VOCENCE_REPO` | `vocence_config_missing` / `vocence_config_missing_model_id` / `model_id_mismatch:yaml=...` |
| 12 | `miner.py` passes the source audit (section 4) | `miner_py_missing` / `banned_call:...` / `banned_import:...` / `from_pretrained_must_use_model_id` |
| 13 | Per-tensor fingerprint computed and persisted to `repo_tensor_fingerprints` | `tensor_fingerprint_failed` |

After all per-miner checks pass, the owner runs **two passes of duplicate detection** across miners. The rule is the same in both passes: earliest commit block keeps `is_valid = True`, later miners flip to invalid.

- **`model_hash` byte-equality** — catches lazy copies where weight files are bit-for-bit identical. Failure reason: `duplicate_model:earliest_uid=<uid>`.
- **Per-tensor fingerprint** — catches repackaging attacks that produce the same tensor values under different file layout (rename, re-shard, format conversion, non-LFS escape) plus partial-copy attacks where most layers are reused. Two thresholds:
  - 100% of tensors bit-identical → `tensor_clone_of:earliest_uid=<uid>`
  - ≥95% of tensors bit-identical → `tensor_near_clone_of:earliest_uid=<uid>:ratio=<r>`. This threshold is calibrated for LoRA-style fine-tuning of a shared base: standard LoRA recipes change 10–25% of tensors so honest independent fine-tunes typically match each other at 75–90% and pass cleanly. A cheater who clones an existing miner must modify at least ~5% of tensors to slip through, which materially degrades the model output.

Checks 1–8 are the existing chute/HF/wrapper gates; **9–13 plus tensor-fingerprint dedup are the new model-pinning gates** described in section 4.

---

## 8. API

- **GET /health** — Returns `status`, `hf_repo_id`, `hf_revision`, `model_loaded`, `sample_rate`, `adapter`.
- **POST /speak** — JSON: `{"instruction": "...", "text": "..."}`. Response: `audio/wav` (raw WAV bytes).

---

## 9. Example and references

- **example_repo/** — Mock HF repo layout: `miner.py`, `chute_config.yml`, `vocence_config.yaml`. Replace with your real engine and model files.
- **chute_template/** — Canonical Jinja2 template; render with your four variables and use as the deploy script.

All example data is placeholder only.
