"""
Miner deployment command implementations for Vocence.

Provides async functions for:
- deploy_command: Deploy TTS model to Chutes
- commit_command: Commit model info to blockchain
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
import textwrap
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp

from vocence.domain.config import SUBNET_ID, CHAIN_NETWORK, CHUTES_AUTH_KEY, COLDKEY_NAME, HOTKEY_NAME
from vocence.shared.logging import emit_log


async def get_chute_info(chute_id: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Get chute info from Chutes API.
    
    Args:
        chute_id: Chute deployment ID
        api_key: Chutes API key
        
    Returns:
        Chute info dict or None if failed
    """
    url = f"https://api.chutes.ai/chutes/{chute_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                
                info = await resp.json()
                # Remove unnecessary fields
                for k in ("readme", "cords", "tagline", "instances"):
                    info.pop(k, None)
                info.get("image", {}).pop("readme", None)
                
                return info
    except Exception as e:
        emit_log(f"Failed to fetch chute {chute_id}: {e}", "warn")
        return None


async def get_latest_chute_id(model_name: str, api_key: str) -> Optional[str]:
    """Get latest chute ID for a repository.
    
    Args:
        model_name: HuggingFace repository name
        api_key: Chutes API key
    
    Returns:
        Chute ID or None if not found
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.chutes.ai/chutes/",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception:
        return None
    
    chutes = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(chutes, list):
        return None
    
    # Find chute matching the model_name
    for chute in reversed(chutes):
        if any(chute.get(k) == model_name for k in ("tagline", "readme", "name")):
            return chute.get("chute_id")
    return None


async def deploy_command(
    model_name: str,
    model_revision: str,
    chutes_api_key: Optional[str] = None,
    chute_user: Optional[str] = None,
) -> Dict[str, Any]:
    """Deploy TTS model to Chutes.
    
    Args:
        model_name: HuggingFace repository ID (e.g., "user/model-name")
        model_revision: HuggingFace commit SHA
        chutes_api_key: Chutes API key (optional, from env if not provided)
        chute_user: Chutes username (optional, from env if not provided)
        
    Returns:
        Result dict with success status and chute_id
    """
    chutes_api_key = chutes_api_key or CHUTES_AUTH_KEY
    chute_user = chute_user
    
    if not chutes_api_key:
        emit_log("CHUTES_AUTH_KEY not configured", "error")
        return {"success": False, "error": "CHUTES_AUTH_KEY not configured"}
    
    if not chute_user:
        emit_log("CHUTE_USER not configured", "error")
        return {"success": False, "error": "CHUTE_USER not configured"}
    
    emit_log(f"Building Chute config for model_name={model_name} model_revision={model_revision}", "info")
    
    # Generate Chute configuration for TTS model
    chutes_config = textwrap.dedent(f'''
import os
import uuid
import asyncio
import aiohttp
import tempfile
from typing import Optional, Literal
from io import BytesIO
from loguru import logger
from pydantic import BaseModel, Field
from fastapi import Response, HTTPException, status

from chutes.image import Image as ChuteImage
from chutes.chute import Chute, NodeSelector

model_name_sanitized = "{model_name}".replace("/", "-")

chute_image = (
    ChuteImage(
        username="chutes",
        name="tts-base",
        tag="0.0.12",
        readme="Text-to-speech synthesis with optimized inference",
    )
    .from_base("parachutes/python:3.12")
    .set_user("root")
    .run_command("apt update && apt -y install ffmpeg espeak-ng && chown chutes /usr/include/python3.12")
    .set_user("chutes")
    .run_command("pip install wheel setuptools setuptools_scm packaging")
    .run_command("pip install torch transformers accelerate && uv cache clean")
)

chute = Chute(
    username="{chute_user}",
    name="{model_name}",
    tagline="{model_name}",
    readme="{model_name}",
    image=chute_image,
    node_selector=NodeSelector(
        gpu_count=1,
        include=["h200"],
    ),
    concurrency=4,
    shutdown_after_seconds=86400,
    allow_external_egress=True,
)


class TTSArgs(BaseModel):
    prompt: str = Field(..., description="Text prompt for speech synthesis.")
    sample_rate: Optional[int] = Field(22050, ge=16000, le=48000, description="Output sample rate.")
    duration: Optional[float] = Field(5.0, ge=1.0, le=30.0, description="Maximum duration in seconds.")
    format: Optional[str] = Field("wav", description="Output format (wav, mp3).")
    seed: Optional[int] = Field(None, description="Generation seed.")


class Synthesizer:
    def __init__(self):
        import torch
        from transformers import AutoProcessor, AutoModelForTextToSpeech
        
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained("{model_name}")
        self.model = AutoModelForTextToSpeech.from_pretrained("{model_name}", torch_dtype=torch.bfloat16)
        self.model.to("cuda")

    def synthesize(self, prompt, sample_rate, duration, format, seed):
        generator = (
            self.torch.Generator("cuda").manual_seed(seed)
            if isinstance(seed, int)
            else self.torch.Generator(device="cuda").manual_seed(42)
        )
        
        inputs = self.processor(text=prompt, return_tensors="pt")
        inputs = inputs.to("cuda")
        
        with self.torch.inference_mode():
            output = self.model.generate(
                **inputs,
                generator=generator,
                max_length=int(sample_rate * duration),
            )
            audio = output.audio_values[0].cpu().numpy()

        # Convert to WAV format
        import soundfile as sf
        output_file = f"/tmp/{{uuid.uuid4()}}.{format}"
        sf.write(output_file, audio, sample_rate)
        
        buffer = BytesIO()
        with open(output_file, "rb") as infile:
            buffer.write(infile.read())
        buffer.seek(0)
        
        return Response(
            content=buffer.getvalue(),
            media_type="audio/wav" if format == "wav" else "audio/mpeg",
            headers={{"Content-Disposition": f"attachment; filename=\\"{{os.path.basename(output_file)}}\\""}}
        )


@chute.on_startup()
async def initialize(self):
    self.synthesizer = Synthesizer()
    self.lock = asyncio.Lock()

@chute.cord(
    public_api_path="/synthesize",
    public_api_method="POST",
    stream=False,
    output_content_type="audio/wav",
)
async def synthesize(self, args: TTSArgs):
    async with self.lock:
        return self.synthesizer.synthesize(
            args.prompt, args.sample_rate, args.duration, args.format, args.seed
        )
''')
    
    tmp_file = Path("tmp_chute.py")
    tmp_file.write_text(chutes_config)
    emit_log(f"Wrote Chute config to {tmp_file}", "info")
    
    # Deploy to Chutes
    cmd = ["chutes", "deploy", f"{tmp_file.stem}:chute", "--accept-fee"]
    env = {**os.environ, "CHUTES_API_KEY": chutes_api_key}
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE,
        )
        
        if proc.stdin:
            proc.stdin.write(b"y\n")
            await proc.stdin.drain()
            proc.stdin.close()
        
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="ignore")
        emit_log(output, "info")
        
        # Check for errors
        import re
        match = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+\|\s+(\w+)", output)
        if match and match.group(2) == "ERROR":
            emit_log("Chutes deploy failed with error log", "error")
            tmp_file.unlink(missing_ok=True)
            return {"success": False, "error": "Chutes deploy failed"}
        
        if proc.returncode != 0:
            emit_log(f"Chutes deploy failed with code {proc.returncode}", "error")
            tmp_file.unlink(missing_ok=True)
            return {"success": False, "error": f"Exit code {proc.returncode}"}
        
        tmp_file.unlink(missing_ok=True)
        emit_log("Chute deployment successful", "success")
        
        # Get chute info
        chute_id = await get_latest_chute_id(model_name, api_key=chutes_api_key)
        emit_log(f"Chute ID: {chute_id}", "info")
        
        chute_info = await get_chute_info(chute_id, chutes_api_key) if chute_id else None
        
        result = {
            "success": bool(chute_id),
            "chute_id": chute_id,
            "chute": chute_info,
            "model_name": model_name,
            "model_revision": model_revision,
        }
        emit_log(f"Deployed to Chutes: {chute_id}", "success")
        return result
    
    except Exception as e:
        emit_log(f"Chutes deployment failed: {e}", "error")
        tmp_file.unlink(missing_ok=True)
        return {"success": False, "error": str(e)}


async def commit_command(
    model_name: str,
    model_revision: str,
    chute_id: str,
    coldkey: Optional[str] = None,
    hotkey: Optional[str] = None,
    chain_network: Optional[str] = None,
    subnet_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Commit model info to blockchain.
    
    Args:
        model_name: HuggingFace repository ID
        model_revision: HuggingFace commit SHA
        chute_id: Chutes deployment ID
        coldkey: Wallet coldkey name (optional, from env if not provided)
        hotkey: Wallet hotkey name (optional, from env if not provided)
        chain_network: Override network (finney/test). If None, uses CHAIN_NETWORK from config.
        subnet_id: Override subnet ID (default 78 mainnet). If None, uses SUBNET_ID from config.
        
    Returns:
        Result dict with success status
    """
    import bittensor as bt

    network = chain_network if chain_network is not None else CHAIN_NETWORK
    netuid = subnet_id if subnet_id is not None else SUBNET_ID

    cold = coldkey or COLDKEY_NAME
    hot = hotkey or HOTKEY_NAME
    wallet = bt.Wallet(name=cold, hotkey=hot)

    emit_log(f"Committing: {model_name}@{model_revision[:8]} (chute: {chute_id})", "info")
    emit_log(f"Network: {network}, subnet: {netuid}", "info")
    emit_log(f"Using wallet: {wallet.hotkey.ss58_address[:16]}...", "info")

    async def _commit():
        subtensor = bt.AsyncSubtensor(network=network)
        data = json.dumps({
            "model_name": model_name,
            "model_revision": model_revision,
            "chute_id": chute_id,
        })

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await subtensor.set_reveal_commitment(
                    wallet=wallet,
                    netuid=netuid,
                    data=data,
                    blocks_until_reveal=1,
                )
                return True
            except Exception as e:
                if "SpaceLimitExceeded" in str(e):
                    emit_log("Space limit exceeded, waiting for next block...", "warn")
                    await asyncio.sleep(12)
                elif attempt < max_retries - 1:
                    emit_log(f"Commit attempt {attempt + 1} failed: {e}", "warn")
                    await asyncio.sleep(6)
                else:
                    raise
        return False
    
    try:
        success = await _commit()
        
        if success:
            result = {
                "success": True,
                "model_name": model_name,
                "model_revision": model_revision,
                "chute_id": chute_id,
            }
            emit_log("Commit successful", "success")
        else:
            result = {"success": False, "error": "Commit failed after retries"}
            emit_log("Commit failed", "error")
        
        return result
    
    except Exception as e:
        emit_log(f"Commit failed: {e}", "error")
        return {"success": False, "error": str(e)}

