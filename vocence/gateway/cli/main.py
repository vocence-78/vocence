"""
CLI interface for Vocence.

Provides commands for running the validator, owner (API + source downloader),
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
    
    Single process. For owner, use `vocence owner serve` to run API + downloader
    in separate processes.
    """
    from vocence.gateway.http.service.app import run_service
    run_service()


# Owner commands (corpus bucket owner: run all owner-side processes)
@cli.group()
def owner():
    """Corpus owner: run all processes that populate the Hippius corpus bucket.
    
    Use owner Hippius credentials (HIPPIUS_OWNER_* or HIPPIUS_ACCESS_KEY).
    """
    pass


@owner.command("serve")
@click.option(
    "--rounds",
    type=int,
    default=None,
    help="Run N rounds then exit (default: run until Ctrl+C)",
)
@click.option(
    "--delay",
    type=float,
    default=2.0,
    help="Initial delay in seconds before first round (default: 2.0)",
)
@click.option(
    "--no-api",
    is_flag=True,
    help="Run only the source audio downloader (no HTTP API). Use when API runs elsewhere.",
)
def owner_serve(rounds, delay, no_api):
    """Run all owner processes in separate processes: API + source audio downloader.
    
    By default starts two processes:
    - Process 1: HTTP API (participants, evaluations, metrics, blocklist) on SERVICE_PORT (default 8000)
    - Process 2: Source audio downloader (LibriVox → corpus bucket, prune when over limit)
    
    Use --no-api to run only the downloader (e.g. if the API is already running).
    """
    import os
    import multiprocessing
    import time

    from vocence.shared.logging import emit_log, print_header
    from vocence.gateway.http.service.tasks.source_audio_downloader import (
        run_source_audio_downloader_standalone,
    )

    if no_api:
        try:
            asyncio.run(
                run_source_audio_downloader_standalone(
                    rounds=rounds,
                    initial_delay_sec=delay,
                )
            )
        except KeyboardInterrupt:
            pass
        return

    # Start API in a separate process
    from vocence.gateway.http.service.app import run_service

    api_process = multiprocessing.Process(target=run_service)
    api_process.start()
    from vocence.domain.config import SERVICE_HOST, SERVICE_PORT
    print_header("Owner serve: API (separate process) + source audio downloader")
    emit_log(f"API process started (PID {api_process.pid}), http://{SERVICE_HOST}:{SERVICE_PORT}", "info")
    time.sleep(1.5)  # give API time to bind

    try:
        asyncio.run(
            run_source_audio_downloader_standalone(
                rounds=rounds,
                initial_delay_sec=delay,
            )
        )
    except KeyboardInterrupt:
        pass
    finally:
        if api_process.is_alive():
            emit_log("Stopping API process...", "info")
            api_process.terminate()
            api_process.join(timeout=5)
            if api_process.is_alive():
                api_process.kill()
                api_process.join()


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
    from openai import AsyncOpenAI

    import asyncio
    from vocence.domain.config import OPENAI_AUTH_KEY, CHUTES_AUTH_KEY, CHAIN_NETWORK, SUBTENSOR_TIMEOUT_SEC
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.storage import create_corpus_storage_client, create_validator_storage_client
    from vocence.pipeline.generation import generate_samples_continuously

    async def run_generator():
        print_header("Vocence Prompt Generator Starting")

        if not CHUTES_AUTH_KEY:
            emit_log("CHUTES_AUTH_KEY environment variable required", "error")
            return
        if not OPENAI_AUTH_KEY:
            emit_log("OPENAI_AUTH_KEY environment variable required", "error")
            return

        subtensor = bt.AsyncSubtensor(network=CHAIN_NETWORK)
        corpus_client = create_corpus_storage_client()
        validator_client = create_validator_storage_client()
        openai_client = AsyncOpenAI(api_key=OPENAI_AUTH_KEY)

        async def get_block_with_timeout():
            return await asyncio.wait_for(subtensor.get_current_block(), timeout=SUBTENSOR_TIMEOUT_SEC)

        await generate_samples_continuously(
            corpus_client, validator_client, openai_client, get_block_with_timeout
        )

    asyncio.run(run_generator())


@services.command("validator")
def start_validator():
    """Start weight setting service only.
    
    Sets weights based on miner performance from the samples bucket.
    Does NOT generate samples - use this with a separate generator instance.
    """
    import bittensor as bt
    
    from vocence.domain.config import CHAIN_NETWORK, COLDKEY_NAME, HOTKEY_NAME
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.storage import create_validator_storage_client
    from vocence.engine.coordinator import cycle_step
    
    async def run_validator():
        print_header("Vocence Validator (Weight Setter) Starting")
        # Use ref so cycle_step can reconnect on timeout
        subtensor_ref = {"client": bt.AsyncSubtensor(network=CHAIN_NETWORK)}
        wallet = bt.Wallet(name=COLDKEY_NAME, hotkey=HOTKEY_NAME)
        validator_client = create_validator_storage_client()
        emit_log(f"Wallet: {COLDKEY_NAME}/{HOTKEY_NAME}", "info")
        emit_log(f"Network: {CHAIN_NETWORK}", "info")
        while True:
            await cycle_step(subtensor_ref, wallet, validator_client)
    
    asyncio.run(run_validator())


# Query commands
@cli.command("get-miners")
def get_miners():
    """List miners with their committed chutes."""
    import bittensor as bt
    
    from vocence.domain.config import SUBNET_ID, CHAIN_NETWORK
    from vocence.shared.logging import emit_log, print_header
    from vocence.adapters.chain import parse_commitment
    
    async def run():
        print_header("Vocence Miners")
        
        subtensor = bt.AsyncSubtensor(network=CHAIN_NETWORK)
        current_block = await subtensor.get_current_block()
        commits = await subtensor.get_all_revealed_commitments(SUBNET_ID, block=current_block)
        
        if not commits:
            emit_log("No miner commitments found", "warn")
            return
        
        for hotkey, commit_data in commits.items():
            commit_block, commit_value = commit_data[-1]
            parsed = parse_commitment(commit_value)
            model_name = parsed.get("model_name", "unknown")
            model_revision = parsed.get("model_revision", "unknown")[:8]
            chute_id = parsed.get("chute_id", "unknown")
            emit_log(f"{hotkey[:8]}: {model_name}@{model_revision} chute={chute_id} (block {commit_block})", "info")
    
    asyncio.run(run())


# Corpus commands (audio source for evaluation)
@cli.group()
def corpus():
    """Corpus management: run the LibriVox source audio downloader."""
    pass


@corpus.command("source-downloader")
@click.option(
    "--rounds",
    type=int,
    default=None,
    help="Run N rounds then exit (default: run until Ctrl+C)",
)
@click.option(
    "--delay",
    type=float,
    default=2.0,
    help="Initial delay in seconds before first round (default: 2.0)",
)
def corpus_source_downloader(rounds, delay):
    """Run the LibriVox source audio downloader (corpus bucket uploader).

    Runs as a separate process: downloads chapters, extracts clips, uploads to
    the Hippius corpus bucket, prunes when over AUDIO_CORPUS_MAX_ENTRIES.
    Use owner Hippius credentials (HIPPIUS_OWNER_* or HIPPIUS_ACCESS_KEY).
    """
    from vocence.gateway.http.service.tasks.source_audio_downloader import (
        run_source_audio_downloader_standalone,
    )

    try:
        asyncio.run(
            run_source_audio_downloader_standalone(
                rounds=rounds,
                initial_delay_sec=delay,
            )
        )
    except KeyboardInterrupt:
        pass


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
