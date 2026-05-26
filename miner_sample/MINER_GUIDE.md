# Vocence Miner Guide

Vocence is a voice intelligence subnet. Miners fine-tune the **Qwen3 1.7B 12Hz voice-design model** and deploy it as a Chute using the **canonical wrapper** template. Your fine-tuned weights and config live in a Hugging Face repo; you render the template with your repo/revision and Chutes user/name, then build and deploy.

---

## 1. Prerequisites

- Chutes developer account and API key.
- Hugging Face account (to host the miner repo).

---

## 2. Repo contents (Hugging Face)

Your HF repo must contain **exactly** the files listed below. No additional files are allowed — the owner validates the manifest and rejects repos with extra or missing files.

| File | Required | Replaceable | Description |
|------|----------|-------------|-------------|
| `miner.py` | Yes | **No (locked)** | Canonical inference script. Copy from `miner_sample/example_repo/miner.py` — must be byte-identical. |
| `model.safetensors` | Yes | Yes | Main model weights (fine-tuned). |
| `config.json` | Yes | Yes | Model configuration. |
| `generation_config.json` | Yes | Yes | Generation parameters. |
| `merges.txt` | Yes | Yes | Tokenizer merges. |
| `preprocessor_config.json` | Yes | Yes | Preprocessor config. |
| `tokenizer_config.json` | Yes | Yes | Tokenizer config. |
| `vocab.json` | Yes | Yes | Vocabulary. |
| `vocence_config.yaml` | Yes | Yes | Must declare `model_name` matching on-chain commitment. Runtime/generation options. |
| `chute_config.yml` | Yes | Yes | Image, NodeSelector, Chute config for build. |
| `speech_tokenizer/model.safetensors` | Yes | Yes | Speech tokenizer weights. |
| `speech_tokenizer/config.json` | Yes | Yes | Speech tokenizer config. |
| `speech_tokenizer/configuration.json` | Yes | Yes | Speech tokenizer additional config. |
| `speech_tokenizer/preprocessor_config.json` | Yes | Yes | Speech tokenizer preprocessor config. |
| `.gitattributes` | No | — | Standard git metadata (auto-generated). |
| `.gitignore` | No | — | Standard git metadata. |
| `README.md` | No | Yes | Optional repo description. |

**What you can change:** Fine-tune the Qwen3 1.7B 12Hz model and replace `model.safetensors`, `speech_tokenizer/` files, config files, and tokenizer files with your fine-tuned versions. You may also adjust `vocence_config.yaml` for runtime settings and `chute_config.yml` for deployment config.

**What you cannot change:** `miner.py` is locked and must be byte-identical to the canonical version. You cannot add any files not listed above. You cannot use a different model architecture — the locked `miner.py` loads Qwen3 TTS exclusively.

Combined `.safetensors` size must be at least **50 MiB**.

---

## 3. Engine contract

- **Constructor:** `Miner(path_hf_repo: Path)` — load config/weights from this path only.
- **warmup()** — Optional; one short `generate_wav` so the first request does not time out.
- **generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]** — Return mono float32 PCM and sample rate.

The wrapper has already downloaded your repo and the HF cache is populated before your `__init__` runs, so `from_pretrained(model_name)` resolves from disk without hitting the network.

---

## 4. `miner.py` rules

**`miner.py` is locked.** All miners must ship the exact canonical `miner.py` — byte-for-byte identical. Copy it from `miner_sample/example_repo/miner.py` in this repository into your HF repo. Do not modify it in any way. This is enforced by SHA-256 hash check both owner-side at registration and at chute startup by the canonical wrapper.

The locked `miner.py` loads the Qwen3 1.7B 12Hz voice-design model from the local repo snapshot and runs inference using the standard `generate_voice_design(text, language, instruct)` API. Miners cannot add audio pre-processing, post-processing, speaker embeddings, or use alternative models — the inference path is fully determined by the locked script.

### 4a. `vocence_config.yaml` must declare `model_name`

The file must contain a top-level `model_name` field whose value equals what you committed on chain:

```yaml
model_name: "your-hf-user/your-repo-name"
runtime:
  adapter: "qwen3_tts_repo_snapshot"
  device_preference: "cuda"
  dtype: "bfloat16"
  default_language: "English"
  use_flash_attention_2: false
generation:
  sample_rate: 24000
  max_seconds: 30
limits:
  max_text_chars: 2000
  max_instruction_chars: 600
  default_language: "English"
```

If `model_name` is missing, malformed, or doesn't match the on-chain commitment, the chute refuses to start and the owner marks the miner invalid.

### 4b. What you can tune

- **Model weights**: Fine-tune the Qwen3 1.7B 12Hz model and replace `model.safetensors` with your fine-tuned version.
- **Speech tokenizer**: Replace `speech_tokenizer/` files if your fine-tuning modifies the speech tokenizer.
- **Config files**: Adjust `config.json`, `generation_config.json`, tokenizer files as needed for your fine-tuned model.
- **Runtime settings**: Adjust `vocence_config.yaml` for device preference, dtype, attention implementation, language defaults, and generation limits.
- **Deployment config**: Adjust `chute_config.yml` for GPU requirements, dependencies, and scaling.

### 4c. What you cannot do

- **Modify `miner.py`** — it must be the canonical version. Any modification is rejected (`miner_py_hash_mismatch`).
- **Add extra files** to the repo — only the files in the manifest (section 2) are allowed (`extra_files:...`).
- **Use a different model architecture** — the locked `miner.py` imports and loads Qwen3 TTS exclusively.
- **Add audio pre/post-processing** — the locked inference script feeds prompts directly to the model and returns the output as-is.
- **Inject speaker embeddings** — the locked inference path does not use them even if present in the weights.
- **Use other approaches** — the locked `miner.py` defines the only allowed inference path.

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
| 7 | Repo contains `.safetensors` files | `safetensors_missing` |
| 8 | Combined size of `.safetensors` files ≥ **50 MiB** | `safetensors_below_min_size:<actual><threshold>` |
| 9 | **File manifest: repo contains only allowed files and all required files are present** | `extra_files:...` / `missing_required_files:...` |
| 10 | `vocence_config.yaml` exists and its `model_name` equals the on-chain `model_name` | `vocence_config_missing` / `model_name_mismatch:yaml=...` |
| 11 | **`miner.py` canonical hash check: must be byte-identical to the locked version** | `miner_py_missing` / `miner_py_hash_mismatch` |
| 12 | Per-tensor fingerprint computed; must not match any existing DB row (current OR historical, any miner) at ≥95% | `tensor_fingerprint_failed` / `tensor_clone_of_existing:<model>@<rev>:ratio=...` |

After all per-miner checks pass, the owner runs **two passes of duplicate detection** across miners. The rule is the same in both passes: earliest commit block keeps `is_valid = True`, later miners flip to invalid.

- **`model_hash` byte-equality** — catches lazy copies where weight files are bit-for-bit identical. Failure reason: `duplicate_model:earliest_uid=<uid>`.
- **Per-tensor fingerprint (audit time)** — when the owner audits any new `(model, revision)`, the computed fingerprint is compared against **every row already in the `repo_tensor_fingerprints` table**. If the match ratio is ≥95% against any existing row, the new commit is rejected and the fingerprint is **NOT** stored (first-stored-wins). This catches repackaging attacks and partial-copy attacks.

---

## 8. Prohibited conduct — what gets you rejected or blacklisted

Two enforcement levels apply. Read this section before deploying. **The owner reserves the right to blacklist any hotkey based on observed behavior; the list below is not exhaustive.**

### At-a-glance: everything that's disallowed

| Action | Consequence |
|---|---|
| Ship weights as anything other than `.safetensors` (no `.bin`/`.pt`/`.pth`/`.ckpt` etc.) | Auto-reject (`safetensors_missing`) |
| Combined `.safetensors` size under **50 MiB** | Auto-reject (`safetensors_below_min_size`) |
| **Modify `miner.py`** — it must be byte-identical to the canonical locked version | Auto-reject (`miner_py_hash_mismatch`) |
| **Add extra files** to the repo not in the file manifest | Auto-reject (`extra_files:...`) |
| **Missing required files** from the file manifest | Auto-reject (`missing_required_files:...`) |
| Missing or wrong `model_name` in `vocence_config.yaml` | Auto-reject (`vocence_config_missing_model_name` / `model_name_mismatch:yaml=...`) |
| `model_revision` is a branch name / tag / `latest` instead of a 40-char hex SHA | Auto-reject (`wrapper_revision_not_sha`) → **blacklist** if repeated |
| Any commit at a block **before 8239720** (the `COMMIT_LOCK_BLOCK` cutover) | Ignored entirely — treated as if it never happened. |
| More than **2 valid on-chain commits** with one hotkey at/after block **8239720** | Auto-reject (`too_many_commits`) → **blacklist** if repeated |
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

This is the soft path. It catches honest mistakes (modified `miner.py`, extra files in repo, forgot `vocence_config.yaml`, wrong `model_name`, repo under 50 MiB, etc.) and lets you fix and try again.

### 8b. Manual blacklist (NOT recoverable)

A blacklisted hotkey is added to the owner's `/blocklist/participants` list. **Once blacklisted, every future commit from that hotkey is excluded from validation, scoring, and weight-setting — even if it passes every auto-check.** The blacklist is hotkey-scoped and permanent. Getting a new hotkey lets you start over, but the same conduct under a new identity will get the new hotkey blacklisted too.

The following behaviors will get your hotkey blacklisted:

#### A. Wrapper or `miner.py` tampering
- Deploying a chute whose wrapper differs from the canonical template in any way other than the four approved variables.
- Attempting to modify, replace, or bypass the locked canonical `miner.py`.
- Attempting to bypass the wrapper's `model_name == VOCENCE_REPO` check or the `miner.py` hash check.

#### B. Model identity gaming
- **Copying another miner's weights, in any form, will get you blacklisted.** This includes: byte-identical clones, repackaged weights (rename/re-shard/format conversion), ε-noise perturbations, precision roundtrips, dtype conversion, partial-layer replacement, and any other technique whose end result is "this miner is shipping someone else's model with cosmetic changes."
- Registering multiple hotkeys whose models share ≥95% of tensors with each other (or with an earlier-registered miner). The duplicate detection in section 7 will auto-reject the later commit; **repeated attempts to register near-clones, including ones tuned to land just below the 95% threshold, will be blacklisted as bad-faith Sybil behavior.**

#### C. Input / output misconduct
- **Modifying the validator's input prompt before feeding it to your model.** The locked `miner.py` passes `text` and `instruction` directly to the model without modification. Any attempt to intercept or modify these inputs is banned.
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

- Use the canonical `miner.py` exactly as provided. Do not modify it.
- Only include files from the allowed manifest. Do not add extra files.
- Fine-tune the Qwen3 1.7B 12Hz model and ship your own weights. Don't reuse another miner's weights.
- Return audio your declared model actually generated. Don't proxy, don't cache, don't replay.
- If you suspect you've hit an auto-rejection, fix the obvious cause and re-commit. Don't probe.

---

## 9. API

- **GET /health** — Returns `status`, `hf_repo_id`, `hf_revision`, `model_loaded`, `sample_rate`, `adapter`.
- **POST /speak** — JSON: `{"instruction": "...", "text": "..."}`. Response: `audio/wav` (raw WAV bytes).

---

## 10. Example and references

- **example_repo/** — Contains the canonical locked `miner.py` and example config files. Copy this as the starting point for your repo, then add your fine-tuned weights.
- **chute_template/** — Canonical Jinja2 template; render with your four variables and use as the deploy script.
