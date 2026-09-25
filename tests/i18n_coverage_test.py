"""i18n coverage scan: zero English-string offenders on the JA UI.

Skip by default (expensive — launches headless browser). Run with:
    RUN_I18N_SCAN=1 pytest tests/i18n_coverage_test.py -v

Pre-requisite: build the SPA first so ``api/static/index.html`` exists.
Building alone is not enough — vite writes ``frontend/dist``, and ``make
bake`` is what copies that into ``api/static``:
    cd frontend && npm run build
    make bake
If ``api/static/index.html`` is absent the fixture skips with a clear
message rather than failing opaquely.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_I18N_SCAN") != "1",
    reason="set RUN_I18N_SCAN=1 to run (launches headless browser)",
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pytest.skip(
        "playwright not installed; run `poetry add --group dev playwright` and `playwright install chromium`",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Matches any run of 4+ ASCII letters — a candidate English token.
WORD_RE = re.compile(r"[a-zA-Z]{4,}")

ALLOWLIST_PATH = Path(__file__).parent / "i18n_coverage_allowlist.txt"

#: Path to the built SPA entry-point (relative to project root).
_STATIC_INDEX = Path("api/static/index.html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_allowlist() -> set[str]:
    """Return lowercase tokens from the allow-list file, ignoring comment lines."""
    if not ALLOWLIST_PATH.exists():
        return set()
    return {
        line.strip().lower()
        for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_server():
    """Launch uvicorn on a free port; yield the base URL.

    Requires ``api/static/index.html`` to exist (build the SPA first).
    Skips the entire module with a clear message if the SPA is not built.
    """
    static_index = Path(__file__).parent.parent / _STATIC_INDEX
    if not static_index.exists():
        pytest.skip(
            f"SPA not built — {_STATIC_INDEX} is missing. "
            "Run `cd frontend && npm run build && cd .. && make bake` first "
            "(the build alone writes frontend/dist; bake is what fills api/static), "
            "then re-run with RUN_I18N_SCAN=1."
        )

    port = _free_port()
    # Keep the server's output instead of discarding it: a startup that dies
    # (a missing provider key, an unreachable DB) is otherwise indistinguishable
    # from one that is merely slow, and both surface as the timeout below. A
    # file, not a PIPE, so a chatty startup cannot fill the buffer and wedge;
    # unnamed and closed on the way out because the runner this job uses is
    # persistent, so a leaked temp file per run accumulates there forever.
    with tempfile.TemporaryFile("w+") as log:
        proc = subprocess.Popen(
            # Not `poetry run`: it resolves its venv by cwd, so from a worktree it
            # starts an interpreter without the project and this fixture reports a
            # startup timeout instead of the real cause.
            [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port), "--no-access-log"],
            env={**os.environ, "ASK_INTENT_CACHE_ENABLED": "true"},
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        # Generous because ASK_INTENT_CACHE_ENABLED makes startup load the
        # sentence-transformers embedder, which dominates it: even with the model
        # already in the HuggingFace cache it revalidates revisions over the
        # network before loading, and on a cold cache it downloads the model
        # first. A tight bound here fails as "server did not start" on a slow
        # link or a loaded runner, pointing at the wrong thing entirely.
        deadline = time.time() + 180
        started = False
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                started = True
                break
            except Exception:
                time.sleep(0.5)

        if not started:
            proc.kill()
            log.seek(0)
            pytest.fail(f"API server did not start within 180 seconds. Server output:\n{log.read()}")

        yield f"http://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Page scanner
# ---------------------------------------------------------------------------


def _scan_page(page, url: str, allow: set[str]) -> list[str]:
    """Navigate to *url*, force JA locale, and return un-allowed English tokens."""
    # First visit — set the locale key before the page renders.
    # The SPA uses 'app.locale' as the localStorage key (see src/i18n/index.ts).
    page.goto(url, wait_until="networkidle", timeout=30_000)
    page.evaluate("() => { localStorage.setItem('app.locale', 'ja'); }")
    # Reload so the SPA picks up the stored locale on initialisation.
    page.reload(wait_until="networkidle", timeout=30_000)

    text: str = page.evaluate("() => document.body.innerText")

    offenders: list[str] = []
    for token in set(WORD_RE.findall(text)):
        if token.lower() not in allow:
            offenders.append(token)

    return sorted(offenders)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_i18n_no_english_leakage_on_ja(app_server: str) -> None:
    """All visible text on every SPA route must be free of un-allowed English tokens.

    The allow-list lives in ``tests/i18n_coverage_allowlist.txt``.  Add
    legitimate proper nouns or technical tokens there rather than weakening
    the regex.
    """
    base = app_server
    allow = _load_allowlist()

    urls = [
        f"{base}/agencies/1/",
        f"{base}/agencies/1/ask",
        f"{base}/agencies/1/reports",
        f"{base}/agencies/1/live",
    ]

    all_offenders: dict[str, list[str]] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "ja,ja-JP;q=0.9"})
            for url in urls:
                offenders = _scan_page(page, url, allow)
                if offenders:
                    all_offenders[url] = offenders
        finally:
            browser.close()

    assert not all_offenders, "English-string offenders found in JA UI:\n" + "\n".join(
        f"  {url}:\n    " + "\n    ".join(sorted(offenders)) for url, offenders in all_offenders.items()
    )
