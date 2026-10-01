"""Log in through a real browser and borrow the session's bearer token.

The Auriga portal is a single-page app behind the school's SSO. It talks to its
API with an `Authorization: Bearer ...` header and no cookies, and the token only
ever exists in the page's memory. So we open the portal in a visible browser, let
the user sign in on the school's own page (this tool never sees a password), and
read the header off the app's first authenticated API request.

The browser profile is persisted, so most runs are already signed in and need no
interaction. The token is kept in memory only and never written to disk.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .config import CONFIG_DIR

PROFILE_DIR = CONFIG_DIR / "browser-profile"

# How long we wait for a sign-in to complete before giving up.
LOGIN_TIMEOUT_S = 300
# After this long without a token we nudge the SPA to re-request its data.
NUDGE_AFTER_S = 8.0


class LoginError(RuntimeError):
    """No authenticated API request was observed in time."""


def token_expiry(token: str) -> datetime | None:
    """Best-effort expiry time of a JWT bearer token (None if it is not one)."""
    try:
        payload = token.split(" ", 1)[-1].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload))["exp"]
        return datetime.fromtimestamp(int(exp), tz=UTC)
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def _nudge_script(hash_route: str) -> str:
    """Re-trigger the SPA router without reloading the page.

    A full reload could wipe a half-typed SSO form; a hash change cannot. It
    exists because a restored session may land on a view that makes no API call.
    """
    return (
        "(() => {"
        f"const target = {json.dumps(hash_route)};"
        "if (location.hash === target) {"
        "  location.hash = '#/';"
        "  setTimeout(() => { location.hash = target; }, 250);"
        "} else { location.hash = target; }"
        "})()"
    )


def _candidate_paths() -> list[str]:
    if sys.platform == "darwin":
        apps = "/Applications"
        return [
            f"{apps}/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"{apps}/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            f"{apps}/Brave Browser.app/Contents/MacOS/Brave Browser",
            f"{apps}/Chromium.app/Contents/MacOS/Chromium",
        ]
    if sys.platform == "win32":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        suffixes = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        return [os.path.join(r, s) for r in roots if r for s in suffixes]
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
             "microsoft-edge", "brave-browser"]
    return [path for name in names if (path := shutil.which(name))]


def find_browser(preference: str = "auto") -> str:
    """Path of a Chromium-family browser: an explicit path/command, else the first one installed."""
    if preference and preference != "auto":
        if Path(preference).exists():
            return preference
        found = shutil.which(preference)
        if found:
            return found
        raise LoginError(f"Browser not found: {preference}")
    for candidate in _candidate_paths():
        if Path(candidate).exists():
            return candidate
    raise LoginError(
        "No Chrome, Edge, Brave or Chromium found. Install one, or set [portal] browser "
        "in the config to its path."
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_debug_port(port: int, process: subprocess.Popen, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LoginError(
                "The browser closed immediately. If another window already uses the "
                f"profile ({PROFILE_DIR}), close it and try again."
            )
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).close()
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    raise LoginError("The browser did not open its debugging port in time.")


def acquire_token(
    url: str,
    *,
    browser: str = "auto",
    profile_dir: Path = PROFILE_DIR,
    timeout_s: int = LOGIN_TIMEOUT_S,
) -> str:
    """Open the portal in a normal browser window, wait for a sign-in, return the token.

    The browser is started as a plain process, exactly as if the user had opened it,
    with no automation switches: SSO-protected sites can stall on browsers that
    advertise being remote-controlled. We only attach to it afterwards, over the
    DevTools protocol, to watch the app's network requests.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    executable = find_browser(browser)
    api_host = urlsplit(url).netloc
    hash_route = "#" + urlsplit(url).fragment if urlsplit(url).fragment else "#/"
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    found: list[str] = []

    def on_request(request) -> None:
        if api_host in request.url and "/api/" in request.url:
            value = request.headers.get("authorization", "").strip()
            if value:
                found.append(value)

    process = subprocess.Popen(
        [
            executable,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_debug_port(port, process)
        with sync_playwright() as pw:
            connection = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                context = connection.contexts[0]
                context.on("request", on_request)
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    # "commit" returns once the navigation starts: the SSO redirect chain
                    # can take a while and the user may be typing during it.
                    page.goto(url, wait_until="commit", timeout=60_000)
                except PlaywrightError:
                    print("The portal is slow to load; waiting for you to sign in...")

                started = last_nudge = time.monotonic()
                announced = False
                while not found:
                    now = time.monotonic()
                    if now - started > timeout_s:
                        raise LoginError(
                            "Never saw an authenticated request. Did you finish logging in "
                            "and is the planning page open?"
                        )
                    if not announced and now - started > 12:
                        print("Please sign in to the portal in the browser window...")
                        announced = True
                    # Only nudge while the app itself is loaded, never on the SSO page.
                    if api_host in page.url and now - max(started, last_nudge) > NUDGE_AFTER_S:
                        try:
                            page.evaluate(_nudge_script(hash_route))
                        except PlaywrightError:
                            pass  # mid-navigation; try again later
                        last_nudge = now
                    try:
                        page.wait_for_timeout(500)
                    except PlaywrightError:
                        time.sleep(0.5)  # page navigated or closed: keep polling
            finally:
                try:
                    connection.close()  # detaches only; the process is stopped below
                except PlaywrightError:
                    pass
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    return found[-1]
