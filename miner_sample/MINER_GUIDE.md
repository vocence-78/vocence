# Vocence Miner Guide

Vocence is a voice intelligence subnet (PromptTTS, STT, STS, cloning, etc.). The current implementation (Q1) focuses on **PromptTTS**. Deploy a PromptTTS model as a Chute using the **canonical wrapper** template. Your engine and config live in a Hugging Face repo; you render the template with your repo/revision and Chutes user/name, then build and deploy.

---

## 1. Prerequisites

- Chutes developer account and API key.
- Hugging Face account (to host the miner repo).

---

## 2. Repo contents (Hugging Face)

Your HF repo must include:

| File | Required | Description |
|------|----------|-------------|
| `miner.py` | Yes | PromptTTS engine: class `Miner`, `__init__(path_hf_repo: Path)`, `warmup()`, `generate_wav(instruction, text)` → `(waveform, sample_rate)`. |
| `chute_config.yml` | Yes | Image (base, pip), NodeSelector (GPU), Chute (tagline, scaling). Used at build time. |
| `vocence_config.yaml` | Yes | **Must declare `model_name` matching what you committed on chain** (see section 4). May also carry runtime/generation options read by your engine. |
| `*.safetensors` weight files | Yes | Weights MUST be shipped as `.safetensors`. The combined size of all `.safetensors` files must be at least **50 MiB**. Pickle-format weights (`.bin`/`.pt`/`.pth`/`.ckpt`) are not accepted. |

All engine logic must live in `miner.py`; only stdlib and site-packages may be imported (no other repo files). See **section 4** for the exact rules on what `miner.py` is allowed to do.

---

## 3. Engine contract

- **Constructor:** `Miner(path_hf_repo: Path)` — load config/weights from this path only.
- **warmup()** — Optional; one short `generate_wav` so the first request does not time out.
- **generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]** — Return mono float32 PCM and sample rate.

The wrapper has already downloaded your repo and the HF cache is populated before your `__init__` runs, so `from_pretrained(model_name)` resolves from disk without hitting the network.

---

## 4. `miner.py` rules

These rules are enforced **twice**: owner-side at registration (early rejection) and at chute startup by the canonical wrapper (hash-locked, can't be bypassed). They constrain **how** you load your model, not **what** your model is — every miner ships their own unique `miner.py`, with their own architecture, sampling, post-processing, and any other engine code they want.

**Naming.** The HF repo ID is called `model_name` everywhere a miner directly interacts with it (matching the `--model-name` CLI flag), and is rendered as `VOCENCE_REPO` inside the canonical wrapper template:

| Where | Field name |
|---|---|
| On chain (your commitment JSON) | `model_name` |
| CLI flag for `vocence miner push` / `commit` | `--model-name` |
| In `vocence_config.yaml` | `model_name` |
| Required variable name in `miner.py` | `model_name` |
| In the rendered canonical wrapper | `VOCENCE_REPO` |

All five must hold the same value. The audit verifies it.

### 4a. `vocence_config.yaml` must declare `model_name`

The file must contain a top-level `model_name` field whose value equals what you committed on chain:

```yaml
model_name: "your-hf-user/your-repo-name"
# ... your other runtime/generation settings below
```

If `model_name` is missing, malformed, or doesn't match the on-chain commitment, the chute refuses to start and the owner marks the miner invalid (`model_name_mismatch:yaml=...`).

### 4b. `from_pretrained` must use the `model_name` variable

Every call to `from_pretrained` in `miner.py` must take the bare `model_name` variable as its first positional argument (or as the `pretrained_model_name_or_path` keyword). No string literals, no other variables, no expressions like `model_name + "-large"`.

✅ Allowed:
```python
import yaml
from transformers import AutoModel, AutoProcessor, AutoTokenizer

class Miner:
    def __init__(self, repo_path):
        cfg = yaml.safe_load((repo_path / "vocence_config.yaml").open())
        model_name = cfg["model_name"]
        self.tok       = AutoTokenizer.from_pretrained(model_name)
        self.processor = AutoProcessor.from_pretrained(model_name)
        self.model     = AutoModel.from_pretrained(model_name)
        # Subfolders within your own repo are fine:
        self.vocoder   = AutoModel.from_pretrained(model_name, subfolder="vocoder")
```

❌ Rejected:
```python
self.m = AutoModel.from_pretrained("other-user/their-model")   # hardcoded string
self.m = AutoModel.from_pretrained(model_name + "-large")      # expression
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
- **Post-processing of your audio output** (waveform filtering, loudness normalization, denoising, format conversion). You may **NOT** modify the validator's `text` or `instruction` strings before feeding them to your model — they must be passed verbatim. Any rewriting, paraphrasing, expansion, normalization, or enrichment of the input prompt is **banned** (see section 8b.C).
- Multiple sub-models loaded via `from_pretrained(model_name, subfolder=...)` for things like vocoder + acoustic model + tokenizer.
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

`VOCENCE_REPO` is the wrapper's rendered copy of the same `model_name` you commit on chain and declare in `vocence_config.yaml` — see the naming table at the top of section 4. Both must match.

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
| 11 | `vocence_config.yaml` exists and its `model_name` equals the on-chain `model_name` | `vocence_config_missing` / `vocence_config_missing_model_name` / `model_name_mismatch:yaml=...` |
| 12 | `miner.py` passes the source audit (section 4) | `miner_py_missing` / `banned_call:...` / `banned_import:...` / `from_pretrained_must_use_model_name` |
| 13 | Per-tensor fingerprint computed; must not match any existing DB row (current OR historical, any miner) at ≥95% | `tensor_fingerprint_failed` / `tensor_clone_of_existing:<model>@<rev>:ratio=...` |

After all per-miner checks pass, the owner runs **two passes of duplicate detection** across miners. The rule is the same in both passes: earliest commit block keeps `is_valid = True`, later miners flip to invalid.

- **`model_hash` byte-equality** — catches lazy copies where weight files are bit-for-bit identical. Failure reason: `duplicate_model:earliest_uid=<uid>`.
- **Per-tensor fingerprint (audit time)** — when the owner audits any new `(model, revision)`, the computed fingerprint is compared against **every row already in the `repo_tensor_fingerprints` table** — current miners, historical entries, any owner. If the match ratio is ≥95% against any existing row, the new commit is rejected as `tensor_clone_of_existing:<model>@<rev>:ratio=...` and the fingerprint is **NOT** stored (the original row keeps its claim). First-stored-wins, permanently. This catches repackaging attacks (rename, re-shard, format conversion, non-LFS escape) and partial-copy attacks (clone most layers, tweak a few). The 95% threshold is calibrated for LoRA-style fine-tuning: standard LoRA recipes change 10–25% of tensors so honest independent fine-tunes on a shared base typically match each other at 75–90% and pass cleanly. A cheater who clones an existing miner must modify at least ~5% of tensors to slip through, which materially degrades the model output.

Checks 1–8 are the existing chute/HF/wrapper gates; **9–13 plus tensor-fingerprint dedup are the new model-pinning gates** described in section 4.

---

## 8. Prohibited conduct — what gets you rejected or blacklisted

Two enforcement levels apply. Read this section before deploying. **The owner reserves the right to blacklist any hotkey based on observed behavior; the list below is not exhaustive.**

### At-a-glance: everything that's disallowed

| Action | Consequence |
|---|---|
| Ship weights as anything other than `.safetensors` (no `.bin`/`.pt`/`.pth`/`.ckpt` etc.) | Auto-reject (`safetensors_missing`) |
| Combined `.safetensors` size under **50 MiB** | Auto-reject (`safetensors_below_min_size`) |
| Missing or wrong `model_name` in `vocence_config.yaml` | Auto-reject (`vocence_config_missing_model_name` / `model_name_mismatch:yaml=...`) |
| `model_revision` is a branch name / tag / `latest` instead of a 40-char hex SHA | Auto-reject (`wrapper_revision_not_sha`) → **blacklist** if repeated |
| Any commit at a block **before 8239720** (the `COMMIT_LOCK_BLOCK` cutover) | Ignored entirely — treated as if it never happened. A hotkey with only pre-cutover commits is invisible to the subnet. |
| More than **2 valid on-chain commits** with one hotkey at/after block **8239720** | Auto-reject (`too_many_commits`) → **blacklist** if repeated |
| `miner.py` calls any banned function — `snapshot_download`, `hf_hub_download`, `cached_download`, `pipeline`, `torch.hub.load`, `eval`, `exec`, `compile`, `__import__`, `import_module` | Auto-reject (`banned_call:...`) |
| `miner.py` imports any banned module — `requests`, `urllib*`, `httpx`, `aiohttp`, `socket`, `huggingface_hub`, `importlib`, `torch.hub` | Auto-reject (`banned_import:...`) |
| `from_pretrained` called with anything other than the bare `model_name` variable (no string literals, no expressions, no other variables) | Auto-reject (`from_pretrained_must_use_model_name`) |
| Modify the canonical wrapper beyond the 4 approved template variables | Auto-reject (`wrapper_hash_mismatch`) |
| Chute name (Chutes-side) missing the substring `vocence` | Auto-reject (`chute_name_missing_vocence`) |
| Chute not hot, repo or revision missing on Hugging Face | Auto-reject |
| Model byte-identical to an earlier miner (same `model_hash`) | Auto-reject (`duplicate_model:earliest_uid=<n>`) — later commit loses |
| Per-tensor fingerprint matches **any existing entry in the DB** (current OR historical, any miner, ≥95% match) | Auto-reject (`tensor_clone_of_existing:<model>@<rev>:ratio=...`). Checked at audit time; first-stored wins permanently — the rejected fingerprint is NOT stored, so the original keeps its claim. |
| **Copying another miner's weights in any form** — byte clone, repackage, rename/re-shard, format conversion, ε-noise, precision roundtrip, dtype conversion, partial-layer replacement | **Blacklist** |
| Registering multiple hotkeys serving the same effective model (Sybil) | **Blacklist** |
| Repeated attempts to land near-misses just below the 95% dedup threshold | **Blacklist** |
| Proxying `/speak` to another chute, returning pre-recorded audio, replaying cached outputs | **Blacklist** |
| Modifying the validator's `text` or `instruction` before passing them to your model (rewriting, paraphrasing, expansion, SSML injection — any "prompt enrichment") | **Blacklist** |
| Returning audio that doesn't match the requested `text` or doesn't reflect the requested traits | **Blacklist** |
| Serving different quality to different validator UIDs to game variance | **Blacklist** |
| Loading weights at runtime from any repo other than your declared `model_name` (monkey-patching, side-channel HTTP, etc.) | **Blacklist** |
| Bypassing the wrapper's `model_name == VOCENCE_REPO` check or the `miner.py` source audit | **Blacklist** |
| Pattern of commits whose only apparent purpose is probing the validation surface | **Blacklist** |
| Coordinating across multiple hotkeys you control | **Blacklist** |
| **Any form of cheating not listed above** — if it looks like gaming, it gets you blacklisted | **Blacklist** |

**Auto-reject** = `is_valid = False` until you fix and re-commit (recoverable). **Blacklist** = hotkey appended to `/blocklist/participants`; every future commit from that hotkey is permanently excluded (NOT recoverable). The detailed rules and the rejection / blacklist mechanics are in 8a–8d below.

### 8a. Automatic rejection (recoverable)

The checks in section 7 run **every 1 hour**. Any failure sets `is_valid = False` for your hotkey, which means validators give you zero scoring weight. You stay registered and can recover by fixing the issue and committing a new revision on chain — the next validation cycle picks up the change.

This is the soft path. It catches honest mistakes (forgot `vocence_config.yaml`, wrong `model_name`, banned import in `miner.py`, repo under 50 MiB, etc.) and lets you fix and try again.

### 8b. Manual blacklist (NOT recoverable)

A blacklisted hotkey is added to the owner's `/blocklist/participants` list. **Once blacklisted, every future commit from that hotkey is excluded from validation, scoring, and weight-setting — even if it passes every auto-check.** The blacklist is hotkey-scoped and permanent. Getting a new hotkey lets you start over, but the same conduct under a new identity will get the new hotkey blacklisted too.

The following behaviors will get your hotkey blacklisted:

#### A. Wrapper or `miner.py` tampering
- Deploying a chute whose wrapper differs from the canonical template in any way other than the four approved variables.
- `miner.py` that loads weights from a repo other than your declared `model_name`. This includes side-channel network calls, runtime monkey-patching of `from_pretrained`, or any code path that fetches model data from outside your declared repo.
- Attempting to bypass the wrapper's `model_name == VOCENCE_REPO` check or its `miner.py` source audit.

#### B. Model identity gaming
- **Copying another miner's weights, in any form, will get you blacklisted.** This includes: byte-identical clones, repackaged weights (rename/re-shard/format conversion), ε-noise perturbations, precision roundtrips, dtype conversion, partial-layer replacement, and any other technique whose end result is "this miner is shipping someone else's model with cosmetic changes."
- Registering multiple hotkeys whose models share ≥95% of tensors with each other (or with an earlier-registered miner). The duplicate detection in section 7 will auto-reject the later commit; **repeated attempts to register near-clones, including ones tuned to land just below the 95% threshold, will be blacklisted as bad-faith Sybil behavior.**

#### C. Input / output misconduct
- **Modifying the validator's input prompt before feeding it to your model.** The `text` and `instruction` arguments to `generate_wav(instruction, text)` must be passed verbatim to your TTS model. Any rewriting, paraphrasing, expansion, summarization, normalization, prosody-tagging, SSML injection, or other "prompt enrichment" of either string is banned — it lets miners with extra preprocessing pipelines game the judge unfairly. Miners may post-process their audio output, but the input prompt is immutable.
- Returning audio not generated by your declared model — e.g., proxying `/speak` to another miner's chute, returning pre-recorded clips, returning source audio with cosmetic perturbations.
- Caching outputs by `(text, instruction)` hash and returning identical bytes for repeated queries when your real model would produce different output.
- Serving intentionally degraded output to specific validator UIDs or returning different quality on repeated queries to game judging variance.
- Returning audio that doesn't actually correspond to the requested `text` (e.g., always returning the same clip regardless of input).

#### D. Anti-evasion / bad-faith conduct
- A pattern of commits whose only apparent purpose is probing the validation surface (repeated near-miss configurations, A/B testing the dedup threshold).
- Coordinating across multiple registered hotkeys you control (Sybil collusion).
- Anything else the owner judges to be in bad faith.

#### E. Chain commitment misconduct
- Using a branch name (`main`, `dev`, `master`, a tag, `latest`, etc.) instead of a 40-character hex commit SHA for `model_revision`. Branch names are mutable — they can point at different repo contents over time, defeating the whole point of pinning your audit to a specific commit. Auto-rejected as `wrapper_revision_not_sha:<value>`. Repeated attempts will be blacklisted.
- Making more than **2 on-chain commits** with a single hotkey at/after block **8239720** (the `COMMIT_LOCK_BLOCK` cutover). Commits at blocks before the cutover are ignored entirely — they do not count toward the cap and cannot be selected as the miner's current commitment, so a hotkey that only ever committed pre-cutover is invisible to the subnet. The cap is enforced on field-valid post-cutover commits only, so retrying invalid commits doesn't burn a slot — but spamming valid commits to game the audit cache or probe the validation surface will be blacklisted. Auto-rejected as `too_many_commits:N_post_cutover_max_2_after_block_8239720`.

**Any form of cheating not enumerated above is also blacklist-worthy.** The list reflects patterns we know about today; new evasion techniques will be added as they're observed, and behavior that's clearly designed to game the subnet will be blacklisted whether or not it fits an existing category. If you're considering whether something might be "technically allowed" because the rules don't explicitly forbid it — that's exactly the kind of thinking that gets you blacklisted.

### 8c. What happens when you're blacklisted

- Your hotkey is appended to the owner's blocklist within the next validation cycle (≤1 hour).
- Every validator's miner list excludes your hotkey from that point forward; you receive zero weight.
- Any pending evaluations involving your hotkey are discarded.
- There is no appeal process. The owner's blocklist is the ground truth.

### 8d. How to stay clear

- Ship a real `miner.py` that loads its declared weights from its declared repo. Don't try to be clever.
- Don't reuse another miner's weights, perturbed or otherwise. If you fine-tune from a top miner, do enough actual training that your tensor fingerprint diverges by at least ~5% — the 95% threshold is set so that real LoRA recipes pass cleanly.
- Return audio your declared model actually generated. Don't proxy, don't cache, don't replay.
- If you suspect you've hit an auto-rejection for a reason that doesn't apply to you, fix the obvious cause and re-commit. Don't probe.

---

## 9. API

- **GET /health** — Returns `status`, `hf_repo_id`, `hf_revision`, `model_loaded`, `sample_rate`, `adapter`.
- **POST /speak** — JSON: `{"instruction": "...", "text": "..."}`. Response: `audio/wav` (raw WAV bytes).

---

## 10. Example and references

- **example_repo/** — Mock HF repo layout: `miner.py`, `chute_config.yml`, `vocence_config.yaml`. Replace with your real engine and model files.
- **chute_template/** — Canonical Jinja2 template; render with your four variables and use as the deploy script.

All example data is placeholder only.
