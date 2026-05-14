"""Tests for kivra/tokens.py — token persistence, expiry, file mode.

Per spec FR-1, FR-2, FR-3, FR-4, FR-8, FR-9, FR-10.
"""

import json
import os
import stat
from pathlib import Path

import pytest
from freezegun import freeze_time

from kivra import tokens
from tests.conftest import FAKE_SSN, FAKE_SSN_ALT


# ----- save_tokens & file structure -----


def test_save_tokens_creates_file_with_mode_0600(tokens_dir, kivra_bankid_response_with_refresh):
    """FR-9: tokens file is created with mode 0600 (owner read/write only)."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})

    path = tokens.tokens_path(FAKE_SSN)
    assert path.exists()
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected mode 0600, got {oct(mode)}"


def test_save_tokens_auto_creates_parent_dir(tmp_path, monkeypatch, kivra_bankid_response_with_refresh):
    """save_tokens creates intermediate directories if missing."""
    nested = tmp_path / "deep" / "nested" / "dir"
    monkeypatch.setenv("KIVRA_SYNC_TOKENS_DIR", str(nested))

    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})

    assert nested.exists()
    assert tokens.tokens_path(FAKE_SSN).exists()


def test_save_tokens_writes_parseable_json(tokens_dir, kivra_bankid_response_with_refresh):
    """Saved file is valid JSON containing the documented schema."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})

    path = tokens.tokens_path(FAKE_SSN)
    data = json.loads(path.read_text())

    assert data["schema_version"] == 1
    assert data["access_token"] == "atk_initial_xxxx"
    assert data["refresh_token"] == "rtk_initial_xxxx"
    assert data["actor_key"] == "abc"
    assert "access_token_expires_at" in data
    assert "access_token_obtained_at" in data


def test_save_tokens_overwrites_existing_file(tokens_dir, kivra_bankid_response_with_refresh):
    """Re-save replaces the prior file's contents (no merge)."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})

    second = {**kivra_bankid_response_with_refresh, "access_token": "atk_second"}
    tokens.save_tokens(FAKE_SSN, second, {"kivra_user_id": "abc"})

    loaded = tokens.load_tokens(FAKE_SSN)
    assert loaded["access_token"] == "atk_second"


def test_per_ssn_hash_filename_isolation(tokens_dir, kivra_bankid_response_with_refresh):
    """FR-8: different SSNs map to different files; no collision."""
    a = kivra_bankid_response_with_refresh
    b = {**a, "access_token": "atk_for_ssn_b"}

    tokens.save_tokens(FAKE_SSN, a, {"kivra_user_id": "user_a"})
    tokens.save_tokens(FAKE_SSN_ALT, b, {"kivra_user_id": "user_b"})

    loaded_a = tokens.load_tokens(FAKE_SSN)
    loaded_b = tokens.load_tokens(FAKE_SSN_ALT)

    assert loaded_a["access_token"] == "atk_initial_xxxx"
    assert loaded_b["access_token"] == "atk_for_ssn_b"
    assert tokens.tokens_path(FAKE_SSN) != tokens.tokens_path(FAKE_SSN_ALT)


def test_save_tokens_preserves_prior_refresh_on_non_rotating_response(
    tokens_dir, kivra_bankid_response_with_refresh, kivra_refresh_response_non_rotating
):
    """FR-2: server omits refresh_token in refresh response → prior refresh is preserved."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})
    prior = tokens.load_tokens(FAKE_SSN)

    # Now save a refresh response that lacks refresh_token, with prior supplied
    tokens.save_tokens(
        FAKE_SSN,
        kivra_refresh_response_non_rotating,
        {"kivra_user_id": "abc"},
        prior=prior,
    )

    loaded = tokens.load_tokens(FAKE_SSN)
    assert loaded["access_token"] == "atk_refreshed_yyyy"  # new
    assert loaded["refresh_token"] == "rtk_initial_xxxx"  # preserved from prior


def test_save_tokens_no_refresh_no_prior_persists_null(tokens_dir, kivra_bankid_response_no_refresh):
    """FR-3: no refresh_token from Kivra, no prior → saved with refresh_token=None."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_no_refresh, {"kivra_user_id": "abc"})

    loaded = tokens.load_tokens(FAKE_SSN)
    assert loaded["refresh_token"] is None


# ----- load_tokens & failure modes -----


def test_load_tokens_missing_file_returns_none(tokens_dir):
    """FR-10: missing token file returns None (no exception)."""
    assert tokens.load_tokens(FAKE_SSN) is None


def test_load_tokens_empty_file_returns_none(tokens_dir):
    """FR-10: empty file returns None."""
    path = tokens.tokens_path(FAKE_SSN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    # Empty file (0 bytes)

    assert tokens.load_tokens(FAKE_SSN) is None


def test_load_tokens_malformed_json_returns_none(tokens_dir):
    """FR-10: malformed JSON returns None (does NOT crash; does NOT delete the file)."""
    path = tokens.tokens_path(FAKE_SSN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{this is not valid json")
    os.chmod(path, 0o600)

    assert tokens.load_tokens(FAKE_SSN) is None
    # Preserved for inspection
    assert path.exists()


def test_load_tokens_schema_invalid_returns_none(tokens_dir):
    """FR-10: JSON valid but missing required fields returns None."""
    path = tokens.tokens_path(FAKE_SSN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"unrelated": "data"}))
    os.chmod(path, 0o600)

    assert tokens.load_tokens(FAKE_SSN) is None


def test_load_tokens_refuses_mode_0644(tokens_dir, kivra_bankid_response_with_refresh):
    """FR-9: file with permissive mode (0644) is refused; returns None + ERROR logged."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})
    path = tokens.tokens_path(FAKE_SSN)
    os.chmod(path, 0o644)  # tamper

    assert tokens.load_tokens(FAKE_SSN) is None


# ----- expiry detection (FR-4) -----


@pytest.mark.parametrize(
    "expires_at,frozen,expected",
    [
        # Token expired in the past
        ("2026-05-14T11:59:00Z", "2026-05-14T12:00:00Z", True),
        # Token expires well into the future
        ("2026-05-14T13:00:00Z", "2026-05-14T12:00:00Z", False),
        # Within 30s skew window — treated as expired (defensive)
        ("2026-05-14T12:00:20Z", "2026-05-14T12:00:00Z", True),
        # Just past skew window — not expired
        ("2026-05-14T12:01:00Z", "2026-05-14T12:00:00Z", False),
    ],
)
def test_is_access_token_expired_with_explicit_expiry(expires_at, frozen, expected):
    """Skew-aware expiry comparison (30s default buffer)."""
    with freeze_time(frozen):
        result = tokens.is_access_token_expired({"access_token_expires_at": expires_at})
    assert result is expected


def test_is_access_token_expired_missing_field_defensive_true():
    """If access_token_expires_at field is missing/malformed, treat as expired (defensive)."""
    assert tokens.is_access_token_expired({}) is True
    assert tokens.is_access_token_expired({"access_token_expires_at": None}) is True
    assert tokens.is_access_token_expired({"access_token_expires_at": "not a date"}) is True


# ----- has_refresh_token (FR-1) -----


@pytest.mark.parametrize(
    "value,expected",
    [
        ("rtk_valid_token", True),
        (None, False),
        ("", False),
    ],
)
def test_has_refresh_token(value, expected):
    """Refresh-token presence check: truthy non-empty string only."""
    assert tokens.has_refresh_token({"refresh_token": value}) is expected


def test_has_refresh_token_missing_key():
    """Missing the refresh_token key entirely → False."""
    assert tokens.has_refresh_token({}) is False


# ----- roundtrip + delete -----


def test_save_load_roundtrip(tokens_dir, kivra_bankid_response_with_refresh):
    """save → load preserves the salient fields."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})
    loaded = tokens.load_tokens(FAKE_SSN)

    assert loaded["access_token"] == "atk_initial_xxxx"
    assert loaded["refresh_token"] == "rtk_initial_xxxx"
    assert loaded["actor_key"] == "abc"
    assert loaded["schema_version"] == 1


def test_delete_tokens_removes_file_and_idempotent(tokens_dir, kivra_bankid_response_with_refresh):
    """delete_tokens removes the file; second call on missing file does not raise."""
    tokens.save_tokens(FAKE_SSN, kivra_bankid_response_with_refresh, {"kivra_user_id": "abc"})
    path = tokens.tokens_path(FAKE_SSN)
    assert path.exists()

    tokens.delete_tokens(FAKE_SSN)
    assert not path.exists()

    # Idempotent
    tokens.delete_tokens(FAKE_SSN)  # should not raise
