"""
Validator API client for Vocence.

Provides a client for validators to communicate with the centralized API service.
Handles request signing, retries, and error handling.
"""

import asyncio
import time
import hashlib
from typing import Dict, Any, List, Optional

import aiohttp
from substrateinterface import Keypair

from vocence.shared.logging import emit_log
from vocence.domain.entities import ParticipantInfo
from vocence.domain.config import API_URL, API_TIMEOUT, API_MAX_RETRIES


class ServiceClient:
    """Client for interacting with the Vocence API service."""
    
    def __init__(
        self,
        api_url: str = API_URL,
        keypair: Optional[Keypair] = None,
        hotkey_path: Optional[str] = None,
    ):
        """Initialize API client.
        
        Args:
            api_url: Base URL of the API service
            keypair: Bittensor keypair for signing requests
            hotkey_path: Path to hotkey file (alternative to keypair)
        """
        self.api_url = api_url.rstrip("/")
        self._keypair = keypair
        self._hotkey_path = hotkey_path
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def keypair(self) -> Keypair:
        """Get or load the keypair."""
        if self._keypair is None:
            if self._hotkey_path:
                self._keypair = Keypair.create_from_uri(self._hotkey_path)
            else:
                raise ValueError("No keypair or hotkey_path provided")
        return self._keypair
    
    @property
    def hotkey(self) -> str:
        """Get the hotkey SS58 address."""
        return self.keypair.ss58_address
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)
            )
        return self._session
    
    async def close(self) -> None:
        """Close the session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _sign_request(self, body: bytes) -> Dict[str, str]:
        """Sign a request body.

        Emits a fresh random nonce per call so a captured request cannot be
        replayed within the SIGNATURE_EXPIRY_SECONDS window on the server.

        Args:
            body: Request body bytes

        Returns:
            Dict with authentication headers
        """
        import secrets

        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)  # 32 chars; well within the server's 8..128 bound

        # Create message: hash of body + timestamp + nonce
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{body_hash}:{timestamp}:{nonce}".encode("utf-8")

        # Sign
        signature = self.keypair.sign(message)

        return {
            "X-Validator-Hotkey": self.hotkey,
            "X-Signature": "0x" + signature.hex(),
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
        }
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Any] = None,
        require_auth: bool = True,
    ) -> Any:
        """Make an API request.
        
        Args:
            method: HTTP method
            endpoint: API endpoint (e.g., "/participants/valid")
            data: Request body data
            require_auth: Whether to sign the request
            
        Returns:
            Response data
            
        Raises:
            Exception: If request fails
        """
        url = f"{self.api_url}{endpoint}"
        session = await self._get_session()
        
        # Prepare body
        if data:
            import json
            body = json.dumps(data).encode("utf-8")
        else:
            body = b""
        
        # Prepare headers
        headers = {"Content-Type": "application/json"}
        if require_auth:
            headers.update(self._sign_request(body))
        
        # Make request with retries
        last_error = None
        for attempt in range(API_MAX_RETRIES):
            try:
                async with session.request(
                    method,
                    url,
                    data=body if body else None,
                    headers=headers,
                ) as response:
                    if response.status == 200 or response.status == 201:
                        return await response.json()
                    elif response.status == 401:
                        raise PermissionError("Authentication failed")
                    elif response.status == 403:
                        raise PermissionError("Access denied")
                    elif response.status == 404:
                        raise ValueError(f"Not found: {endpoint}")
                    else:
                        error_text = await response.text()
                        raise Exception(f"API error {response.status}: {error_text}")
                        
            except aiohttp.ClientError as e:
                last_error = e
                if attempt < API_MAX_RETRIES - 1:
                    emit_log(f"API request failed, retrying... ({e})", "warn")
                    await asyncio.sleep(2 ** attempt)
        
        raise last_error or Exception("Request failed")
    
    # =========================================================================
    # Miners API
    # =========================================================================
    
    async def get_valid_miners(self) -> List[ParticipantInfo]:
        """Get list of valid miners from GET /participants/valid.
        
        Returns:
            List of valid ParticipantInfo objects
        """
        data = await self._request("GET", "/participants/valid")
        return self._participants_to_info(data.get("participants", []))
    
    async def get_all_miners(self) -> List[ParticipantInfo]:
        """Get all miners (valid and invalid) from GET /participants/all.
        
        Returns:
            List of all ParticipantInfo objects
        """
        data = await self._request("GET", "/participants/all")
        return self._participants_to_info(data.get("participants", []))

    async def get_active_validators(self) -> List[str]:
        """Get active validator hotkeys from GET /validators/active."""
        data = await self._request("GET", "/validators/active")
        validators = data.get("validators", [])
        return [str(hotkey).strip() for hotkey in validators if str(hotkey).strip()]
    
    @staticmethod
    def _participants_to_info(participants: List[Dict[str, Any]]) -> List[ParticipantInfo]:
        """Convert API participant list to ParticipantInfo."""
        return [
            ParticipantInfo(
                uid=p.get("uid", 0),
                hotkey=p.get("hotkey", ""),
                model_name=p.get("model_name") or "",
                model_revision=p.get("model_revision") or "",
                model_hash=p.get("model_hash") or "",
                chute_id=p.get("chute_id") or "",
                chute_slug=p.get("chute_slug") or "",
                is_valid=p.get("is_valid", False),
                invalid_reason=p.get("invalid_reason"),
                block=p.get("block") or 0,
            )
            for p in participants
        ]
    
    # =========================================================================
    # Evaluations API (submit evaluation metadata; one per miner per sample)
    # =========================================================================
    
    async def submit_evaluation(
        self,
        evaluation_id: str,
        participant_hotkey: str,
        s3_bucket: str,
        s3_prefix: str,
        wins: bool,
        prompt: Optional[str] = None,
        confidence: Optional[int] = None,
        reasoning: Optional[str] = None,
        original_audio_url: Optional[str] = None,
        generated_audio_url: Optional[str] = None,
        score: Optional[float] = None,
        element_scores: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Submit one evaluation to POST /evaluations.

        Args:
            evaluation_id: Unique evaluation (sample) identifier
            participant_hotkey: Miner's hotkey
            s3_bucket: S3 bucket containing sample
            s3_prefix: S3 prefix for sample files
            wins: Whether generated audio won
            prompt: Generation prompt
            confidence: Confidence percentage
            reasoning: Evaluation reasoning
            original_audio_url: Pre-signed URL for original/source audio
            generated_audio_url: Pre-signed URL for miner-generated audio
            score: Continuous weighted score in [0, 1]
            element_scores: Per-element raw score dict (keys match ELEMENT_WEIGHTS)

        Returns:
            Created evaluation record
        """
        data = {
            "evaluation_id": evaluation_id,
            "participant_hotkey": participant_hotkey,
            "s3_bucket": s3_bucket,
            "s3_prefix": s3_prefix,
            "wins": wins,
        }
        if prompt is not None:
            data["prompt"] = prompt
        if confidence is not None:
            data["confidence"] = confidence
        if reasoning is not None:
            data["reasoning"] = reasoning
        if original_audio_url is not None:
            data["original_audio_url"] = original_audio_url
        if generated_audio_url is not None:
            data["generated_audio_url"] = generated_audio_url
        if score is not None:
            data["score"] = score
        if element_scores is not None:
            data["element_scores"] = element_scores
        return await self._request("POST", "/evaluations", data)

    async def submit_evaluations_batch(
        self,
        evaluations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Submit up to 100 evaluations in a single request.

        Prefer this over looping over ``submit_evaluation`` — the owner API
        rate-limits writes per hotkey, and per-miner submits hit the limit.

        Args:
            evaluations: List of dicts with the same keys as ``submit_evaluation``
                (``evaluation_id``, ``participant_hotkey``, ``s3_bucket``,
                ``s3_prefix``, ``wins``, plus any optional fields).

        Returns:
            List of created evaluation records.
        """
        return await self._request("POST", "/evaluations/batch", evaluations)

    async def submit_sample(
        self,
        sample_id: str,
        miner_hotkey: str,
        s3_bucket: str,
        s3_prefix: str,
        wins: bool,
        prompt: Optional[str] = None,
        confidence: Optional[int] = None,
        reasoning: Optional[str] = None,
        original_audio_url: Optional[str] = None,
        generated_audio_url: Optional[str] = None,
        score: Optional[float] = None,
        element_scores: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Submit sample metadata (alias: one evaluation per miner).
        Calls POST /evaluations with evaluation_id=sample_id, participant_hotkey=miner_hotkey.
        """
        return await self.submit_evaluation(
            evaluation_id=sample_id,
            participant_hotkey=miner_hotkey,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
            wins=wins,
            prompt=prompt,
            confidence=confidence,
            reasoning=reasoning,
            original_audio_url=original_audio_url,
            generated_audio_url=generated_audio_url,
            score=score,
            element_scores=element_scores,
        )

    async def submit_live_evaluation_started(
        self,
        evaluation_id: str,
        prompt_summary: Optional[str] = None,
        miner_hotkeys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Notify owner API that an evaluation has started (for dashboard status bar).
        Calls POST /evaluations/live. Non-blocking; log and ignore failures."""
        data = {
            "evaluation_id": evaluation_id,
            "prompt_summary": (prompt_summary or "")[:512],
            "miner_hotkeys": list(miner_hotkeys or []),
        }
        return await self._request("POST", "/evaluations/live", data=data)

    async def cancel_live_evaluation(self, evaluation_id: str) -> Dict[str, Any]:
        """Clear pending live evaluation when no results will be submitted (e.g. bail-out or all evals failed).
        Calls POST /evaluations/live/cancel. Fire-and-forget; log and ignore failures."""
        data = {"evaluation_id": evaluation_id}
        return await self._request("POST", "/evaluations/live/cancel", data=data)

    async def start_weight_setting(
        self,
        cycle_block: int,
        target_validator_hotkeys: Optional[List[str]] = None,
        phase: str = "starting",
    ) -> Dict[str, Any]:
        """Notify owner API that this validator is starting weight setting for a cycle."""
        data = {
            "cycle_block": cycle_block,
            "target_validator_hotkeys": list(target_validator_hotkeys or []),
            "phase": phase,
        }
        return await self._request("POST", "/graph/weights/start", data=data)

    async def finish_weight_setting(
        self,
        cycle_block: int,
        result: str = "success",
        winner_hotkey: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Notify owner API that this validator finished weight setting for a cycle."""
        data = {
            "cycle_block": cycle_block,
            "result": result,
            "winner_hotkey": winner_hotkey,
        }
        return await self._request("POST", "/graph/weights/end", data=data)

    # =========================================================================
    # Blacklist API
    # =========================================================================
    
    async def get_blacklisted_miners(self) -> List[str]:
        """Get list of blacklisted miner hotkeys from GET /blocklist/participants.
        
        Returns:
            List of blacklisted miner hotkeys
        """
        return await self._request("GET", "/blocklist/participants", require_auth=False)
    
    async def add_to_blacklist(
        self,
        hotkey: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a miner to the blacklist (requires admin signature).
        
        Args:
            hotkey: Miner hotkey to blacklist
            reason: Reason for blacklisting
            
        Returns:
            Created blacklist entry
        """
        data = {"hotkey": hotkey}
        if reason:
            data["reason"] = reason
        return await self._request("POST", "/blacklist", data=data, require_auth=True)
    
    async def remove_from_blacklist(self, hotkey: str) -> Dict[str, Any]:
        """Remove a miner from the blacklist (requires admin signature).
        
        Args:
            hotkey: Miner hotkey to remove
            
        Returns:
            Confirmation message
        """
        return await self._request("DELETE", f"/blacklist/{hotkey}", require_auth=True)


# Convenience function to create a client from wallet
def create_service_client_from_wallet(
    wallet_name: str = "default",
    hotkey_name: str = "default",
    api_url: str = API_URL,
) -> ServiceClient:
    """Create an API client from a Bittensor wallet.
    
    Args:
        wallet_name: Wallet name
        hotkey_name: Hotkey name
        api_url: API service URL
        
    Returns:
        Configured ServiceClient
    """
    import bittensor as bt
    
    wallet = bt.Wallet(name=wallet_name, hotkey=hotkey_name)
    keypair = wallet.hotkey
    
    return ServiceClient(api_url=api_url, keypair=keypair)
