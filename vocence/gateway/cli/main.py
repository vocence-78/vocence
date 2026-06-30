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


if __name__ == "__main__":
    cli()
