#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""LocalHtmlInteractionProvider — the self-refreshing file:// QR viewer.

Why this exists: the default `local` provider does `PIL.Image.show()`, which
opens a STATIC image in the OS viewer (macOS Preview). Kivra's QR is valid
only for the auth order's lifetime; a distracted user returns to a dead
Preview window with zero feedback (observed 2026-05-18: `start_failed`).
This provider mirrors din-mamma's proven Lowell `FileQrSurface`: a
self-contained, state-aware, auto-refreshing local `qr.html` (no HTTP
server, no Preview) that transitions scanning → authorized → done.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interaction.base import InteractionProvider
from interaction.local_html import LocalHtmlInteractionProvider


@pytest.fixture
def qr_png(tmp_path: Path) -> Path:
    p = tmp_path / "kivra_qr.png"
    # Minimal valid 1x1 PNG (provider only copies bytes; never decodes).
    p.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000"
            "00907753de0000000a49444154789c6360000000020001e221bc3300"
            "00000049454e44ae426082"
        )
    )
    return p


def test_is_an_interaction_provider():
    assert issubclass(LocalHtmlInteractionProvider, InteractionProvider)


def test_display_qr_code_writes_self_refreshing_viewer(qr_png: Path):
    prov = LocalHtmlInteractionProvider(auto_open=False)
    prov.display_qr_code(str(qr_png))

    out = qr_png.parent
    html = (out / "qr.html").read_text(encoding="utf-8")
    state = (out / "qr_state.js").read_text(encoding="utf-8")

    # PNG copied next to the viewer (relative ref works on file://).
    assert (out / "qr.png").read_bytes() == qr_png.read_bytes()
    # Self-refreshing + state-aware (the proven Lowell pattern).
    assert 'src="qr.png?"' in html
    assert 'src="qr_state.js?"' in html
    assert "Skanna med BankID" in html  # calm-tech Swedish
    assert '"scanning"' in state


def test_lifecycle_transitions_flip_viewer_state(qr_png: Path, capsys):
    prov = LocalHtmlInteractionProvider(auto_open=False)
    prov.display_qr_code(str(qr_png))
    state_js = qr_png.parent / "qr_state.js"

    prov.report_authentication_success()
    assert '"authorized"' in state_js.read_text(encoding="utf-8")

    prov.report_completion(
        {
            "receipts_total": 0,
            "receipts_fetched": 0,
            "receipts_stored": 0,
            "letters_total": 3,
            "letters_fetched": 3,
            "letters_stored": 2,
        }
    )
    assert '"done"' in state_js.read_text(encoding="utf-8")
    # Console summary parity with LocalInteractionProvider is preserved.
    assert "Letters: 2 new" in capsys.readouterr().out


def test_never_raises_on_bad_path():
    """Best-effort: a viewer-write failure must never derail the auth flow."""
    prov = LocalHtmlInteractionProvider(auto_open=False)
    prov.display_qr_code("/nonexistent/dir/that/cannot/be/made/\0/qr.png")
    prov.report_authentication_success()
    prov.report_completion({"receipts_total": 0, "letters_total": 0})


def test_selectable_via_cli_choice():
    """`local_html` is a registered --interaction-provider choice (proven via
    the CLI's own --help; no main() refactor / no network)."""
    import subprocess
    import sys
    from pathlib import Path as _P

    root = _P(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, str(root / "kivra_sync.py"), "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "local_html" in out.stdout, out.stdout[-500:]
