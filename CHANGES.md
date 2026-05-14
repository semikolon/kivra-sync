# Changes from upstream `felixandersen/kivra-sync`

This fork (`semikolon/kivra-sync`) extends upstream to power the Kivra sensor
in `semikolon/din-mamma` (a personal-finance guardian agent for Swedish
consumers). All changes are additive; the upstream CLI remains backward-compatible.

Upstream remote tracked as `upstream` (`git remote -v`). Rebase onto upstream
periodically; consider proposing a refresh-token PR to upstream once design
proves out in production.

## Functional changes

### 1. Refresh-token persistence and reuse

Upstream's `KivraAuth.authenticate()` extracts only `access_token` + `actor_key`
from Kivra's `/v2/oauth2/token` response, discarding `refresh_token` if present.
Every run therefore triggers a fresh BankID QR scan, even when Kivra has issued
a long-lived refresh token.

The fork captures the full token response, persists it to
`~/.local/share/kivra-sync/tokens-{ssn_sha256_8}.json` (mode 0600), and on
subsequent runs:

1. Reuses a still-valid cached `access_token` (zero network)
2. Uses the cached `refresh_token` to obtain a new `access_token` (one HTTP call)
3. Falls back to the BankID QR flow only when both above paths fail

Refresh-token rotation is handled correctly — if Kivra returns a new
`refresh_token` in the refresh response, it is persisted; otherwise the prior
one is preserved (non-rotating server case). If Kivra never issues a
refresh_token at all, this is logged clearly and behaviour degrades gracefully
to per-run BankID.

New module: `kivra/tokens.py`. New methods on `KivraAuth`:
`authenticate_with_refresh_fallback`, `_try_refresh`, `_auth_dict_from_cached`.

### 2. SSN sourced from env var by default

Upstream requires the SSN as a positional CLI argument, exposing it in
`ps aux` and shell history. The fork makes the positional optional and reads
`KIVRA_SSN` env var as the primary source:

```bash
# preferred (env-based; no SSN in ps/history):
export KIVRA_SSN=YYYYMMDDXXXX  # typically from ~/.secrets/tier-mac.env
python kivra_sync.py

# upstream-compatible (still works):
python kivra_sync.py YYYYMMDDXXXX
```

New module: `kivra/config.py`. Argv overrides env (testing convenience).
Format-validates SSN regardless of source.

### 3. Comprehensive test coverage (TDD)

55 unit tests covering every refresh/fallback branch, every token persistence
edge case, and SSN sourcing. Coverage on new modules:

| Module | Coverage |
|---|---|
| `kivra/tokens.py` | 98% |
| `kivra/config.py` | 100% |
| `kivra/auth.py` augmentation | ~97% (existing BankID flow is mock-bypassed in unit tests) |

Test deps in `requirements-dev.txt`: `pytest`, `responses`, `freezegun`,
`pytest-cov`. All HTTP mocked; no network in unit tests; full suite <0.5s.

Run: `pytest tests/ -v` or `pytest tests/ --cov=kivra.tokens --cov=kivra.config`.

## Unchanged

- All upstream GraphQL queries (`ContentList`, `Receipts`, `ReceiptDetails`)
- Filesystem + Paperless-ngx storage providers
- Local / web / ntfy interaction providers
- Letter + receipt fetchers and pagination logic
- WeasyPrint HTML→PDF pipeline for `text/html` parts
- Docker, Nix flake, and CI (`.github/workflows/release.yml`) setup
- Public Kivra `client_id` (no change to OAuth2 PKCE flow itself)
