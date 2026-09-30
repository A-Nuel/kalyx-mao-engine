"""Unit tests for Phase 21 OrbioApiBalanceVerifier."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from src.settlement.orbio_api_verifier import (
    OrbioApiBalanceVerifier,
    OrbioApiStatus,
    OrbioApiVerificationReport,
)


def test_orbio_api_verifier_pending_without_key(monkeypatch):
    monkeypatch.delenv("ORBIO_API_KEY", raising=False)
    verifier = OrbioApiBalanceVerifier(api_key=None)
    report = verifier.verify()
    assert report.status == OrbioApiStatus.PENDING
    assert not report.is_confirmed
    assert "not configured" in report.error_message


def test_orbio_api_verifier_verified_success():
    mock_payload = {
        "id": "key_orbio_live_test_123",
        "status": "active",
        "account_id": "0x4675b9d0323479b1af399c87331d1d2436e6be99",
        "balance": {
            "available": "1.00",
            "used": "0.05",
            "currency": "USD",
        },
        "rate_limit": {
            "requests_per_minute": 120,
            "concurrent": 32,
        },
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        verifier = OrbioApiBalanceVerifier(api_key="sk-test-live-key")
        report = verifier.verify(
            expected_min_credits=0.95,
            expected_account_id="0x4675b9d0323479b1af399c87331d1d2436e6be99",
        )

        assert report.status == OrbioApiStatus.VERIFIED
        assert report.is_confirmed
        assert report.available_balance == 1.00
        assert report.used_balance == 0.05
        assert report.key_id == "key_orbio_live_test_123"
        assert report.account_id == "0x4675b9d0323479b1af399c87331d1d2436e6be99"
        audit = report.to_audit_dict()
        assert audit["is_confirmed"] is True
        assert audit["status"] == "VERIFIED"


def test_orbio_api_verifier_rejected_insufficient_balance():
    mock_payload = {
        "id": "key_123",
        "balance": {
            "available": "0.20",
            "used": "0.00",
            "currency": "USD",
        },
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        verifier = OrbioApiBalanceVerifier(api_key="sk-test-live-key")
        report = verifier.verify(expected_min_credits=0.95)

        assert report.status == OrbioApiStatus.REJECTED
        assert not report.is_confirmed
        assert "Available balance 0.2 USD < expected 0.95 credits" in report.error_message


def test_orbio_api_verifier_rejected_account_mismatch():
    mock_payload = {
        "id": "key_123",
        "account_id": "0x1111111111111111111111111111111111111111",
        "balance": {"available": "1.00", "used": "0.00"},
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        verifier = OrbioApiBalanceVerifier(api_key="sk-test-live-key")
        report = verifier.verify(
            expected_account_id="0x4675b9d0323479b1af399c87331d1d2436e6be99"
        )

        assert report.status == OrbioApiStatus.REJECTED
        assert "Account mismatch" in report.error_message


def test_orbio_api_verifier_rejected_http_401():
    mock_fp = MagicMock()
    mock_fp.read.return_value = b'{"error": "invalid_api_key"}'
    http_err = urllib.error.HTTPError(
        url="https://api.orbio.so/api/v1/key",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=mock_fp,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        verifier = OrbioApiBalanceVerifier(api_key="sk-invalid")
        report = verifier.verify()

        assert report.status == OrbioApiStatus.REJECTED
        assert "HTTP 401" in report.error_message


def test_orbio_api_verifier_pending_on_network_timeout():
    url_err = urllib.error.URLError("Connection timed out")

    with patch("urllib.request.urlopen", side_effect=url_err):
        verifier = OrbioApiBalanceVerifier(api_key="sk-test-key")
        report = verifier.verify()

        assert report.status == OrbioApiStatus.PENDING
        assert "Transport error" in report.error_message
