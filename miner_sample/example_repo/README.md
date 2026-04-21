# Example Vocence HF repo (mock)

This folder is a **reference layout** for the Hugging Face repo miners must push. All content is mock.

## Required in your repo

| File | Description |
|------|-------------|
| `miner.py` | PromptTTS engine: class `Miner`, `__init__(path_hf_repo: Path)`, `warmup()`, `generate_wav(instruction, text)` → `(waveform, sample_rate)`. |
| `chute_config.yml` | Chute/Image/NodeSelector (base image, pip, GPU). Used at build time. |

Optional: `vocence_config.yaml` (sample_rate, limits) — read by your engine if present.

## Deploy flow

1. Push this layout (with your real `miner.py` and model artifacts) to a HF repo.
2. Render the chute template (see miner_sample/chute_template) with your `VOCENCE_REPO`, `VOCENCE_REVISION`, `VOCENCE_CHUTES_USER`, and **chute name** (`VOCENCE_CHUTE_ID` in the template). The chute name must contain "vocence" (case-insensitive) for owner validation; the chute_id you commit on chain is a UUID from Chutes.
3. Build: `chutes build <module>:chute --local` or `--wait`.
4. Deploy: `chutes deploy <module>:chute --accept-fee`.
5. Commit on chain: model_name, model_revision, chute_id (the UUID from Chutes).

## Wrapper integrity (owner check)

The **owner** (not validators) verifies your chute: they fetch the deploy script from the Chutes API, mask the four approved variables, hash, and compare to the canonical wrapper. If the script is unchanged except for those variables, you pass. Do not modify the wrapper logic.
