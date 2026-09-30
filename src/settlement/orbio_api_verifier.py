"""Phase 21 — Off-chain Orbio API balance and entitlement verifier.

Connects the on-chain CREDIT activation evidence (Phase 20B) to the off-chain
Orbio API entitlement (Phase 21).

Authoritative Orbio Gateway endpoint:
  GET https://api.orbio.so/api/v1/key
  Headers:
    Authorization: Bearer <ORBIO_API_KEY>

Response schema:
  {
    "id": "key_...",
    "status": "active",
    "account_id": "0x...",
    "balance": {
      "available": "0.95",
      "used": "0.00",
      "currency": "USD"
    },
    "rate_limit": { ... }
  }
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_ORBIO_GATEWAY_BASE = "https://api.orbio.so/api/v1"


class OrbioApiStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


@dataclass
class OrbioApiVerificationReport:
    status: OrbioApiStatus
    available_balance: Optional[float] = None
    used_balance: Optional[float] = None
    currency: str = "USD"
    key_id: Optional[str] = None
    account_id: Optional[str] = None
    rate_limit: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    verified_at: str = ""
    raw_response: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.verified_at:
            self.verified_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_confirmed(self) -> bool:
        return self.status == OrbioApiStatus.VERIFIED

    def to_audit_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "is_confirmed": self.is_confirmed,
            "available_balance": self.available_balance,
            "used_balance": self.used_balance,
            "currency": self.currency,
            "key_id": self.key_id,
            "account_id": self.account_id,
            "error_message": self.error_message,
            "verified_at": self.verified_at,
        }


class OrbioApiBalanceVerifier:
    """Verifies that on-chain CREDIT activation is recognized off-chain by Orbio."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        gateway_base: Optional[str] = None,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = (api_key or os.getenv("ORBIO_API_KEY", "")).strip() or None
        self.gateway_base = (
            gateway_base or os.getenv("ORBIO_GATEWAY_BASE", DEFAULT_ORBIO_GATEWAY_BASE)
        ).rstrip("/")
        self.timeout = timeout

    def verify(
        self,
        *,
        expected_min_credits: float = 0.95,
        expected_account_id: Optional[str] = None,
    ) -> OrbioApiVerificationReport:
        """Query Orbio API key endpoint and verify active balance entitlement."""
        if not self.api_key:
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.PENDING,
                error_message="ORBIO_API_KEY not configured in environment",
            )

        endpoint = f"{self.gateway_base}/key"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "Kalyx-Orbio-Verifier/1.0",
        }

        req = urllib.request.Request(endpoint, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status_code = resp.status
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning("Orbio API returned HTTP %s: %s", exc.code, err_body[:200])
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                error_message=f"Orbio API returned HTTP {exc.code}: {err_body[:100]}",
            )
        except urllib.error.URLError as exc:
            logger.warning("Orbio API connection failed: %s", exc)
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.PENDING,
                error_message=f"Transport error reaching Orbio API: {exc}",
            )
        except Exception as exc:
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                error_message=f"Unexpected error querying Orbio API: {exc}",
            )

        if status_code != 200:
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                error_message=f"Unexpected status code HTTP {status_code}",
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                error_message="Malformed JSON response from Orbio API",
            )

        if not isinstance(payload, dict):
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                error_message="Invalid payload structure from Orbio API",
            )

        # Extract balance details
        bal = payload.get("balance")
        if isinstance(bal, dict):
            avail_raw = bal.get("available", bal.get("limit_remaining", 0))
            used_raw = bal.get("used", bal.get("usage", 0))
            currency = str(bal.get("currency", "USD"))
        else:
            avail_raw = payload.get("available", 0)
            used_raw = payload.get("used", 0)
            currency = str(payload.get("currency", "USD"))

        try:
            available = float(avail_raw)
            used = float(used_raw)
        except (ValueError, TypeError):
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                error_message=f"Unparseable balance values: available={avail_raw}, used={used_raw}",
                raw_response=payload,
            )

        key_id = payload.get("id") or payload.get("key_id")
        account_id = payload.get("account_id")
        rate_limit = payload.get("rate_limit")

        # Optional account verification if operator address is bound
        if expected_account_id and account_id:
            is_both_evm = str(expected_account_id).startswith("0x") and str(account_id).startswith("0x")
            if is_both_evm:
                if str(account_id).lower() != str(expected_account_id).lower():
                    return OrbioApiVerificationReport(
                        status=OrbioApiStatus.REJECTED,
                        available_balance=available,
                        used_balance=used,
                        currency=currency,
                        key_id=str(key_id) if key_id else None,
                        account_id=str(account_id),
                        error_message=(
                            f"Account mismatch: Orbio API account {account_id} != expected {expected_account_id}"
                        ),
                        raw_response=payload,
                    )
            elif not str(expected_account_id).startswith("0x"):
                if str(account_id).lower() != str(expected_account_id).lower():
                    return OrbioApiVerificationReport(
                        status=OrbioApiStatus.REJECTED,
                        available_balance=available,
                        used_balance=used,
                        currency=currency,
                        key_id=str(key_id) if key_id else None,
                        account_id=str(account_id),
                        error_message=(
                            f"Account mismatch: Orbio API account {account_id} != expected {expected_account_id}"
                        ),
                        raw_response=payload,
                    )

        # Validate minimum expected balance
        if available < expected_min_credits:
            return OrbioApiVerificationReport(
                status=OrbioApiStatus.REJECTED,
                available_balance=available,
                used_balance=used,
                currency=currency,
                key_id=str(key_id) if key_id else None,
                account_id=str(account_id) if account_id else None,
                rate_limit=rate_limit if isinstance(rate_limit, dict) else None,
                error_message=(
                    f"Available balance {available} {currency} < expected {expected_min_credits} credits"
                ),
                raw_response=payload,
            )

        return OrbioApiVerificationReport(
            status=OrbioApiStatus.VERIFIED,
            available_balance=available,
            used_balance=used,
            currency=currency,
            key_id=str(key_id) if key_id else None,
            account_id=str(account_id) if account_id else None,
            rate_limit=rate_limit if isinstance(rate_limit, dict) else None,
            raw_response=payload,
        )
