"""
Hotkey signature verification for service authentication.

Implements SS58 signature verification for validator requests with:
- Timestamp freshness (SIGNATURE_EXPIRY_SECONDS)
- Nonce-based replay protection (per-hotkey LRU of recently-seen nonces)
- Validator-registry membership check (see validate_request)
"""

import time
import hashlib
from collections import OrderedDict
from typing import Optional, Tuple, Annotated

from fastapi import HTTPException, Header, Request, Depends
from substrateinterface import Keypair

from vocence.shared.logging import emit_log
from vocence.domain.config import SIGNATURE_EXPIRY_SECONDS, ADMIN_HOTKEYS
from vocence.registry.persistence.repositories.blocklist_repository import BlocklistRepository
from vocence.registry.persistence.repositories.validator_repository import ValidatorRepository


# Max (hotkey, nonce) pairs kept in memory. At 2 req/10 min per hotkey (#6) and
# ~20 validators, 2048 easily covers the SIGNATURE_EXPIRY_SECONDS window.
_NONCE_CACHE_MAX = 2048


class _NonceCache:
    """Tiny LRU of (hotkey, nonce) -> seen_at. Rejects repeats within
    SIGNATURE_EXPIRY_SECONDS. Entries older than the expiry are pruned on access."""

    def __init__(self, max_size: int = _NONCE_CACHE_MAX, ttl_seconds: int = SIGNATURE_EXPIRY_SECONDS):
        self._data: "OrderedDict[Tuple[str, str], float]" = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def seen(self, hotkey: str, nonce: str) -> bool:
        """Return True if this (hotkey, nonce) has been seen within the TTL.
        Otherwise record it and return False."""
        now = time.time()
        key = (hotkey, nonce)
        # Prune expired entries cheaply — oldest are at the front.
        while self._data:
            oldest_key, oldest_t = next(iter(self._data.items()))
            if now - oldest_t > self._ttl:
                self._data.popitem(last=False)
            else:
                break
        if key in self._data:
            return True
        self._data[key] = now
        if len(self._data) > self._max_size:
            self._data.popitem(last=False)
        return False


class RequestVerifier:
    """Verifies hotkey signatures for service requests."""

    def __init__(self):
        self.blocklist_repo = BlocklistRepository()
        self.validator_repo = ValidatorRepository()
        self._nonce_cache = _NonceCache()

    async def validate_request(
        self,
        body: bytes,
        hotkey: str,
        signature: str,
        timestamp: str,
        nonce: str,
    ) -> Tuple[bool, Optional[str]]:
        """Validate a signed request.

        Args:
            body: Request body bytes
            hotkey: SS58 address of the signer
            signature: Hex-encoded signature
            timestamp: Unix timestamp string
            nonce: Single-use random string bound into the signed message

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate timestamp
        try:
            ts = int(timestamp)
            current_time = int(time.time())
            if abs(current_time - ts) > SIGNATURE_EXPIRY_SECONDS:
                return False, f"Timestamp expired (max {SIGNATURE_EXPIRY_SECONDS}s)"
        except ValueError:
            return False, "Invalid timestamp format"

        # Nonce must be present and not reused within the expiry window.
        if not nonce or len(nonce) < 8 or len(nonce) > 128:
            return False, "Missing or malformed nonce"
        if self._nonce_cache.seen(hotkey, nonce):
            return False, "Nonce already used (replay rejected)"

        # Check if hotkey is blocked
        if await self.blocklist_repo.is_blocked(hotkey):
            return False, "Hotkey is blocked"
        
        # Verify signature
        try:
            # Create message to verify: hash of (body + timestamp + nonce)
            message = self._build_message(body, timestamp, nonce)
            
            # Verify using substrateinterface
            keypair = Keypair(ss58_address=hotkey)
            
            # Remove 0x prefix if present
            sig_bytes = bytes.fromhex(signature.removeprefix("0x"))
            
            is_valid = keypair.verify(message, sig_bytes)

            if not is_valid:
                return False, "Invalid signature"

            # Signature is cryptographically valid — now check the signer is
            # actually a registered validator. Without this check any miner
            # (who has a valid keypair) could sign requests and pass as a
            # validator, inserting fake evaluations and graph activity rows.
            registered = await self.validator_repo.fetch_by_hotkey(hotkey)
            if registered is None:
                return False, "Hotkey is not a registered validator"

            # Optional stake gate. Zero-stake rows are kept around so the
            # dashboard can show validators that haven't been paid yet, but
            # we still allow them to submit — the owner chose to add them.
            # If you want a stake floor later, add a config constant and
            # check `registered.stake >= MIN_VALIDATOR_STAKE` here.

            await self.validator_repo.update_last_seen(hotkey)
            return True, None

        except Exception as e:
            emit_log(f"Signature verification error: {e}", "warn")
            return False, "Signature verification failed"
    
    def _build_message(self, body: bytes, timestamp: str, nonce: str) -> bytes:
        """Build the message that was signed.

        The nonce is bound into the signed message so a replayed signature can
        be detected and rejected even before the cache check fires.

        Args:
            body: Request body bytes
            timestamp: Unix timestamp string
            nonce: Single-use random string

        Returns:
            Message bytes to verify
        """
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{body_hash}:{timestamp}:{nonce}"
        return message.encode("utf-8")
    
    def check_admin(self, hotkey: str) -> bool:
        """Check if a hotkey is an admin.
        
        Args:
            hotkey: SS58 address to check
            
        Returns:
            True if hotkey is in admin list
        """
        return hotkey in ADMIN_HOTKEYS and hotkey != ""


# Global verifier instance
_verifier: Optional[RequestVerifier] = None


def get_verifier() -> RequestVerifier:
    """Get or create the request verifier instance."""
    global _verifier
    if _verifier is None:
        _verifier = RequestVerifier()
    return _verifier


async def verify_validator_signature(
    request: Request,
    x_validator_hotkey: Annotated[str, Header()],
    x_signature: Annotated[str, Header()],
    x_timestamp: Annotated[str, Header()],
    x_nonce: Annotated[str, Header()],
) -> str:
    """FastAPI dependency for signature verification.

    Args:
        request: FastAPI request object
        x_validator_hotkey: Validator's SS58 address
        x_signature: Hex-encoded signature
        x_timestamp: Unix timestamp
        x_nonce: Single-use random string (8..128 chars) bound into the signed message

    Returns:
        Verified hotkey

    Raises:
        HTTPException: If verification fails
    """
    verifier = get_verifier()

    # Read request body
    body = await request.body()

    is_valid, error = await verifier.validate_request(
        body=body,
        hotkey=x_validator_hotkey,
        signature=x_signature,
        timestamp=x_timestamp,
        nonce=x_nonce,
    )

    if not is_valid:
        raise HTTPException(
            status_code=401,
            detail=error or "Authentication failed",
        )

    return x_validator_hotkey


async def verify_admin_signature(
    request: Request,
    x_validator_hotkey: Annotated[str, Header()],
    x_signature: Annotated[str, Header()],
    x_timestamp: Annotated[str, Header()],
    x_nonce: Annotated[str, Header()],
) -> str:
    """FastAPI dependency for admin signature verification.

    Args:
        request: FastAPI request object
        x_validator_hotkey: Admin's SS58 address
        x_signature: Hex-encoded signature
        x_timestamp: Unix timestamp
        x_nonce: Single-use random string

    Returns:
        Verified admin hotkey

    Raises:
        HTTPException: If verification fails or not admin
    """
    verifier = get_verifier()

    # First verify the signature
    hotkey = await verify_validator_signature(
        request, x_validator_hotkey, x_signature, x_timestamp, x_nonce
    )

    # Then check admin status
    if not verifier.check_admin(hotkey):
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )

    return hotkey


def sign_request_body(keypair: Keypair, body: bytes, timestamp: str, nonce: str) -> str:
    """Sign a message for service authentication.

    This is a utility function for clients to sign requests.

    Args:
        keypair: Bittensor keypair (with private key)
        body: Request body bytes
        timestamp: Unix timestamp string
        nonce: Single-use random string (8..128 chars)

    Returns:
        Hex-encoded signature
    """
    verifier = RequestVerifier()
    message = verifier._build_message(body, timestamp, nonce)
    signature = keypair.sign(message)
    return "0x" + signature.hex()

