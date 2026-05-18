#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Self-refreshing local file:// QR viewer interaction provider.

`LocalInteractionProvider` does `PIL.Image.show()` — a STATIC image in the OS
viewer (macOS Preview). Kivra's QR is valid only for the auth order's
lifetime, so a distracted user returns to a dead Preview window with zero
feedback (observed 2026-05-18 from din-mamma `dim sync`: `start_failed`).

This provider mirrors din-mamma's proven Lowell `FileQrSurface`: a
self-contained, state-aware, auto-refreshing local `qr.html` (NO HTTP server,
NO Preview) opened in the browser. The QR `<img>` cache-busts `qr.png` every
800 ms; a 1 s loop hot-loads `qr_state.js` so the same open tab transitions
scanning → authorized → done without the user staring at a stale code.
Everything is best-effort — a viewer-write failure must never derail the
BankID/auth flow.
"""

from __future__ import annotations

import logging
import shutil
import webbrowser
from pathlib import Path

from interaction.base import InteractionProvider

# Ported verbatim from din-mamma portal_scraper.base.FileQrSurface._VIEWER
# (battle-tested on the Lowell Sensor-5 path). Self-contained, no external
# assets, file://-safe (fetch/XHR are CORS-blocked on file://; <img src> /
# <script src> are not). Calm-tech Swedish register.
_VIEWER = """<!doctype html><html lang=sv><meta charset=utf-8>
<title>BankID - Din Mamma</title>
<style>
 body{display:grid;place-items:center;min-height:100vh;margin:0;
  font-family:system-ui,-apple-system,sans-serif;background:#fafafa;color:#1a1a1a}
 .box{text-align:center;max-width:22rem;padding:1rem}
 h3{font-weight:600;margin:0 0 .75rem;font-size:1.15rem}
 p{color:#6b6b6b;margin:.5rem 0 0}
 #q{width:300px;height:300px;image-rendering:pixelated}
 .hidden{display:none}
 .spin{width:34px;height:34px;margin:.25rem auto 0;border:3px solid #e6e6e6;
  border-top-color:#9a9a9a;border-radius:50%;animation:r 1s linear infinite}
 @keyframes r{to{transform:rotate(360deg)}}
</style>
<body>
<div id=scan class=box><h3>Skanna med BankID-appen</h3>
 <img id=q alt="BankID QR"><p>uppdateras automatiskt</p></div>
<div id=auth class="box hidden"><h3>Inloggning klar</h3>
 <div class=spin></div><p>Hämtar dina ärenden…</p></div>
<div id=done class="box hidden"><h3>Klart</h3>
 <p>Inloggningen lyckades. Du kan stänga den här fliken.</p></div>
<div id=failed class="box hidden"><h3>Ingen inloggning registrerades</h3>
 <p>Tidsgränsen gick ut. Stäng fliken och kör `dim sync` igen.</p></div>
<script>
var S="scanning",term=false;
function show(id){["scan","auth","done","failed"].forEach(function(x){
 document.getElementById(x).classList.toggle("hidden",x!==id);});}
function render(st){
 if(term||!st||st===S)return; S=st;
 if(st==="authorized"){show("auth");}
 else if(st==="done"){show("done");term=true;try{window.close();}catch(e){}}
 else if(st==="failed"){show("failed");term=true;}}
setInterval(function(){ if(S==="scanning")
 document.getElementById("q").src="qr.png?"+Date.now(); },800);
setInterval(function(){ if(term)return;
 var o=document.getElementById("st"); if(o)o.remove();
 var s=document.createElement("script"); s.id="st";
 s.src="qr_state.js?"+Date.now();
 s.onload=function(){render(window.__dimState);};
 s.onerror=function(){}; document.body.appendChild(s); },1000);
</script>
"""


class LocalHtmlInteractionProvider(InteractionProvider):
    """Self-refreshing file:// QR viewer (the Lowell-style fix).

    Selectable via `--interaction-provider local_html`. din-mamma's Sensor-1
    `dim sync` uses this instead of `local` so the QR window auto-refreshes
    and shows its own success/timeout state.
    """

    def __init__(self, *, auto_open: bool = True) -> None:
        # auto_open=False in tests so the suite never spawns a browser.
        self._auto_open = auto_open
        self._dir: Path | None = None

    def _write_state(self, state: str) -> None:
        """Atomically publish viewer state to qr_state.js. Best-effort —
        never raises (a viewer failure must not derail auth/sync)."""
        if self._dir is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / "qr_state.js.tmp"
            tmp.write_text(f'window.__dimState="{state}";\n', encoding="utf-8")
            tmp.replace(self._dir / "qr_state.js")
        except Exception:
            return

    def display_qr_code(self, qr_image_path):
        """Write the self-refreshing viewer next to the QR PNG and open it
        in the browser (not Preview). Best-effort; falls back to a clear
        console line if anything fails."""
        try:
            src = Path(qr_image_path)
            self._dir = src.parent
            self._dir.mkdir(parents=True, exist_ok=True)
            # Copy the QR next to the viewer (relative ref works on file://),
            # atomically so the 800 ms poll never sees a half-written frame.
            png = self._dir / "qr.png"
            tmp = self._dir / "qr.png.tmp"
            shutil.copyfile(src, tmp)
            tmp.replace(png)
            html = self._dir / "qr.html"
            html.write_text(_VIEWER, encoding="utf-8")
            self._write_state("scanning")
            print(f"QR-kod visas i en självuppdaterande webbsida: {html}")
            if self._auto_open:
                try:
                    webbrowser.open(html.as_uri())
                except Exception as e:  # noqa: BLE001 - never derail auth
                    logging.warning("Could not auto-open QR viewer: %s", e)
        except Exception as e:  # noqa: BLE001 - never derail auth
            logging.error("Could not render self-refreshing QR viewer: %s", e)
            print(f"QR code saved as '{qr_image_path}'")

    def report_authentication_success(self):
        """BankID scan succeeded; data sync starting. Flip the open tab to
        the 'authorized' state + keep console parity with LocalInteraction."""
        self._write_state("authorized")
        print("BankID authentication successful! Starting data sync...")

    def report_completion(self, stats):
        """Sync done. Flip the viewer to 'done' (auto-closes the tab) and
        print the same summary LocalInteractionProvider does."""
        self._write_state("done")
        print("\nAll done!")
        receipts_total = stats.get("receipts_total", 0)
        receipts_fetched = stats.get("receipts_fetched", 0)
        letters_total = stats.get("letters_total", 0)
        letters_fetched = stats.get("letters_fetched", 0)
        receipts_count = (
            receipts_total
            if receipts_fetched == receipts_total
            else f"{receipts_fetched} of {receipts_total}"
        )
        letters_count = (
            letters_total
            if letters_fetched == letters_total
            else f"{letters_fetched} of {letters_total}"
        )
        print(f"Receipts: {stats.get('receipts_stored', 0)} new items, {receipts_count} fetched")
        print(f"Letters: {stats.get('letters_stored', 0)} new items, {letters_count} fetched")
