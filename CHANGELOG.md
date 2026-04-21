# Changelog

All notable changes to the Vocence subnet codebase are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.2] - 2026-03-20

### Added

- **Global consensus scoring across validator buckets**
  - Validators now fetch the valid miner list and active validator list from the owner API, read recent evaluation windows from all active validator buckets, and compute the same shared winner instead of scoring from only their own bucket.
  - Added shared global scoring helpers so validator-side weight setting and owner-side dashboard metrics use the same aggregation and winner-selection logic.

- **Active validator owner API**
  - Added owner-side active-validator discovery based on recent evaluation submissions so validators can restrict scoring to currently active validators.

- **Live subnet graph activity support**
  - Added short-lived graph activity leases for evaluation start, evaluation-result handoff, and weight-setting lifecycle events.
  - Added owner-side graph endpoints and persistence support so downstream dashboards can visualize live validator activity safely.

- **Global scoring snapshots**
  - Added owner-side snapshot persistence for global miner ranking, winner reasoning, threshold checks, and per-validator score breakdowns for dashboard consumption.

### Changed

- **Eligibility rule**
  - Updated global eligibility so a miner must have more than `MIN_EVALS_TO_COMPETE` evaluations in at least `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY` individual validator buckets.
  - With current defaults, a miner now needs more than `40` evaluations in at least `3` validators to become globally eligible.

- **Weight-setting flow**
  - Validators still set weights once per cycle window, but they now use shared active-validator inputs and stake-weighted global aggregation (`sqrt(stake)`) before applying the existing threshold rule.

- **Configuration and setup**
  - Standardized validator bucket credential loading through `VALIDATOR_BUCKETS_JSON` in `.env`.
  - Updated documentation around scoring, validator setup, and base-model / burn-key behavior.

### Fixed

- **Evaluation handoff transition**
  - Made evaluation-to-handoff graph activity transitions atomic so live dashboard consumers do not observe a gap where evaluation disappears before submission starts.

- **Determinism / consistency**
  - Reduced cross-validator scoring drift by aligning winner selection on the same valid miner set, active validator set, scoring window, and aggregation logic.

---

## [0.1.1] - 2026-03-19

### Added

- **Base miner**
  - Implemented the new base miner and integrated it into the current subnet flow.

### Changed

- **Scoring**
  - Refined the scoring mechanism and adjusted the scoring window.
  - Updated set-weights block number handling for better cycle execution behavior.

- **Operations / infrastructure**
  - Improved CI/CD workflows.
  - Improved Watchtower behavior.

### Fixed

- **Subtensor connection stability**
  - Fixed an issue where the subtensor connection could drop unexpectedly.

- **General maintenance**
  - Applied several smaller stability and maintenance fixes across the project.

---

## [0.1.0] - 2025-02-28

### Added

- **Voice intelligence subnet (Q1: PromptTTS)**  
  Bittensor subnet for development and evaluation of voice intelligence models (PromptTTS, STT, STS, voice cloning, etc.). Initial release focuses on PromptTTS: miners deploy models that generate speech from text + voice-trait instructions; validators score content correctness, audio quality, and prompt adherence.

- **Validator**
  - Sample generation loop: download audio from corpus, get transcription + voice traits via GPT audio model, query miners (Chutes `/speak`), run forced-choice evaluation, upload results to validator’s Hippius bucket.
  - Weight-setting loop: every `CYCLE_LENGTH` blocks, compute scores from last `MAX_EVALS_FOR_SCORING` evaluations, apply winner-take-all with “beat predecessors by threshold” rule, set weights on chain.
  - Defaults: sample every 10 minutes (`ASSESSMENT_INTERVAL=600`), scoring window 50 evals (`MAX_EVALS_FOR_SCORING=50`), miner must have more than 35 evals in window to be eligible (`MIN_EVALS_TO_COMPETE=36`).

- **Miner**
  - CLI: `vocence miner push` (deploy to Chutes), `vocence miner commit` (commit model + chute ID to chain). Optional `--network` and `--netuid` to override .env.
  - Canonical wrapper template (`chute_template/vocence_chute.py.jinja2`) and miner sample guide. Chute ID must contain the word `vocence` for owner validation.

- **Owner / centralized API**
  - HTTP API: participants (valid miners), evaluations submission, metrics, blocklist, status. Background workers: participant validation (HuggingFace + Chutes + wrapper integrity), metrics calculation.
  - Source audio downloader: LibriVox clips (20–25s, capped to validator max), upload to corpus bucket. Clip duration capped so validator never sees >25s.

- **Configuration**
  - Single config module (`vocence.domain.config`): loads `.env` on import, all defaults in one place. Mainnet default subnet 78; testnet subnet set via .env (`NETUID=XXX`).

### Changed

- N/A (initial release)

### Fixed

- N/A (initial release)

---

[0.1.2]: https://github.com/Vocence-bt/vocence/releases/tag/v0.1.2
[0.1.1]: https://github.com/Vocence-bt/vocence/releases/tag/v0.1.1
[0.1.0]: https://github.com/Vocence-bt/vocence/releases/tag/v0.1.0
