<p align="center">
  <img src="docs/vocence.png" alt="Vocence" width="640">
</p>

# <a href="https://vocence.ai" style="color: #DFFF00;">Vocence</a>

**Open, incentivized voice intelligence on Bittensor.**

Vocence is a Bittensor subnet focused on the development and evaluation of voice intelligence models, including Prompt-based Text-to-Speech (PromptTTS), Speech-to-Text (STT), Speech-to-Speech (STS), voice cloning, and other multimodal voice capabilities.

The network incentivizes miners to train and deploy models that follow natural-language prompts describing both content and voice traits such as gender, tone, emotion, pitch, speaking speed, age group, accent, and recording environment.

Validators evaluate how well models produce high-quality audio that matches both the requested content and the described voice characteristics.

## Current Focus (Q1)

The initial implementation focuses on **PromptTTS**.

Miners deploy PromptTTS models that generate speech from prompts like:

> "A calm middle-aged male voice with a warm tone, speaking slowly and clearly, reading the following sentence…"

Validators measure performance across three core dimensions:

- **Content correctness** – does the speech match the text
- **Audio quality** – clarity, naturalness, and absence of artifacts
- **Prompt adherence** – how accurately the voice matches the requested traits

This establishes the baseline evaluation pipeline that will later expand to additional voice tasks.

## What We're Building

Vocence creates a decentralized marketplace where:

- **Miners** deploy open voice models and compete on performance
- **Validators** run a shared evaluation pipeline to measure model quality
- **Rewards** are distributed based on measurable improvements

All models run through a standardized interface (canonical wrapper + Hugging Face repository) so outputs remain directly comparable across miners.

## Why Bittensor

Bittensor enables decentralized incentives without a central gatekeeper.

- Miners compete based on measurable model performance
- Validators are rewarded for correctly running evaluation pipelines
- Models remain open and reproducible

The subnet uses the Bittensor chain for registration, weight assignment, and incentives, while keeping model artifacts and evaluation data open.

Vocence integrates with other Bittensor infrastructure, including:

- **Chutes** for model deployment
- **Hippius** and other storage/compute layers as the ecosystem evolves

---

## Roles

| Role | What they do |
|------|----------------|
| **Miners** | Train PromptTTS models, publish them on Hugging Face, and deploy on [Chutes](https://chutes.ai) using the canonical Vocence wrapper. They expose a single `/speak` API (text + instruction → WAV). You can run miner workflows via the [CLI](docs/CLI.md#miner-commands) (`vocence miner push`, `vocence miner commit`) or follow [miner_sample](miner_sample/MINER_GUIDE.md) for the Chutes deploy. Rewards come from validator scores. |
| **Validators** | Pull the list of registered miners, call each miner's Chutes `/speak` endpoint with evaluation prompts, run the pre-defined scoring pipeline, and set weights on chain. Sample generation remains per-validator, but weight setting now uses **global consensus scoring**: each validator reads the most recent evaluation window from every **active validator** bucket, aggregates those results with **stake-weighted** scoring, and then applies the subnet's winner-take-all + threshold rule. Run the validator via **Docker + Watchtower** (recommended; see [validator-setup.md](docs/validator-setup.md)) or the [CLI](docs/CLI.md#validator-commands) (`vocence serve`). They need Chutes access (to hit miner chutes), the owner API (miners list, active validators, dashboard), and readonly access to other active validators' Hippius sample buckets. |

---

## Credentials at a glance

**Validators need:**

- **Bittensor:** `NETWORK`, `NETUID`, `WALLET_NAME`, `HOTKEY_NAME` (to run the validator and set weights).
- **Chutes:** `CHUTES_API_KEY` (or `CHUTES_AUTH_KEY`) — **must be granted by the Vocence team** so your validator is allowed to call miners' chutes.
- **Hippius:** `HIPPIUS_CORPUS_*` (read-only corpus, from owner); `HIPPIUS_VALIDATOR_*` (your own bucket for evaluation samples).
- **Validator bucket config:** `VALIDATOR_BUCKETS_JSON` in `.env`, containing readonly bucket credentials for every validator you may score against (`hotkey`, `bucket_name`, `access_key`, `secret_key`).
- **Owner API:** `API_URL` — endpoint of the Vocence owner service (miners, blocklist, evaluations, active validators, dashboard). **Provided by the Vocence team**

**Miners need:**

- **Chutes:** A Chutes account; you deploy your chute with `chutes build` / `chutes deploy` (see miner_sample). Your **chute name/ID must contain "vocence"** (any position) for owner validation. No extra API key needed for deployment; validators use their own key to call you.
- **Hugging Face:** A repo with your voice model engine (`miner.py`, PromptTTS in Q1), `chute_config.yml`, and optionally `vocence_config.yaml`; you render the canonical template with `VOCENCE_REPO`, `VOCENCE_REVISION`, `VOCENCE_CHUTES_USER`, `VOCENCE_CHUTE_ID`.
- **Bittensor:** Wallet (coldkey + hotkey) to register/commit on the subnet once your chute is live.

---

## Validator quick start

**To run a validator you must contact the Vocence team.** They will:

- Grant **Chutes permission** so your validator can access miners' chutes.
- Provide the **owner API endpoint** (`API_URL`) for miner list, blocklist, and evaluation submission (dashboard integration).
- Provide the **Hippius sub-bucket keys**

Then:

1. **Clone, env, and run with Docker (recommended)**

   ```bash
   git clone https://github.com/Vocence-bt/vocence
   cd vocence
   cp env.example .env
   # Edit .env: NETWORK, NETUID (78), WALLET_NAME, HOTKEY_NAME,
   # CHUTES_API_KEY, API_URL, Hippius keys, VALIDATOR_NAME, etc.
   # Edit VALIDATOR_BUCKETS_JSON with readonly access for active validator sample buckets.
   docker-compose up -d
   ```

   The stack runs the published validator image and **Watchtower**; when the team pushes a new image, your validator auto-updates. See **[docs/validator-setup.md](docs/validator-setup.md)** for details and **[docs/cicd-pipeline.md](docs/cicd-pipeline.md)** for how the image is built and published.

2. **Optional: run from source**

   ```bash
   uv sync
   uv run vocence serve
   ```

   For all validator CLI options (e.g. split generator vs weight setter), see [docs/CLI.md](docs/CLI.md#validator-commands).

---

## Validator scoring

Sample generation is still local to each validator: each validator downloads source audio, queries miners, evaluates results, uploads artifacts to its own Hippius bucket, and submits evaluation metadata to the owner API.

Weight setting is now **global and deterministic**:

- Validators fetch the current **valid miner list** from the owner API.
- Validators fetch the current **active validator list** from the owner API. A validator is considered active when it submitted evaluation data recently (default window: 24 hours).
- Validators load local readonly bucket credentials from `VALIDATOR_BUCKETS_JSON` in `.env`.
- For each active validator that also exists in `VALIDATOR_BUCKETS_JSON`, validators read the most recent scoring window from that validator's bucket (default: 50 evaluations).
- Miner win rates are aggregated across active validators using **stake-weighted scoring**, where each validator's influence is weighted by `sqrt(stake)` from the current metagraph.
- A miner is globally eligible only if it has more than **40 evaluations** in at least **3 active validator buckets**.
- The winner must still beat earlier eligible miners, including the owner base model when present, by `THRESHOLD_MARGIN`.
- If there are too few active validators, too few readable validator buckets, or no miner satisfies the consensus + margin rules, validators burn by setting weight `1.0` on UID `0`.

This makes honest validators converge on the same leader from the same cross-validator evidence instead of relying on a single validator's local bucket.

---

## Miner quick start

See the **miner_sample/** directory for the full flow (HF repo + Chutes wrapper). You can also use the [CLI](docs/CLI.md#miner-commands) to deploy and commit:

- **vocence miner push** — Deploy your model to Chutes (`--model-name`, `--model-revision`).
- **vocence miner commit** — Commit model + Chute ID to chain (`--chute-id`, wallet).

miner_sample contains:

- **MINER_GUIDE.md** — Repo layout, engine contract, approved variables, render/build/deploy, and owner-side wrapper integrity.
- **chute_template/** — Canonical Jinja2 template; render with your four variables.
- **example_repo/** — Example HF repo layout (mock miner.py, chute_config.yml, vocence_config.yaml).

Use **uv** for local tooling (e.g. `uv run vocence`); Chutes builds run in their own environment.

---


## Links

- **CLI reference:** [docs/CLI.md](docs/CLI.md) — All commands for validators, miners, and owners.
- **Scoring logic:** [docs/scoring.md](docs/scoring.md) — Global validator scoring, stake-weighted aggregation, eligibility, tie-breaks, and burn rules.
- **Validator setup (Docker + Watchtower):** [docs/validator-setup.md](docs/validator-setup.md) — Run the published image with auto-updates. [CI/CD pipeline](docs/cicd-pipeline.md) — How the image is built and published.
- **Miners:** [miner_sample/MINER_GUIDE.md](miner_sample/MINER_GUIDE.md)

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
