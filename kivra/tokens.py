"""Token persistence — STUB for RED phase. Implementation lands in Phase 2."""

from pathlib import Path


def tokens_path(ssn: str) -> Path:
    raise NotImplementedError("tokens.tokens_path — Phase 2")


def load_tokens(ssn: str):
    raise NotImplementedError("tokens.load_tokens — Phase 2")


def save_tokens(ssn: str, response: dict, jwt_data: dict, prior=None) -> None:
    raise NotImplementedError("tokens.save_tokens — Phase 2")


def is_access_token_expired(tokens: dict, skew_seconds: int = 30) -> bool:
    raise NotImplementedError("tokens.is_access_token_expired — Phase 2")


def has_refresh_token(tokens: dict) -> bool:
    raise NotImplementedError("tokens.has_refresh_token — Phase 2")


def delete_tokens(ssn: str) -> None:
    raise NotImplementedError("tokens.delete_tokens — Phase 2")
