"""Shared pytest fixtures for kivra-sync fork tests.

Convention:
- The tokens module reads its base directory from env var KIVRA_SYNC_TOKENS_DIR
  (falls back to ~/.local/share/kivra-sync). Tests redirect it to tmp_path via
  the `tokens_dir` fixture.
- A canonical fake SSN '190001011234' is used throughout (12 digits, format-valid,
  fictitious — would be born 1900-01-01 which is implausible). Never use a real SSN.
- Frozen-time fixtures use freezegun anchored at 2026-05-14T12:00:00Z.
"""

import os
import json
from pathlib import Path

import pytest
from freezegun import freeze_time


# A canonical test SSN: 12 digits, format-valid, deliberately implausible (1900-01-01)
FAKE_SSN = "190001011234"
FAKE_SSN_ALT = "200012319876"  # second user for isolation tests


@pytest.fixture
def tokens_dir(tmp_path, monkeypatch):
    """Redirect kivra-sync's token storage to tmp_path. Yields the directory."""
    d = tmp_path / "kivra-sync-tokens"
    monkeypatch.setenv("KIVRA_SYNC_TOKENS_DIR", str(d))
    return d


@pytest.fixture
def clean_env(monkeypatch):
    """Strips any KIVRA_SSN / KIVRA_SYNC_TOKENS_DIR from env to give tests a clean slate."""
    monkeypatch.delenv("KIVRA_SSN", raising=False)
    return monkeypatch


@pytest.fixture
def frozen_now():
    """Freezes time at 2026-05-14T12:00:00Z. Returns the freezer context manager."""
    return freeze_time("2026-05-14T12:00:00Z")


@pytest.fixture
def kivra_bankid_response_with_refresh():
    """A successful BankID /v2/oauth2/token response that includes refresh_token."""
    return {
        "access_token": "atk_initial_xxxx",
        "refresh_token": "rtk_initial_xxxx",
        "id_token": _fake_id_token("9c8b7a6d-fake-actor-key"),
        "expires_in": 3600,
        "token_type": "Bearer",
    }


@pytest.fixture
def kivra_bankid_response_no_refresh():
    """A successful BankID response that does NOT include refresh_token (the
    pessimistic case — Kivra might be non-issuing)."""
    return {
        "access_token": "atk_initial_xxxx",
        "id_token": _fake_id_token("9c8b7a6d-fake-actor-key"),
        "expires_in": 3600,
        "token_type": "Bearer",
    }


@pytest.fixture
def kivra_refresh_response_rotated():
    """Refresh response that includes a NEW refresh_token (rotation case)."""
    return {
        "access_token": "atk_refreshed_yyyy",
        "refresh_token": "rtk_rotated_yyyy",  # NEW value
        "expires_in": 3600,
        "token_type": "Bearer",
    }


@pytest.fixture
def kivra_refresh_response_non_rotating():
    """Refresh response that omits refresh_token (non-rotating server)."""
    return {
        "access_token": "atk_refreshed_yyyy",
        "expires_in": 3600,
        "token_type": "Bearer",
    }


@pytest.fixture
def valid_tokens_on_disk(tokens_dir, kivra_bankid_response_with_refresh):
    """Pre-populates tokens_dir with a valid-future-expiry token file. Returns the dict written."""
    # Defer import so monkeypatched env is in place before module-level reads
    from kivra import tokens

    tokens.save_tokens(
        ssn=FAKE_SSN,
        response=kivra_bankid_response_with_refresh,
        jwt_data={"kivra_user_id": "9c8b7a6d-fake-actor-key"},
    )
    return tokens.load_tokens(FAKE_SSN)


@pytest.fixture
def expired_tokens_on_disk(tokens_dir, kivra_bankid_response_with_refresh, monkeypatch):
    """Pre-populates tokens_dir with a token file that expired 1 hour ago.
    Uses freezegun to write 'in the past' then unfreezes."""
    with freeze_time("2026-05-14T10:00:00Z"):  # write 2h before frozen_now
        from kivra import tokens
        tokens.save_tokens(
            ssn=FAKE_SSN,
            response=kivra_bankid_response_with_refresh,
            jwt_data={"kivra_user_id": "9c8b7a6d-fake-actor-key"},
        )
    # Outside the freeze, real now() is much later — expires_at is in the past
    from kivra import tokens
    return tokens.load_tokens(FAKE_SSN)


# ----- internal helpers -----


def _fake_id_token(kivra_user_id: str) -> str:
    """Builds a 3-part JWT-shaped string whose payload contains kivra_user_id.
    Signature segment is unverified by kivra-sync (per audit) so a bogus value is fine."""
    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload_json = json.dumps({"kivra_user_id": kivra_user_id, "sub": kivra_user_id}).encode()
    payload = base64.urlsafe_b64encode(payload_json).rstrip(b"=").decode()
    sig = "fakeSig"
    return f"{header}.{payload}.{sig}"
