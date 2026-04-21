# Base model and burn-key protocol (owner ↔ validators)

This document describes how the owner and validators integrate around **base models** (owner-deployed reference models, e.g. qwen3-voice-design) and the **burn key** when no miner is eligible.

**Aim:** Prevent miners from using the base model to win. The base model is a normal participant in every step (validators send requests to it, evaluate it, include it in scoring) but with priority: it is treated as committed at block 1000, so any miner must beat its win rate by the threshold margin (e.g. 5%) to become the winner.

## Concepts

- **Base model**: A model run by the owner in a Chute (e.g. qwen3-voice-design). It is considered “committed” at a fixed block (e.g. 1000) so that miners must beat it by the threshold margin (e.g. 5%) to win. There can be multiple base-model Chutes. Validators send synthesis requests to the base model chute every sample round and include it in evaluations and scoring like any other participant.
- **Burn key**: On Bittensor, **UID 0** is the burn key. Setting weight 1 on UID 0 sends incentives to burn (no miner receives them).
- **Eligible miner**: A miner with more than `MIN_EVALS_TO_COMPETE` evaluations in at least `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY` validator buckets. Only eligible miners can win; if none are eligible, validators set weight 1 on UID 0 (burn).

## Owner side

### Configuration (in code)

The owner **never commits on chain**. All owner/base-model data is in config. In `vocence/domain/config.py` the owner sets (for transparency, in code rather than `.env`):

- **`OWNER_UID`** (0), **`OWNER_HOTKEY`**, **`BASE_MODEL_CHUTE_ID`**, **`BASE_MODEL_MODEL_NAME`**, **`BASE_MODEL_MODEL_REVISION`**, **`BASE_MODEL_COMMIT_BLOCK`** (e.g. 1000). Replace mock values with real.
- **`BASE_MODEL_COMMIT_BLOCK`**: Block at which base model(s) are considered committed (e.g. 1000). This is the block the owner uses when registering the base model on chain (manually); other miners cannot use a base model because they would need to beat it by the margin and the base model is “first” by block.

### Participant validation

When the owner runs participant validation (gateway task that syncs metagraph and validates each miner):

- For any participant whose **`chute_id`** equals **`BASE_MODEL_CHUTE_ID`**, the owner **skips all validation checks** and marks that participant as **valid**. So the owner’s hotkey (the one that registered that chute) is always valid for that commitment.
- All other participants are validated as before (chute fetch, wrapper integrity, chute hot, revision match, model fingerprint, duplicate detection, etc.).

So: validators see the same participant list as before; base-model UIDs appear as valid participants with their (early) commit block and chute_id. No separate “strange UID” list is required—the only special case validators need is “no eligible miner → burn.”

## Validator side

### Sample generation and scoring (base model included)

- Validators get the list of **valid participants** from the owner API (GET /participants/valid). This list **includes** the base model(s): same hotkey, chute_id, block (e.g. 1000), etc.
- On each sample round, validators **send synthesis requests to every valid participant's chute**, including the owner's base-model chute. So the owner chute is called every round like any other miner chute; responses are evaluated (GPT-4o forced-choice) and stored in the validator's S3 bucket.
- When setting weights, validators use the same participant list. Participants are ordered by commit block (ascending). The base model (block 1000) is **first** in that order. The "beat predecessors by threshold" rule applies: to win, a miner must have more than `MIN_EVALS_TO_COMPETE` evals in at least `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY` validator buckets and beat **every** earlier participant by 5%. So miners must beat the base model's win rate by 5%—they cannot win by just running the same base model.

### Weights and winner selection

- Validators compute scores from their own S3 bucket and apply the existing rules:
  - Order participants by commit block (ascending).
  - **Eligible** = participants with more than `MIN_EVALS_TO_COMPETE` evals in at least `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY` validator buckets.
  - If **no eligible miner**: do **not** assign a miner as winner. Set weights so **UID 0** has weight **1** and all others **0** (i.e. call `set_weights` with `uids=[0]`, `weights=[1.0]`). All incentives are burned.
  - If there is at least one eligible miner: choose the winner with the existing “beat predecessors by threshold” rule among eligible miners; set weights winner-take-all as today.

### Handling “strange” UIDs

- Validators do **not** need a separate list of “base model UIDs.” Base models are simply valid participants with an early commit block. They will usually have zero or few evals in a new validator’s bucket, so they will not be eligible and cannot win.
- The only special handling on the validator is: **when there is no eligible miner, set weight 1 on UID 0 (burn)**. No need to treat any other UID specially.

## Summary

| Role     | Action |
|----------|--------|
| Owner    | Set `BASE_MODEL_CHUTE_ID` (and other owner/base-model config) in `config.py`. Owner is injected at `BASE_MODEL_COMMIT_BLOCK` (e.g. 1000). In validation, treat `chute_id == BASE_MODEL_CHUTE_ID` as always valid (skip all checks). |
| Validator| Get valid participants from API. When no miner has more than `MIN_EVALS_TO_COMPETE` evals in at least `MIN_VALIDATOR_APPEARANCES_FOR_ELIGIBILITY` validator buckets, set weight **1** on **UID 0** (burn). Otherwise run normal winner selection and set weights. |

This gives a single protocol: owner configures base-model chutes and always-valid behavior; validators burn when no one is eligible and otherwise select the winner as before.
