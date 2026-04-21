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
| `vocence_config.yaml` | No | PromptTTS options (sample_rate, limits). Read by your engine if present. |

All engine logic must live in `miner.py`; only stdlib and site-packages may be imported (no other repo files).

---

## 3. Engine contract

- **Constructor:** `Miner(path_hf_repo: Path)` — load config/weights from this path only.
- **warmup()** — Optional; one short `generate_wav` so the first request does not time out.
- **generate_wav(instruction: str, text: str) -> tuple[np.ndarray, int]** — Return mono float32 PCM and sample rate.

---

## 4. Approved template variables (only these)

The canonical wrapper is generated from the template in `chute_template/`. You may change **only**:

| Variable | Meaning |
|---------|--------|
| `VOCENCE_REPO` | Hugging Face repo ID (e.g. `your-username/your-tts-repo`). |
| `VOCENCE_REVISION` | Repo revision; **commit hash strongly recommended**. |
| `VOCENCE_CHUTES_USER` | Chutes username. |
| `VOCENCE_CHUTE_ID` | Chute name you give when deploying (e.g. `vocence-tts-001`). **Must contain `vocence`** (case-insensitive); the owner checks the chute **name** from the Chutes API, not the chute_id (UUID) on chain. |

All other wrapper code is fixed. Changing anything else causes **wrapper integrity** to fail (see below).

---

## 5. Render, build, deploy

1. Copy the template from `chute_template/vocence_chute.py.jinja2` and replace the placeholders:
   - `{{ huggingface_repository_name }}` → your repo ID  
   - `{{ huggingface_repository_revision }}` → your commit hash  
   - `{{ chute_username }}` → your Chutes user  
   - `{{ chute_name }}` → your **chute name** (the name you give the deployment in Chutes; must contain **vocence** somewhere, case-insensitive; e.g. `vocence-parler-tts-010`, `my-vocence-tts`). This is the deployment name in Chutes, not the chute_id (UUID) you commit on chain.  

2. Build: `chutes build <your_module>:chute --local` (or `--wait` for remote).

3. Deploy: `chutes deploy <your_module>:chute --accept-fee`.

4. Commit on chain: `model_name`, `model_revision`, `chute_id` (the UUID from Chutes; via CLI or your flow). The **chute name** (step 1) is what must contain "vocence"; the chute_id on chain is a UUID and is not checked for the magic word.

---

## 6. Wrapper integrity (owner, not validators)

**The owner** (central service) verifies that your deployed chute uses the canonical wrapper:

1. Owner fetches your **deploy script** from the Chutes API (`GET /chutes/code/{chute_id}`).
2. If the fetch fails → participant marked invalid (`chute_code_fetch_failed`).
3. Owner masks the four approved variables (replaces their values with a placeholder), normalizes the source (AST), and hashes it.
4. Owner compares this hash to the hash of the canonical template (same masking). If they differ → invalid (`wrapper_hash_mismatch`).

Validators **do not** perform this check. They only call your `/health` and `/speak` endpoints for scoring. So: keep the wrapper unchanged except for the four variables; the owner will confirm that and set your participant validity accordingly.

---

## 7. API

- **GET /health** — Returns `status`, `hf_repo_id`, `hf_revision`, `model_loaded`, `sample_rate`, `adapter`.
- **POST /speak** — JSON: `{"instruction": "...", "text": "..."}`. Response: `audio/wav` (raw WAV bytes).

---

## 8. Example and references

- **example_repo/** — Mock HF repo layout: `miner.py`, `chute_config.yml`, `vocence_config.yaml`. Replace with your real engine and model files.
- **chute_template/** — Canonical Jinja2 template; render with your four variables and use as the deploy script.

All example data is placeholder only.
