"""Tests for kivra/auth.py — refresh-token flow + BankID fallback orchestration.

Per spec FR-1, FR-2, FR-3, FR-4, FR-5. All HTTP mocked via responses; no
network. BankID QR flow is mocked at the method boundary (we don't drive
real QR scanning in unit tests).
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import responses
from freezegun import freeze_time

from kivra import tokens
from kivra.auth import KivraAuth
from tests.conftest import FAKE_SSN


TOKEN_URL = "https://app.api.kivra.com/v2/oauth2/token"


@pytest.fixture
def auth(tokens_dir, tmp_path):
    """KivraAuth instance with a no-op interaction provider."""
    interaction = MagicMock()
    interaction.display_qr_code = MagicMock()
    interaction.report_authentication_success = MagicMock()
    return KivraAuth(str(tmp_path / "qr"), interaction)


@pytest.fixture
def bankid_flow_mock():
    """Mocks KivraAuth.authenticate (the existing BankID QR flow) to return a deterministic dict.
    Lets tests focus on the refresh+fallback orchestration without driving real QR."""
    with patch.object(KivraAuth, "authenticate") as m:
        m.return_value = {
            "access_token": "atk_via_bankid",
            "refresh_token": "rtk_via_bankid",
            "actor_key": "user_via_bankid",
            "expires_in": 3600,
            "jwt_data": {"kivra_user_id": "user_via_bankid"},
        }
        yield m


@pytest.fixture
def bankid_flow_mock_no_refresh():
    """BankID variant where Kivra issues no refresh_token at all."""
    with patch.object(KivraAuth, "authenticate") as m:
        m.return_value = {
            "access_token": "atk_via_bankid",
            "refresh_token": None,
            "actor_key": "user_via_bankid",
            "expires_in": 3600,
            "jwt_data": {"kivra_user_id": "user_via_bankid"},
        }
        yield m


# ----- Path 3: BankID fallback (no tokens file) -----


def test_no_tokens_file_triggers_bankid_flow(auth, bankid_flow_mock):
    """FR-1 / FR-5: missing tokens file → BankID QR flow runs."""
    result = auth.authenticate_with_refresh_fallback(FAKE_SSN)

    bankid_flow_mock.assert_called_once_with(FAKE_SSN)
    assert result["access_token"] == "atk_via_bankid"

    # Tokens persisted after BankID
    saved = tokens.load_tokens(FAKE_SSN)
    assert saved is not None
    assert saved["access_token"] == "atk_via_bankid"
    assert saved["refresh_token"] == "rtk_via_bankid"


def test_bankid_without_refresh_token_persists_null(auth, bankid_flow_mock_no_refresh):
    """FR-3: BankID succeeds but Kivra issues no refresh_token → saved with null + INFO log."""
    result = auth.authenticate_with_refresh_fallback(FAKE_SSN)

    saved = tokens.load_tokens(FAKE_SSN)
    assert saved["access_token"] == "atk_via_bankid"
    assert saved["refresh_token"] is None


# ----- Path 1: cached access_token (no network) -----


@responses.activate
def test_valid_cached_access_token_no_network(auth, valid_tokens_on_disk):
    """FR-4: valid access_token in tokens.json → reused, /v2/oauth2/token NOT called."""
    with freeze_time("2026-05-14T12:00:00Z"):
        # Mark token as future-expiry by re-saving with a fresh response
        from kivra import tokens as tokens_mod
        tokens_mod.save_tokens(
            FAKE_SSN,
            {
                "access_token": "atk_valid_future",
                "refresh_token": "rtk_present",
                "expires_in": 3600,
            },
            {"kivra_user_id": "abc"},
        )
        result = auth.authenticate_with_refresh_fallback(FAKE_SSN)

    assert result["access_token"] == "atk_valid_future"
    # No HTTP calls to Kivra token endpoint
    assert len(responses.calls) == 0


# ----- Path 2: refresh-token grant -----


@responses.activate
def test_expired_access_with_refresh_rotates_both_tokens(
    auth, kivra_refresh_response_rotated, tokens_dir
):
    """FR-2: expired access + refresh succeeds + server returns NEW refresh → both updated."""
    # Pre-populate expired tokens
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_old", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(responses.POST, TOKEN_URL, json=kivra_refresh_response_rotated, status=200)

    with freeze_time("2026-05-14T12:00:00Z"):  # 2h later — access expired
        result = auth.authenticate_with_refresh_fallback(FAKE_SSN)

    assert result["access_token"] == "atk_refreshed_yyyy"
    assert result["refresh_token"] == "rtk_rotated_yyyy"  # NEW
    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body)
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "rtk_old"


@responses.activate
def test_expired_access_with_non_rotating_refresh_preserves_refresh(
    auth, kivra_refresh_response_non_rotating, tokens_dir
):
    """FR-2: refresh succeeds but server omits refresh_token → access updated, refresh unchanged."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_long_lived", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(responses.POST, TOKEN_URL, json=kivra_refresh_response_non_rotating, status=200)

    with freeze_time("2026-05-14T12:00:00Z"):
        result = auth.authenticate_with_refresh_fallback(FAKE_SSN)

    saved = tokens.load_tokens(FAKE_SSN)
    assert saved["access_token"] == "atk_refreshed_yyyy"
    assert saved["refresh_token"] == "rtk_long_lived"  # preserved


@responses.activate
def test_refresh_400_invalid_grant_falls_back_to_bankid(auth, bankid_flow_mock, tokens_dir):
    """FR-5: refresh returns 400 invalid_grant → BankID fallback (no exit)."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_revoked", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"error": "invalid_grant"},
        status=400,
    )

    with freeze_time("2026-05-14T12:00:00Z"):
        result = auth.authenticate_with_refresh_fallback(FAKE_SSN)

    bankid_flow_mock.assert_called_once_with(FAKE_SSN)
    assert result["access_token"] == "atk_via_bankid"


@responses.activate
def test_refresh_401_falls_back_to_bankid(auth, bankid_flow_mock, tokens_dir):
    """FR-5: refresh 401 → BankID fallback."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_old", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(responses.POST, TOKEN_URL, json={"error": "unauthorized"}, status=401)

    with freeze_time("2026-05-14T12:00:00Z"):
        auth.authenticate_with_refresh_fallback(FAKE_SSN)

    bankid_flow_mock.assert_called_once()


@responses.activate
def test_refresh_5xx_falls_back_to_bankid(auth, bankid_flow_mock, tokens_dir):
    """FR-5: refresh 5xx → BankID fallback (warning logged)."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_old", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(responses.POST, TOKEN_URL, status=503)

    with freeze_time("2026-05-14T12:00:00Z"):
        auth.authenticate_with_refresh_fallback(FAKE_SSN)

    bankid_flow_mock.assert_called_once()


@responses.activate
def test_refresh_network_error_falls_back_to_bankid(auth, bankid_flow_mock, tokens_dir):
    """FR-5: refresh raises connection error → BankID fallback."""
    import requests

    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_old", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(
        responses.POST,
        TOKEN_URL,
        body=requests.exceptions.ConnectionError("network down"),
    )

    with freeze_time("2026-05-14T12:00:00Z"):
        auth.authenticate_with_refresh_fallback(FAKE_SSN)

    bankid_flow_mock.assert_called_once()


@responses.activate
def test_refresh_200_missing_access_token_falls_back_to_bankid(auth, bankid_flow_mock, tokens_dir):
    """Defensive: refresh 200 but response missing access_token → treat as malformed → BankID."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_old", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"token_type": "Bearer", "expires_in": 3600},  # no access_token!
        status=200,
    )

    with freeze_time("2026-05-14T12:00:00Z"):
        auth.authenticate_with_refresh_fallback(FAKE_SSN)

    bankid_flow_mock.assert_called_once()


@responses.activate
def test_expired_access_no_refresh_token_falls_back_to_bankid(auth, bankid_flow_mock, tokens_dir):
    """If tokens file exists with expired access AND no refresh_token → skip refresh, do BankID."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "expires_in": 3600},  # no refresh_token!
            {"kivra_user_id": "abc"},
        )

    with freeze_time("2026-05-14T12:00:00Z"):
        auth.authenticate_with_refresh_fallback(FAKE_SSN)

    # No /v2/oauth2/token calls at all (refresh skipped — no token to send)
    assert len(responses.calls) == 0
    bankid_flow_mock.assert_called_once()


@responses.activate
def test_refresh_success_persists_tokens_to_disk(
    auth, kivra_refresh_response_rotated, tokens_dir
):
    """After successful refresh, the new tokens are durably saved (next run reuses them)."""
    with freeze_time("2026-05-14T10:00:00Z"):
        tokens.save_tokens(
            FAKE_SSN,
            {"access_token": "atk_old", "refresh_token": "rtk_old", "expires_in": 3600},
            {"kivra_user_id": "abc"},
        )

    responses.add(responses.POST, TOKEN_URL, json=kivra_refresh_response_rotated, status=200)

    with freeze_time("2026-05-14T12:00:00Z"):
        auth.authenticate_with_refresh_fallback(FAKE_SSN)

    # Reload from disk in a fresh context
    saved = tokens.load_tokens(FAKE_SSN)
    assert saved["access_token"] == "atk_refreshed_yyyy"
    assert saved["refresh_token"] == "rtk_rotated_yyyy"
