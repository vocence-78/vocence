"""
CLI interface for Vocence.

Provides commands for running the validator, the HTTP API (dashboard/metrics),
miner (push/commit), and queries.

When you add or change commands or options, update the CLI reference:
  docs/CLI.md
"""

import asyncio
from pathlib import Path

import click

# Load .env from current working directory so NETWORK, NETUID, etc. apply to all commands
try:
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env")
except ImportError:
    pass

from vocence.shared.logging import print_banner
from vocence import __version__


@click.group()
@click.version_option(version=__version__, prog_name="vocence")
def cli():
    """Vocence - Voice Intelligence Subnet on Bittensor.
    
    Voice intelligence subnet (PromptTTS, STT, STS, cloning, etc.). Current
    focus (Q1) is PromptTTS; miners run voice models and validators evaluate
    them (content correctness, audio quality, prompt adherence).
    """
    print_banner()


@cli.command()
def serve():
    """Start the validator (prompt generator + weight setter).
    
    This is the main command to run the complete Vocence validator.
    It starts both the sample generation loop and the weight setting loop.
    """
    from vocence.engine.coordinator import main
    asyncio.run(main())


@cli.command("api")
def api():
    """Start the HTTP API only (participants, evaluations, metrics, blocklist).

    Single process. Dashboard/metrics service only — validators run independently.
    """
    from vocence.gateway.http.service.app import run_service
    run_service()


@cli.group()
def services():
    """Start individual services.
    
    Use these commands to run specific components separately.
    """
    pass


@services.command("generator")
def start_generator():
    """Start prompt generation service only.
    
    Continuously generates samples by downloading audio from Hippius,
    querying miners, scoring via GPT-4o, and uploading results.
    Uses block-based slots (same as full validator); requires chain access for current block.
    """
    import bittensor as bt

    import asyncio
    from vocence.domain.config import GEMINI_AUTH_KEY, CHUTES_AUTH_KEY, CHAIN_NETWORK, USE_LOCAL_REGISTRY
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.storage import create_validator_storage_client
    from vocence.pipeline.generation import generate_samples_continuously
    from vocence.pipeline.corpus import run_corpus_manager
    from vocence.pipeline.emotion_corpus import run_emotion_corpus_manager
    from vocence.engine.block_clock import BlockClock, run_block_poller
    from vocence.engine.coordinator import _reconnect_subtensor

    async def run_generator():
        print_header("Vocence Prompt Generator Starting")

        if not CHUTES_AUTH_KEY:
            emit_log("CHUTES_AUTH_KEY environment variable required", "error")
            return
        if not GEMINI_AUTH_KEY:
            emit_log("GEMINI_API_KEY (or GOOGLE_API_KEY) environment variable required", "error")
            return

        # One connection + one block poller; tasks read the shared clock.
        subtensor_ref = {"client": bt.AsyncSubtensor(network=CHAIN_NETWORK)}
        clock = BlockClock()
        validator_client = create_validator_storage_client()
        # AudioJudge builds its own Gemini client from GEMINI_AUTH_KEY; pass None as the
        # vestigial judge_client arg.
        judge_client = None

        bg_tasks = [
            asyncio.create_task(run_block_poller(subtensor_ref, clock, _reconnect_subtensor)),
            asyncio.create_task(run_corpus_manager()),
            asyncio.create_task(run_emotion_corpus_manager()),
        ]
        if USE_LOCAL_REGISTRY:
            from vocence.registry.local_registry import run_miner_registry
            bg_tasks.append(asyncio.create_task(run_miner_registry(get_block=clock.get_async, subtensor_ref=subtensor_ref)))
        try:
            await generate_samples_continuously(
                validator_client, judge_client, clock.get_async
            )
        finally:
            for t in bg_tasks:
                t.cancel()

    asyncio.run(run_generator())


@services.command("corpus")
def start_corpus():
    """Start the local audio corpus manager only.

    Continuously downloads English LibriVox clips (20-25s) into the local corpus
    directory (CORPUS_LOCAL_DIR) and prunes oldest clips beyond AUDIO_CORPUS_MAX_ENTRIES.
    Run this as a standalone process when generation and corpus upkeep are split.
    """
    import asyncio
    from vocence.shared.logging import print_header
    from vocence.pipeline.corpus import run_corpus_manager

    async def run():
        print_header("Vocence Local Corpus Manager Starting")
        await run_corpus_manager()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


@services.command("emotion-corpus")
def start_emotion_corpus():
    """Start the emotional (EARS) corpus manager only.

    Downloads EARS speaker zips and extracts 15-25s emotional clips (mapped onto the
    pipeline's `emotion` enum) into EMOTION_CORPUS_LOCAL_DIR, pruning beyond
    EMOTION_CORPUS_MAX_ENTRIES. Run standalone when corpus upkeep is split out.
    """
    import asyncio
    from vocence.shared.logging import print_header
    from vocence.pipeline.emotion_corpus import run_emotion_corpus_manager

    async def run():
        print_header("Vocence Emotional Corpus Manager Starting")
        await run_emotion_corpus_manager()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


@services.command("registry")
def start_registry():
    """Start the local miner registry only.

    Validates miners from chain commitments (HuggingFace + Chutes + duplicate
    detection), mirrors the central blacklist, and writes valid miners to the local
    SQLite registry (REGISTRY_DB_PATH). 'serve', 'services generator', and
    'services validator' already run this in the background; use this to run it as a
    standalone process when splitting services.
    """
    import asyncio
    from vocence.shared.logging import print_header
    from vocence.registry.local_registry import run_miner_registry

    async def run():
        print_header("Vocence Local Miner Registry Starting")
        await run_miner_registry()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


@services.command("validator")
def start_validator():
    """Start weight setting service only.
    
    Sets weights based on miner performance from the samples bucket.
    Does NOT generate samples - use this with a separate generator instance.
    """
    import bittensor as bt

    from vocence.domain.config import CHAIN_NETWORK, COLDKEY_NAME, HOTKEY_NAME, USE_LOCAL_REGISTRY
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.storage import create_validator_storage_client
    from vocence.engine.coordinator import cycle_step, _reconnect_subtensor
    from vocence.engine.block_clock import BlockClock, run_block_poller

    async def run_validator():
        print_header("Vocence Validator (Weight Setter) Starting")
        # One connection + one block poller; cycle_step and the registry read the clock.
        subtensor_ref = {"client": bt.AsyncSubtensor(network=CHAIN_NETWORK)}
        clock = BlockClock()
        wallet = bt.Wallet(name=COLDKEY_NAME, hotkey=HOTKEY_NAME)
        validator_client = create_validator_storage_client()
        emit_log(f"Wallet: {COLDKEY_NAME}/{HOTKEY_NAME}", "info")
        emit_log(f"Network: {CHAIN_NETWORK}", "info")
        bg_tasks = [asyncio.create_task(run_block_poller(subtensor_ref, clock, _reconnect_subtensor))]
        if USE_LOCAL_REGISTRY:
            from vocence.registry.local_registry import run_miner_registry
            bg_tasks.append(asyncio.create_task(run_miner_registry(get_block=clock.get_async, subtensor_ref=subtensor_ref)))
        try:
            while True:
                await cycle_step(subtensor_ref, wallet, validator_client, clock.get_async)
        finally:
            for t in bg_tasks:
                t.cancel()

    asyncio.run(run_validator())


# Query commands
@cli.command("get-miners")
def get_miners():
    """List miners with their committed chutes.

    Only commits at/after COMMIT_LOCK_BLOCK are recognized — pre-cutover commits
    are ignored entirely, matching the owner-side validation rule.
    """
    import bittensor as bt

    from vocence.domain.config import SUBNET_ID, CHAIN_NETWORK, COMMIT_LOCK_BLOCK
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.chain import parse_commitment, validate_commitment_fields

    async def run():
        print_header("Vocence Miners")

        subtensor = bt.AsyncSubtensor(network=CHAIN_NETWORK)
        current_block = await subtensor.get_current_block()
        commits = await subtensor.get_all_revealed_commitments(SUBNET_ID, block=current_block)

        if not commits:
            emit_log("No miner commitments found", "warn")
            return

        for hotkey, commit_data in commits.items():
            # Filter to commits at/after the cutover. Pre-cutover commits are
            # not recognized as commitments at all.
            if COMMIT_LOCK_BLOCK > 0:
                recognized = [
                    (b, v) for b, v in commit_data
                    if b >= COMMIT_LOCK_BLOCK and validate_commitment_fields(parse_commitment(v))[0]
                ]
            else:
                recognized = list(commit_data)
            if not recognized:
                continue
            commit_block, commit_value = recognized[-1]
            parsed = parse_commitment(commit_value)
            model_name = parsed.get("model_name", "unknown")
            model_revision = parsed.get("model_revision", "unknown")[:8]
            chute_id = parsed.get("chute_id", "unknown")
            emit_log(f"{hotkey[:8]}: {model_name}@{model_revision} chute={chute_id} (block {commit_block})", "info")

    asyncio.run(run())


# Miner commands
@cli.group()
def miner():
    """Miner management commands.
    
    Commands for deploying voice models to Chutes (PromptTTS in Q1) and
    committing model info to the blockchain.
    """
    pass


@miner.command("push")
@click.option("--model-name", required=True, help="HuggingFace repository ID (e.g., user/model-name)")
@click.option("--model-revision", required=True, help="HuggingFace commit SHA")
@click.option("--chutes-api-key", help="Chutes API key (optional, from env CHUTES_AUTH_KEY)")
@click.option("--chute-user", help="Chutes username (optional, from env CHUTE_USER)")
def miner_push(model_name, model_revision, chutes_api_key, chute_user):
    """Deploy voice model (PromptTTS) to Chutes."""
    import json
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.deployment import deploy_command
    
    async def run():
        print_header("Deploying to Chutes")
        emit_log(f"Repository: {model_name}", "info")
        emit_log(f"Revision: {model_revision[:16]}...", "info")
        
        result = await deploy_command(
            model_name=model_name,
            model_revision=model_revision,
            chutes_api_key=chutes_api_key,
            chute_user=chute_user,
        )
        
        print(json.dumps(result, indent=2))
        
        if result.get("success"):
            print_header("Deployment Complete")
            emit_log(f"Chute ID: {result.get('chute_id')}", "success")
        else:
            emit_log(f"Deployment failed: {result.get('error')}", "error")
    
    asyncio.run(run())


@miner.command("commit")
@click.option("--model-name", required=True, help="HuggingFace repository ID")
@click.option("--model-revision", required=True, help="HuggingFace commit SHA")
@click.option("--chute-id", required=True, help="Chutes deployment ID")
@click.option("--network", help="Chain network: finney (mainnet), test (testnet). Overrides NETWORK/CHAIN_NETWORK from .env")
@click.option("--netuid", type=int, help="Subnet ID (default 78 mainnet). Overrides NETUID/SUBNET_ID from .env")
@click.option("--coldkey", help="Wallet coldkey name (optional, from env COLDKEY_NAME)")
@click.option("--hotkey", help="Wallet hotkey name (optional, from env HOTKEY_NAME)")
def miner_commit(model_name, model_revision, chute_id, network, netuid, coldkey, hotkey):
    """Commit model info to blockchain. Network and subnet come from .env (NETWORK, NETUID) unless --network/--netuid are set."""
    import json
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.deployment import commit_command

    async def run():
        print_header("Committing to Chain")
        emit_log(f"Repository: {model_name}", "info")
        emit_log(f"Revision: {model_revision[:16]}...", "info")
        emit_log(f"Chute ID: {chute_id}", "info")
        if network:
            emit_log(f"Network: {network} (from --network)", "info")
        if netuid is not None:
            emit_log(f"Subnet ID: {netuid} (from --netuid)", "info")

        result = await commit_command(
            model_name=model_name,
            model_revision=model_revision,
            chute_id=chute_id,
            coldkey=coldkey,
            hotkey=hotkey,
            chain_network=network,
            subnet_id=netuid,
        )
        
        print(json.dumps(result, indent=2))
        
        if result.get("success"):
            print_header("Commit Complete")
            emit_log("Model info committed to chain", "success")
        else:
            emit_log(f"Commit failed: {result.get('error')}", "error")
    
    asyncio.run(run())


@cli.group()
def miner():
    """Miner commands: validate a model directory and publish a submission.

    Model-submission flow (no live endpoint, no team-granted key):
    fine-tune the canonical base -> `vocence miner check` -> `vocence miner publish`
    (validate -> upload to Hippius -> commit the v7 reveal on chain).
    """


@miner.command("check")
@click.option("--path", "path", required=True, type=click.Path(exists=True, file_okay=False),
              help="Local model directory to validate.")
@click.option("--seed-config", "seed_config", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Local genesis seed config.json to run the architecture-lock check against.")
def miner_check(path, seed_config):
    """Free local dry-run of the manifest + canonical-script (+ optional arch) checks."""
    import json
    from vocence.domain.spec import load_spec
    from vocence.registry.gauntlet import check_file_manifest, check_architecture
    from vocence.adapters.model_store import build_manifest, file_sha256, compute_dir_digest
    from vocence.shared.logging import emit_log, print_header

    spec = load_spec()
    print_header(f"Validating {path}")
    files = list(build_manifest(path).keys())
    miner_sha = file_sha256(path, spec.forbidden_py_except or "miner.py")

    outcomes = [check_file_manifest(files, spec, miner_py_sha256=miner_sha)]
    if seed_config:
        with open(Path(path) / "config.json") as fh:
            candidate = json.load(fh)
        with open(seed_config) as fh:
            seed = json.load(fh)
        outcomes.append(check_architecture(candidate, seed, spec))
    else:
        emit_log("architecture check skipped (pass --seed-config to enable)", "warn")

    ok = True
    for o in outcomes:
        emit_log(f"[{'PASS' if o.ok else 'FAIL'}] {o.name}: {o.reason or 'ok'}",
                 "success" if o.ok else "error")
        ok = ok and o.ok

    digest = compute_dir_digest(path)
    emit_log(f"content digest: {digest}", "info")
    emit_log("VALID" if ok else "INVALID", "success" if ok else "error")
    if not ok:
        raise SystemExit(1)


@miner.command("publish")
@click.option("--path", "path", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--name", "name", required=True, help="Repo suffix; full repo becomes <ns>/vocence-prompttts-<name>.")
@click.option("--namespace", "namespace", default=None, help="Hippius namespace (defaults to $VOCENCE_NAMESPACE).")
@click.option("--bucket", "bucket", default=None, help="Hippius model bucket (defaults to $VOCENCE_MODEL_BUCKET or 'vocence-models').")
@click.option("--commit/--no-commit", "do_commit", default=False, help="Also commit the v7 reveal on chain.")
@click.option("--king-digest", "king_digest", default="", help="Digest of the king this model was trained against (stale-parent).")
@click.option("--coldkey", default=None)
@click.option("--hotkey", default=None)
@click.option("--network", default=None)
@click.option("--netuid", default=None, type=int)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt before committing on chain.")
def miner_publish(path, name, namespace, bucket, do_commit, king_digest, coldkey, hotkey, network, netuid, yes):
    """Validate locally, upload to Hippius, and (optionally) commit the v7 reveal."""
    import os
    import json as _json
    from vocence.domain.spec import load_spec
    from vocence.registry.gauntlet import check_file_manifest
    from vocence.adapters.model_store import build_manifest, file_sha256, upload_model
    from vocence.adapters.storage import create_validator_storage_client
    from vocence.adapters.chain import format_reveal
    from vocence.adapters.deployment import commit_reveal_command
    from vocence.shared.logging import emit_log, print_header

    spec = load_spec()
    namespace = namespace or os.environ.get("VOCENCE_NAMESPACE")
    if not namespace:
        raise click.UsageError("--namespace (or $VOCENCE_NAMESPACE) is required")
    bucket = bucket or os.environ.get("VOCENCE_MODEL_BUCKET", "vocence-models")
    # Strip any accidental doubled prefix so `--name vocence-prompttts-v1` also works.
    suffix = name.removeprefix("vocence-prompttts-")
    repo = f"{namespace}/vocence-prompttts-{suffix}"

    print_header(f"Publishing {repo}")
    files = list(build_manifest(path).keys())
    miner_sha = file_sha256(path, spec.forbidden_py_except or "miner.py")
    fm = check_file_manifest(files, spec, miner_py_sha256=miner_sha)
    if not fm.ok:
        emit_log(f"[FAIL] file_manifest: {fm.reason}", "error")
        raise SystemExit(1)
    emit_log("[PASS] local file manifest", "success")

    async def _run():
        client = create_validator_storage_client()
        digest = await upload_model(client, bucket, repo, path)
        reveal = format_reveal(repo, digest, king_digest)
        emit_log(f"Uploaded. Reveal: {reveal}", "success")
        if do_commit:
            if not yes and not click.confirm(f"Commit {reveal} on chain?"):
                emit_log("Commit skipped.", "warn")
                return
            result = await commit_reveal_command(
                repo=repo, digest=digest, king_digest=king_digest, coldkey=coldkey, hotkey=hotkey,
                chain_network=network, subnet_id=netuid,
            )
            print(_json.dumps(result, indent=2))
        else:
            emit_log("Skipped on-chain commit (pass --commit to write it).", "info")

    asyncio.run(_run())


@cli.group()
def corpus():
    """Evaluation-corpus commands. The corpus is deterministic and pinned by hash in
    vocence.toml so every validator evaluates identical prompts."""


@corpus.command("build")
@click.option("--out", "out", required=True, type=click.Path(dir_okay=False), help="Output JSON path.")
@click.option("--n", "n", default=128, type=int, help="Number of samples (>= spec corpus_min_samples).")
def corpus_build(out, n):
    """Generate the deterministic eval corpus and print its pinned hash."""
    from pathlib import Path
    from vocence.pipeline.corpus_builder import build_corpus, serialize_corpus
    from vocence.pipeline.eval_corpus import corpus_hash
    from vocence.shared.logging import emit_log

    raw = serialize_corpus(build_corpus(n))
    Path(out).write_bytes(raw)
    h = corpus_hash(raw)
    emit_log(f"Wrote {n} samples to {out}", "success")
    emit_log(f"corpus hash: {h}", "info")
    emit_log("Pin this under [eval] corpus_hash in vocence.toml and publish the file to Hippius.", "info")


@cli.command("serve-koth")
@click.option("--corpus", "corpus_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Pinned eval corpus JSON (target_text + traits per sample).")
@click.option("--seed-config", "seed_config", default=None, type=click.Path(exists=True, dir_okay=False),
              help="Genesis seed config.json for the architecture-lock check.")
@click.option("--model-bucket", default=None, help="Hippius bucket holding submitted models ($VOCENCE_MODEL_BUCKET).")
@click.option("--dashboard-bucket", default=None, help="Hippius bucket to publish the dashboard to.")
@click.option("--votes", default=1, type=int, help="Naturalness judge votes per sample (order-swapped).")
def serve_koth(corpus_path, seed_config, model_bucket, dashboard_bucket, votes):
    """Run the decentralized KOTH validator loop (model-submission, local GPU eval).

    Reads v7 reveals from chain, validates + duels challengers locally, sets weights,
    and republishes the dashboard each cycle. Requires a GPU host with the judge/TTS
    models available. No owner API, no Chutes key.
    """
    import os
    import json
    import tempfile
    from datetime import datetime, timezone

    import bittensor as bt
    from vocence.domain import config as cfg
    from vocence.domain.spec import load_spec
    from vocence.pipeline.eval_corpus import load_corpus_file
    from vocence.pipeline.cache import GenerationCache
    from vocence.registry.fingerprint import FingerprintStore
    from vocence.adapters.storage import create_validator_storage_client
    from vocence.adapters.model_store import download_model
    from vocence.engine.chain_gateway import BittensorChainGateway
    from vocence.engine.run_koth_validator import (
        build_judges, make_generator_factory, make_validator, run_forever,
    )
    from vocence.gateway.dashboard.model import build_dashboard, build_run_detail
    from vocence.gateway.dashboard.publish import publish_dashboard, publish_run_detail
    from vocence.gateway.dashboard.store import ReportStore
    from vocence.shared.logging import emit_log, print_header

    spec = load_spec()
    print_header(f"KOTH validator · {spec.name} netuid {spec.netuid}")
    model_bucket = model_bucket or os.environ.get("VOCENCE_MODEL_BUCKET", "vocence-models")
    dashboard_bucket = dashboard_bucket or os.environ.get("VOCENCE_DASHBOARD_BUCKET", "vocence")
    seed_cfg = json.loads(open(seed_config).read()) if seed_config else {}
    corpus = load_corpus_file(corpus_path)
    emit_log(f"Loaded {len(corpus)} corpus samples (min {spec.corpus_min_samples})", "info")

    from vocence.engine.genesis import genesis_reign as _genesis_reign
    genesis = _genesis_reign(spec, owner_uid=cfg.OWNER_UID, owner_hotkey=cfg.OWNER_HOTKEY)

    wallet = bt.Wallet(name=cfg.COLDKEY_NAME, hotkey=cfg.HOTKEY_NAME)
    gateway = BittensorChainGateway(wallet, network=cfg.CHAIN_NETWORK, netuid=spec.netuid, spec=spec)
    store = FingerprintStore()
    cache = GenerationCache()
    storage = create_validator_storage_client()

    async def fetch_model(repo, digest):
        dest = tempfile.mkdtemp(prefix="vocence-model-")
        return await download_model(storage, model_bucket, repo, dest, expected_digest=digest)

    validate = make_validator(spec, seed_cfg, fetch_model, store)
    make_gen = make_generator_factory(spec, fetch_model, cache)
    judges = build_judges(spec, votes=votes)
    report_store = ReportStore(os.path.join(os.getcwd(), "data", "koth_reports.jsonl"))

    async def on_report(report):
        report_store.append(report)  # persists (survives restarts); drives the leaderboard
        try:
            # Per-run detail (albedo-style): every duel is an addressable record.
            if report.run_id and report.duel is not None:
                await publish_run_detail(storage, dashboard_bucket, build_run_detail(report, corpus))
            reign = await gateway.resolve_reign()
            data = build_dashboard(
                spec=spec, block=report.block, reign=reign, runs=report_store.recent(200),
                updated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            await publish_dashboard(storage, dashboard_bucket, data)
        except Exception as exc:  # dashboard must never break the loop
            emit_log(f"dashboard publish failed: {exc}", "warn")

    asyncio.run(run_forever(
        chain=gateway, validate=validate, make_generator=make_gen, judges=judges,
        corpus=corpus, spec=spec, cycle_length=cfg.CYCLE_LENGTH, on_report=on_report,
        genesis_reign=genesis,
    ))


@cli.group()
def dashboard():
    """Dashboard commands. The dashboard is a static site (see ./dashboard) that reads
    a JSON snapshot published to Hippius — there is no dashboard API server."""


@dashboard.command("publish")
@click.option("--file", "file", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Dashboard JSON to upload (build it from validator state).")
@click.option("--bucket", "bucket", default=None, help="Hippius bucket (default $VOCENCE_DASHBOARD_BUCKET or 'vocence').")
@click.option("--key", "key", default=None, help="Object key (default data/dashboard.json).")
def dashboard_publish(file, bucket, key):
    """Upload a dashboard JSON snapshot to Hippius for the static frontend to read."""
    import os
    import json as _json
    from vocence.adapters.storage import create_validator_storage_client
    from vocence.gateway.dashboard.publish import publish_dashboard, DASHBOARD_OBJECT_KEY
    from vocence.shared.logging import emit_log

    bucket = bucket or os.environ.get("VOCENCE_DASHBOARD_BUCKET", "vocence")
    key = key or DASHBOARD_OBJECT_KEY
    with open(file) as fh:
        data = _json.load(fh)

    async def _run():
        client = create_validator_storage_client()
        written = await publish_dashboard(client, bucket, data, object_key=key)
        emit_log(f"Dashboard live at <hippius>/{bucket}/{written}", "success")

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
