"""Tests for kivra/config.py — SSN sourcing + validation.

Per spec FR-6, FR-7.
"""

import pytest

from kivra import config
from tests.conftest import FAKE_SSN


def test_get_ssn_uses_env_when_no_argv(clean_env):
    """FR-6: KIVRA_SSN env var is the primary source."""
    clean_env.setenv("KIVRA_SSN", FAKE_SSN)
    assert config.get_ssn(argv_ssn=None) == FAKE_SSN


def test_get_ssn_argv_overrides_env(clean_env):
    """FR-6: argv positional SSN wins over env (testing/override path)."""
    clean_env.setenv("KIVRA_SSN", "190001011234")
    assert config.get_ssn(argv_ssn="200012319876") == "200012319876"


def test_get_ssn_both_missing_exits_clearly(clean_env):
    """FR-6: missing both → SystemExit with a message naming both sources."""
    clean_env.delenv("KIVRA_SSN", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        config.get_ssn(argv_ssn=None)
    # Message should mention both KIVRA_SSN env and argv
    msg = str(exc_info.value).lower()
    assert "kivra_ssn" in msg
    assert ("argv" in msg or "argument" in msg or "command" in msg)


@pytest.mark.parametrize(
    "bad_ssn",
    [
        "12345",  # too short
        "12345678901",  # 11 digits
        "1234567890123",  # 13 digits
        "ABCDEFGHIJKL",  # non-numeric, right length
        "1900-01-01-12",  # contains hyphens
        "1900 01 01 1234",  # contains spaces in middle
    ],
)
def test_get_ssn_format_invalid_exits(clean_env, bad_ssn):
    """FR-7: malformed SSN values → SystemExit with format error."""
    clean_env.setenv("KIVRA_SSN", bad_ssn)
    with pytest.raises(SystemExit):
        config.get_ssn(argv_ssn=None)


def test_get_ssn_strips_whitespace(clean_env):
    """FR-7: leading/trailing whitespace in env value is stripped before validation."""
    clean_env.setenv("KIVRA_SSN", f"  {FAKE_SSN}  ")
    assert config.get_ssn(argv_ssn=None) == FAKE_SSN


def test_get_ssn_strips_trailing_newline(clean_env):
    """FR-7: trailing newline (common from `source .env` quirks) is stripped."""
    clean_env.setenv("KIVRA_SSN", FAKE_SSN + "\n")
    assert config.get_ssn(argv_ssn=None) == FAKE_SSN


def test_get_ssn_empty_env_treated_as_missing(clean_env):
    """FR-6: empty env value falls through to argv; if argv also missing → exit."""
    clean_env.setenv("KIVRA_SSN", "")
    with pytest.raises(SystemExit):
        config.get_ssn(argv_ssn=None)


def test_get_ssn_empty_env_falls_through_to_argv(clean_env):
    """Empty env doesn't shadow valid argv."""
    clean_env.setenv("KIVRA_SSN", "")
    assert config.get_ssn(argv_ssn=FAKE_SSN) == FAKE_SSN
