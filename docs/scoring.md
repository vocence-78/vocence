# Vocence Scoring and Winner Selection

This document describes how validators score miners and how the subnet picks the top miner for a weight-setting cycle.

---

## Overview

Vocence uses a two-stage validator flow:

1. **Sample generation**
   Each validator generates its own evaluation samples:
   - download random source audio from the corpus bucket
   - extract a task spec from the source audio via GPT-4o audio
   - call miners' `/speak` endpoints with that spec
   - extract the same fields from each miner's audio and score element-by-element against the spec
   - pairwise-compare each miner's audio vs the source for naturalness
   - upload metadata and artifacts to the validator's own Hippius bucket
   - submit evaluation metadata to the owner API

2. **Weight setting**
   Validators do **not** rank miners from only their own bucket. Instead, each validator:
   - fetches the current valid miner list from the owner API
   - fetches the current active validator list from the owner API
   - loads local readonly bucket credentials from `VALIDATOR_BUCKETS_JSON` in `.env`
   - reads the recent scoring window from every active validator bucket it can match
   - computes the same global stake-weighted scores
   - applies the same deterministic winner-selection rule
   - sets weights in the same cycle window

This design is intended to reduce drift between honest validators on a winner-takes-all subnet.

---

## How Tasks Are Generated

Each validator generates evaluation tasks from real source audio in the shared corpus bucket.

### Source audio selection

Validators pull source audio from the Hippius corpus bucket:

- bucket: `AUDIO_SOURCE_BUCKET`
- access: `HIPPIUS_CORPUS_*`

The validator selects a random `.wav` file from the corpus while avoiding recently used items in memory. The source file must pass the validator-side duration checks:

- minimum: `AUDIO_SOURCE_MIN_DURATION_SEC`
- maximum: `AUDIO_SOURCE_MAX_DURATION_SEC`

Default Q1 behavior expects source clips around 20 to 25 seconds.

### Task spec extraction

Vocence does not generate arbitrary text prompts. The task is derived directly from the selected source audio via a single pointwise GPT-4o-audio-preview call.

The judge returns a JSON object with these closed-enum fields (the **task spec**):

- `transcription` — exact words spoken
- `gender` — one of `male`, `female`, `neutral`
- `pitch` — one of `low`, `mid`, `high`
- `speed` — one of `slow`, `normal`, `fast`
- `age_group` — one of `child`, `young_adult`, `adult`, `senior`
- `emotion` — one of `neutral`, `happy`, `sad`, `angry`, `calm`, `excited`, `serious`, `fearful`
- `tone` — one of `warm`, `cold`, `friendly`, `formal`, `casual`, `authoritative`
- `accent` — one of `us`, `uk`, `au`, `in`, `neutral`, `other`

The spec is persisted in `metadata.prompt.spec` for auditability and for deterministic re-scoring.

### What miners receive

The spec is flattened into the canonical `/speak` request payload:

```json
{
  "text": "<transcription>",
  "instruction": "gender: <...> | pitch: <...> | speed: <...> | age_group: <...> | emotion: <...> | tone: <...> | accent: <...>"
}
```

- `text` is the spoken content to synthesize.
- `instruction` is the voice/style description inferred from the source audio.

---

## How Miner Outputs Are Evaluated

After miners return generated audio, the validator runs **two GPT-4o audio calls per miner in parallel**:

1. **Pointwise extraction** on the miner's audio — returns the same 8-field schema as the source spec.
2. **Pairwise naturalness** — GPT hears the source audio and the miner audio (presentation order randomized to neutralize position bias) and answers `FIRST` or `SECOND` for which clip sounds more natural as human speech.

The extracted miner fields are compared element-by-element against the spec. The pairwise naturalness result contributes one additional element.

### Scoring rules per element

| Element | Scoring rule |
|---|---|
| `script` | `1 − WER(spec.transcription, miner.transcription)`, clamped to `[0, 1]`. WER uses Levenshtein distance on lowercase word tokens. |
| `pitch`, `speed`, `age_group` | Ordinal enum. Exact match = `1.0`, off by one bucket = `0.5`, else `0.0`. |
| `gender`, `emotion`, `tone`, `accent` | Exact enum match. `1.0` if equal, `0.0` otherwise. |
| `naturalness` | `1.0` if the judge picks the miner clip as more natural, `0.0` otherwise. |

### Weighted sum

Each element's raw score is multiplied by its weight and summed. The weights sum to exactly `1.0`:

| Element | Weight |
|---|---|
| script | 0.30 |
| naturalness | 0.15 |
| gender | 0.10 |
| speed | 0.10 |
| emotion | 0.10 |
| age_group | 0.10 |
| pitch | 0.05 |
| accent | 0.05 |
| tone | 0.05 |
| **Total** | **1.00** |

Final per-evaluation score is a continuous value in `[0, 1]`. This is the primary ranking signal.

### Pass threshold

Each evaluation is tagged with a binary `generated_wins`:

- `generated_wins = true` when `score >= PASS_THRESHOLD`
- default: `PASS_THRESHOLD = 0.9`

**`generated_wins` is the ranking primitive.** Per-validator and global aggregates both use the binary win/lose signal derived from this threshold. The continuous `score` and `breakdown` are stored for diagnostics and dashboards but do not drive winner selection.

### Robustness

- **Position bias** on the naturalness call is neutralized by random order swap per call.
- **Trait extractor drift** (e.g. `"american"` instead of `"us"`) is silently coerced to the closed enum set via an alias map.
- **Judge temperature** is `0.0` for both extraction and pairwise calls — the signal should be as deterministic as the model allows.

### What gets stored

For each miner in an evaluation round, the validator stores:

- `score` — weighted final score in `[0, 1]`
- `generated_wins` — boolean pass flag (score ≥ `PASS_THRESHOLD`)
- `breakdown` — per-element `{expected, actual, score, weight}` rows, plus naturalness `{reasoning, presentation_order}`
- `extracted_traits` — raw traits pulled from the miner audio
- `naturalness` — full naturalness judge result

Stored as `metadata.json` in the validator's own bucket and summarized into `validator_evaluations` via the owner API.

---

## Active Validators

The owner API returns the list of **active validators**.

A validator is considered active when it has submitted evaluation data recently:

- source: `validator_evaluations.evaluated_at`
- default activity window: `ACTIVE_VALIDATOR_WINDOW_HOURS = 24`

Validators only score buckets that satisfy both:

- the validator hotkey is returned by the owner API as active
- the validator exists in `VALIDATOR_BUCKETS_JSON`

If too few active validators are available, the cycle burns.

---

## Validator Bucket Config

Each validator keeps readonly bucket credentials in `.env` via `VALIDATOR_BUCKETS_JSON`.

Each entry contains exactly:

- `hotkey`
- `bucket_name`
- `access_key`
- `secret_key`

---

## Scoring Window

For each active validator bucket, Vocence reads the most recent:

- `MAX_EVALS_FOR_SCORING` evaluations

Default:

- `MAX_EVALS_FOR_SCORING = 50`

The window is based on the most recent `metadata.json` objects in that bucket, sorted by evaluation id.

Only miners that are currently valid according to the owner API are included in scoring.

---

## Per-Validator Miner Stats

Within each validator bucket, each miner gets local stats:

- `total` — eval count in the scoring window
- `wins` — number of evals where `score >= PASS_THRESHOLD` (each eval is binary: win or lose)
- `win_rate = wins / total` — **binary win rate**, the ranking signal
- `score_sum` — sum of continuous scores across evals (diagnostic only)
- `mean_score = score_sum / total` — mean continuous score (diagnostic only)

Back-compat: if an evaluation record predates the pass-threshold gating and only has the binary `generated_wins` flag, the eval contributes `1` to `wins` when true and `1.0` to `score_sum` when true (else `0`).

A validator only contributes to a miner's global score when the miner has at least:

- `MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE`

Default:

- `MIN_EVALS_PER_VALIDATOR_FOR_GLOBAL_SCORE = 1`

---

## Stake-Weighted Global Score

For each active validator, the validator weight is derived from the current subnet metagraph:

- `validator_weight = sqrt(stake)`

If all validator stakes resolve to zero or are unavailable, scoring falls back to equal weights.

For each miner:

- take each contributing validator's binary `win_rate` (as defined above)
- combine them into a stake-weighted mean

Formula:

```text
global_win_rate(miner) =
  sum(sqrt(stake_v) * win_rate_v)
  /
  sum(sqrt(stake_v))
```

Only validators that actually contributed data for that miner are included in the denominator.

Additional aggregated values tracked per miner:

- `total` — total evaluations across contributing validators
- `wins` — total binary passes across contributing validators
- `validator_count` — number of contributing validators
- `weighted_evals` — stake-weighted evaluation volume, used as a tie-break
- `raw_win_rate = wins / total`

---

## Global Eligibility

A miner is **globally eligible** only if:

1. it has **more than** `MIN_EVALS_TO_COMPETE` evaluations in a validator bucket
2. that happens in at least `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY` individual validator buckets

Defaults:

- `MIN_EVALS_TO_COMPETE = 40`
- `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY = 3`

So with default settings, a miner must have **41+** evaluations in **at least 3 validators** to enter the eligible set.

Important distinction:

- `validator_count` = number of validators contributing to the weighted score at all
- `eligible_validator_count` = number of validators where that miner has more than `MIN_EVALS_TO_COMPETE` evaluations

Winner eligibility uses `eligible_validator_count`, not the summed global total.

---

## Ordering of Miners

Eligible miners are evaluated in chain commit order:

- earlier commit block = earlier participant

The owner base model is injected as a synthetic participant and is part of the same ordering logic when it has scoring data.

This preserves the subnet rule that later miners must beat earlier baselines by a threshold margin.

---

## Choosing the Top Miner

Once global scores are computed, Vocence selects the winner in this order:

1. Build the eligible miner set
2. For each eligible miner, check whether it beats every earlier scored eligible miner by `THRESHOLD_MARGIN`
3. Discard miners that fail that threshold check
4. Among the remaining candidates, pick the best one using deterministic tie-breaks

Threshold rule:

```text
candidate passes prior miner P if:
  candidate_global_win_rate >= P_global_win_rate + THRESHOLD_MARGIN
```

Default:

- `THRESHOLD_MARGIN = 0.02`

So a candidate must exceed each earlier eligible miner by at least 2 percentage points of win rate.

---

## Tie-Breaks

If more than one miner beats all earlier eligible miners, the winner is chosen by:

1. higher `global_win_rate`
2. higher `eligible_validator_count`
3. higher `weighted_evals`
4. earlier commit block
5. lexicographically smaller hotkey

This ordering is deterministic so honest validators using the same inputs converge on the same winner.

---

## Burn Conditions

Vocence burns for the cycle by setting weight `1.0` on burn UID `0` when any of these happen:

1. Fewer than `MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING` validators are returned as active by the owner API
2. Fewer than `MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING` active validators can be matched locally in `VALIDATOR_BUCKETS_JSON`
3. No usable global scoring data is available across active validator buckets
4. No miner satisfies global eligibility
5. No globally eligible miner beats earlier eligible miners by `THRESHOLD_MARGIN`

Default:

- `MIN_ACTIVE_VALIDATORS_FOR_GLOBAL_SCORING = 3`

Burning is preferred over setting inconsistent non-burn weights from weak or partial evidence.

---

## Exact Timing

Validators do not set weights at one exact block. They set within the same cycle window:

- target cycle block ± `CYCLE_BLOCK_TOLERANCE`

As long as honest validators:

- use the same cycle window
- see the same valid miner list
- see the same active validator list
- use the same bucket config for active validators
- read the same recent bucket data
- use the same metagraph stake snapshot semantics

they should compute the same winner within that window.

---

## Cost Profile

Per evaluation round:

- **1** GPT-4o-audio pointwise call on the source audio (task spec extraction)
- **2N** GPT-4o-audio calls for N successful miners (pointwise extraction + pairwise naturalness, run in parallel per miner)

Judge concurrency is capped at `MAX_PARALLEL_EVALS` (default 4). Miner `/speak` concurrency is capped at `MAX_PARALLEL_MINERS` (default 20).

---

## Practical Notes

- Sample generation is still decentralized and validator-local.
- Weight setting is global and consensus-oriented.
- Validators can be active in the owner API but missing from `VALIDATOR_BUCKETS_JSON`; in that case they do not silently count as usable input.
- Missing or unreadable bucket data reduces usable coverage and can cause a burn.
- The scoring system intentionally prioritizes deterministic convergence over always forcing a non-burn winner.
- `ELEMENT_WEIGHTS` and `PASS_THRESHOLD` must be identical across all honest validators for consensus; tune with care.

---

## Related Files

- [`vocence/pipeline/evaluation.py`](../vocence/pipeline/evaluation.py) — trait extraction, element scoring, pairwise naturalness, weights, pass threshold
- [`vocence/pipeline/generation.py`](../vocence/pipeline/generation.py) — per-round orchestration
- [`vocence/ranking/calculator.py`](../vocence/ranking/calculator.py) — per-validator aggregation from bucket
- [`vocence/ranking/global_scoring.py`](../vocence/ranking/global_scoring.py) — cross-validator weighted mean + winner selection
- [`vocence/engine/coordinator.py`](../vocence/engine/coordinator.py) — validator main loop
- [`vocence/validator_buckets.py`](../vocence/validator_buckets.py) — bucket config parsing
- [`docs/validator-setup.md`](validator-setup.md)
