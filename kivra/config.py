"""SSN sourcing + validation.

Per spec FR-6, FR-7 at ~/Projects/din-mamma/.claude/specs/kivra-sync-fork-refresh-tokens/.

Priority: argv > env KIVRA_SSN > error.
SSN format: 12 digits exactly (YYYYMMDDXXXX). Whitespace stripped before validation.
"""

import os
import re
import sys
from typing import Optional

SSN_PATTERN = re.compile(r"^\d{12}$")


def get_ssn(argv_ssn: Optional[str]) -> str:
    """Returns validated SSN.

    Priority:
        1. argv_ssn if non-empty after strip
        2. env var KIVRA_SSN if non-empty after strip
        3. SystemExit with clear message

    Raises:
        SystemExit: if neither source provides a value, or value fails format check.
    """
    # Priority 1: argv (override path; primarily for tests + upstream compat)
    if argv_ssn:
        candidate = argv_ssn.strip()
        if candidate:
            return _validate(candidate)

    # Priority 2: env (production path)
    env_value = os.environ.get("KIVRA_SSN", "").strip()
    if env_value:
        return _validate(env_value)

    # Priority 3: no source — exit clearly
    sys.exit(
        "ERROR: SSN not provided. Set KIVRA_SSN env var "
        "(e.g., add to ~/.secrets/tier-mac.env and source ~/.secrets/load-all.sh) "
        "or pass as positional command-line argument: "
        "python kivra_sync.py YYYYMMDDXXXX"
    )


def _validate(ssn: str) -> str:
    if not SSN_PATTERN.match(ssn):
        sys.exit(
            f"ERROR: Invalid SSN format. Expected 12 digits (YYYYMMDDXXXX); "
            f"got {len(ssn)} characters."
        )
    return ssn
