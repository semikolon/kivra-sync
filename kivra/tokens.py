"""Token persistence for kivra-sync fork.

Per spec at ~/Projects/din-mamma/.claude/specs/kivra-sync-fork-refresh-tokens/.

Stores tokens at $KIVRA_SYNC_TOKENS_DIR/tokens-{sha256(ssn)[:8]}.json (default
~/.local/share/kivra-sync/...), mode 0600. Refuses to read files with more
permissive modes. Per-SSN-hash filename isolation. Tolerant load (returns None
for any failure: missing/empty/malformed/schema-invalid/mode-wrong).
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _tokens_dir() -> Path:
    """Resolved per-call so env-var overrides take effect under tests."""
    env = os.environ.get("KIVRA_SYNC_TOKENS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "kivra-sync"


def _ssn_hash(ssn: str) -> str:
    """First 8 hex chars of SHA-256(ssn). Stable per-SSN identifier; SSN never on disk."""
    return hashlib.sha256(ssn.encode("utf-8")).hexdigest()[:8]


def tokens_path(ssn: str) -> Path:
    """Returns the file path where tokens for this SSN live."""
    return _tokens_dir() / f"tokens-{_ssn_hash(ssn)}.json"


def load_tokens(ssn: str) -> Optional[dict]:
    """Returns parsed token dict, or None for any failure mode.

    Failure modes (all return None, never raise):
        - file missing
        - file empty
        - mode more permissive than 0600 (also logs ERROR — refuses to read)
        - malformed JSON
        - schema-invalid (not a dict, or missing access_token)
    """
    path = tokens_path(ssn)
    if not path.exists():
        logger.info("No tokens file at %s; need fresh BankID auth", path)
        return None

    try:
        mode = path.stat().st_mode & 0o777
    except OSError as e:
        logger.warning("Cannot stat tokens file %s: %s", path, e)
        return None

    # Refuse if group/other have any access (mode > 0600)
    if mode & 0o077:
        logger.error(
            "Refusing to read %s: permissions are %s (must be 0600). "
            "Token file may have been tampered with — investigate manually before retrying.",
            path,
            oct(mode),
        )
        return None

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read tokens file %s: %s", path, e)
        return None

    if not content.strip():
        logger.info("Tokens file %s is empty; need fresh BankID auth", path)
        return None

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(
            "Tokens file %s is malformed JSON: %s. Preserving for inspection; "
            "falling back to BankID.",
            path,
            e,
        )
        return None

    if not isinstance(data, dict) or "access_token" not in data:
        logger.warning(
            "Tokens file %s lacks required fields (need dict with access_token); "
            "falling back to BankID.",
            path,
        )
        return None

    return data


def save_tokens(
    ssn: str,
    response: dict,
    jwt_data: dict,
    prior: Optional[dict] = None,
) -> None:
    """Persists tokens atomically with mode 0600.

    Args:
        ssn: User SSN (hashed for filename; not stored in content).
        response: Kivra /v2/oauth2/token response dict. MUST contain `access_token`.
            May contain `refresh_token`, `expires_in`, `token_type`, etc.
        jwt_data: Decoded JWT id_token payload (must contain `kivra_user_id`).
        prior: Previously-loaded tokens dict, if any. If `response` omits
            `refresh_token` but `prior` carries one, the prior refresh_token is
            preserved (non-rotating server case). If both lack one, saved value is None.

    Raises:
        ValueError: if `response` lacks `access_token`.
    """
    if "access_token" not in response:
        raise ValueError("response missing access_token")

    now = datetime.now(timezone.utc)
    expires_in = response.get("expires_in") or 3600
    expires_at = now + timedelta(seconds=int(expires_in))

    # Refresh-token preservation logic (FR-2)
    refresh_token = response.get("refresh_token")
    refresh_obtained_at: Optional[str] = None
    if refresh_token:
        refresh_obtained_at = now.isoformat()
    elif prior is not None and prior.get("refresh_token"):
        refresh_token = prior.get("refresh_token")
        refresh_obtained_at = prior.get("refresh_token_obtained_at")

    data = {
        "schema_version": 1,
        "ssn_sha256_8": _ssn_hash(ssn),
        "access_token": response["access_token"],
        "access_token_obtained_at": now.isoformat(),
        "access_token_expires_at": expires_at.isoformat(),
        "refresh_token": refresh_token,
        "refresh_token_obtained_at": refresh_obtained_at,
        "actor_key": jwt_data.get("kivra_user_id"),
        "id_token_jwt_data": jwt_data,
    }

    path = tokens_path(ssn)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Atomic write: open with O_CREAT mode 0o600, write to .tmp, chmod, rename.
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Belt + suspenders: chmod again post-write in case umask interfered.
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

    if refresh_token:
        logger.info(
            "Saved tokens for ssn=%s (access expires %s, refresh present)",
            _ssn_hash(ssn),
            expires_at.isoformat(),
        )
    else:
        logger.info(
            "Saved tokens for ssn=%s with NO refresh_token (next run will require BankID)",
            _ssn_hash(ssn),
        )


def is_access_token_expired(tokens: dict, skew_seconds: int = 30) -> bool:
    """Returns True if access_token is expired or expires within `skew_seconds`.

    Defensive: returns True if `access_token_expires_at` is missing or unparseable.
    """
    expires_at_str = tokens.get("access_token_expires_at")
    if not expires_at_str or not isinstance(expires_at_str, str):
        return True
    try:
        # datetime.fromisoformat handles offset suffix only in some Python versions;
        # normalize 'Z' → '+00:00' for older runtimes.
        normalized = expires_at_str.replace("Z", "+00:00")
        expires_at = datetime.fromisoformat(normalized)
    except (ValueError, AttributeError):
        return True

    if expires_at.tzinfo is None:
        # Assume UTC if naive (shouldn't happen with our writer, but be defensive)
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    return expires_at <= now + timedelta(seconds=skew_seconds)


def has_refresh_token(tokens: dict) -> bool:
    """True iff `refresh_token` field is present and truthy (non-empty str)."""
    return bool(tokens.get("refresh_token"))


def delete_tokens(ssn: str) -> None:
    """Removes the tokens file for this SSN. Idempotent on missing file."""
    path = tokens_path(ssn)
    try:
        path.unlink()
    except FileNotFoundError:
        pass  # idempotent
