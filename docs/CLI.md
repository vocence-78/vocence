# Vocence CLI Reference

## Overview

The Vocence CLI (`vocence`) is the single entry point for:

- **Validators:** run the full validator (`serve`) or split services.
- **Owners:** run the HTTP API (dashboard/metrics).
- **Miners:** deploy models to Chutes and commit to the chain.
- **Queries:** list miners.

Entry point: `vocence` (from `pyproject.toml`: `vocence = "vocence.gateway.cli.main:cli"`). Run with `uv run vocence` or after `pip install -e .`.

---

## Global options

- `--help` — Show help for the command or group.
- `--version` — Show version (e.g. `vocence --version`).

---

## Validator commands

### `vocence serve`

**Purpose:** Run the full validator in one process (recommended for normal operation).

**What it runs:**

1. **Sample generation** (background): downloads audio from corpus, gets transcription + voice traits via GPT, queries miners (Chutes), runs forced-choice evaluation, uploads samples to the validator’s Hippius bucket and submits to the API.
2. **Weight setting** (foreground): every `CYCLE_LENGTH` blocks, fetches the valid miner list and active validator list from the owner API, reads the recent scoring window from all active validator buckets listed in `VALIDATOR_BUCKETS_JSON`, computes a stake-weighted global score, picks a leader (winner-take-all + threshold), and sets weights on chain.

**Usage:**

```bash
vocence serve
```

**Requires:** `CHUTES_AUTH_KEY`, `OPENAI_AUTH_KEY`, wallet (e.g. `COLDKEY_NAME`, `HOTKEY_NAME`), chain config, corpus + validator Hippius credentials, API URL (`API_URL`), and `VALIDATOR_BUCKETS_JSON` for readonly access to active validator sample buckets.

---

### `vocence api`

**Purpose:** Run only the centralized HTTP API (one process).

**Endpoints:** participants, evaluations, metrics, blocklist, validators, status. Background workers: participant validation, metrics calculation. Uses DB and env for service host/port.

**Usage:**

```bash
vocence api
```

**Env:** `SERVICE_HOST` (default `0.0.0.0`), `SERVICE_PORT` (default `8000`), DB and API config as per app.

**Typical use:** Run the dashboard/metrics API. It is not required for validators to function — validators run independently of it.

---

### `vocence services generator`

**Purpose:** Run only the sample generation loop (no weight setting). For scaling: multiple generators can feed one validator’s bucket.

**Usage:**

```bash
vocence services generator
```

**Requires:** Same as sample-generation side of `serve` (local corpus + validator storage, API, Chutes, OpenAI). Starts its own local corpus manager in the background.

---

### `vocence services validator`

**Purpose:** Run only the weight-setting loop (no sample generation). Use with a separate generator (or generators) elsewhere.

**Usage:**

```bash
vocence services validator
```

**Requires:** Wallet, chain config, API for participant list + active validator list, and `VALIDATOR_BUCKETS_JSON` for readonly access to active validator sample buckets.

---

### `vocence services corpus`

**Purpose:** Run only the local audio corpus manager. Continuously downloads English LibriVox clips (20-25s) into `CORPUS_LOCAL_DIR` and prunes the oldest clips beyond `AUDIO_CORPUS_MAX_ENTRIES`. Use this when generation and corpus upkeep are split across processes; `serve` and `services generator` already run it in the background.

**Usage:**

```bash
vocence services corpus
```

**Requires:** Disk for `CORPUS_LOCAL_DIR` and `ffmpeg`. No Hippius/corpus-bucket credentials — the corpus is per-validator and local.

---

### `vocence services registry`

**Purpose:** Run only the local miner registry. Validates miners from on-chain commitments (HuggingFace + Chutes + duplicate detection) on block-aligned boundaries and writes the valid set to the local SQLite DB (`REGISTRY_DB_PATH`); also mirrors the centralized blacklist (cached). Use this when splitting services; `serve`, `services generator`, and `services validator` already run it in the background.

**Usage:**

```bash
vocence services registry
```

**Requires:** Chain RPC, `CHUTES_API_KEY` (chute liveness), disk for `REGISTRY_DB_PATH`. `HF_AUTH_TOKEN` optional.

---

## Query commands

### `vocence get-miners`

**Purpose:** List miners and their committed Chutes (from chain).

**Usage:**

```bash
vocence get-miners
```

**Requires:** `SUBNET_ID`, `CHAIN_NETWORK` (and Bittensor/chain access).

---

## Miner commands

For subnet miners: deploy voice models to Chutes and commit model info to the chain. The current implementation (Q1) uses PromptTTS; the canonical wrapper (template, `miner.py`, `chute_config.yml`) and owner-side wrapper integrity (hash check) are described in **miner_sample/** (see MINER_GUIDE.md). **Your chute name** (the deployment name you give in Chutes, e.g. `vocence-parler-tts-010`) **must contain the word `vocence`** (any position, case-insensitive) for owner validation to pass; the Chute ID you commit on chain is a UUID and is not checked for this.

### `vocence miner push`

**Purpose:** Deploy a voice model (PromptTTS in Q1) to Chutes.

**Usage:**

```bash
vocence miner push --model-name <repo> --model-revision <sha>
vocence miner push --model-name user/model --model-revision abc123 --chutes-api-key KEY
```

**Options:**

| Option            | Required | Description |
|-------------------|----------|-------------|
| `--model-name`    | Yes      | HuggingFace repository ID (e.g. `user/model-name`). |
| `--model-revision`| Yes      | HuggingFace commit SHA. |
| `--chutes-api-key`| No       | Chutes API key (else `CHUTES_AUTH_KEY`). |
| `--chute-user`    | No       | Chutes username (else `CHUTE_USER`). |

---

### `vocence miner commit`

**Purpose:** Commit model info (model name, revision, Chute ID) to the blockchain.

**Network and subnet:** By default the command uses `NETWORK` / `NETUID` (or `CHAIN_NETWORK` / `SUBNET_ID`) from your `.env`. Default is mainnet: `NETWORK=finney`, `NETUID=78`. For testnet set `NETWORK=test` and `NETUID=XXX` in `.env` (replace XXX with the testnet subnet id), or pass `--network finney --netuid 78` to override for this run.

**Usage:**

```bash
vocence miner commit --model-name <repo> --model-revision <sha> --chute-id <id>
vocence miner commit --model-name user/model --model-revision abc123 --chute-id chute-xxx --network finney --netuid 78
```

**Options:**

| Option            | Required | Description |
|-------------------|----------|-------------|
| `--model-name`    | Yes      | HuggingFace repository ID. |
| `--model-revision`| Yes      | HuggingFace commit SHA. |
| `--chute-id`      | Yes      | Chutes deployment ID from `miner push`. |
| `--network`       | No       | Chain network: `finney` (mainnet), `test` (testnet). Overrides .env. |
| `--netuid`        | No       | Subnet ID (default mainnet 78; testnet set via .env). Overrides .env. |
| `--coldkey`       | No       | Wallet coldkey (else `COLDKEY_NAME`). |
| `--hotkey`        | No       | Wallet hotkey (else `HOTKEY_NAME`). |

---

## Command summary

| Command | Role / use |
|---------|------------|
| `vocence serve` | Validator: full run (generator + weight setter). |
| `vocence api` | HTTP API only (dashboard/metrics; single process). |
| `vocence services generator` | Validator: sample generation only (runs local corpus manager too). |
| `vocence services validator` | Validator: weight setting only. |
| `vocence services corpus` | Validator: local audio corpus manager only. |
| `vocence services registry` | Validator: local miner registry (validation) only. |
| `vocence get-miners` | Query: list miners from chain. |
| `vocence miner push` | Miner: deploy model to Chutes. |
| `vocence miner commit` | Miner: commit model + Chute ID to chain. |

---

## Keeping this doc up to date

1. **After adding a new command or group:** add a section under the right role (Validator / Owner / Query / Miner) and add a row to the Command summary table.
2. **After adding or changing options:** update the options table and any usage examples for that command.
3. **After renaming or removing a command:** remove or rename the section and update the summary table.
4. **Verify:** run `vocence --help`, `vocence services --help`, `vocence services corpus --help`, etc., and confirm the doc matches the help output.

Source of truth for options and help text remains `vocence/gateway/cli/main.py`; this document is the human-readable reference and should stay in sync with it.
