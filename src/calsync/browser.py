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
import time
from datetime import datetime, timezone
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
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
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


def acquire_token(
    url: str,
    *,
    browser: str = "chrome",
    profile_dir: Path = PROFILE_DIR,
    timeout_s: int = LOGIN_TIMEOUT_S,
) -> str:
    """Open the portal, wait for a sign-in, return the Authorization header value."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    api_host = urlsplit(url).netloc
    hash_route = "#" + urlsplit(url).fragment if urlsplit(url).fragment else "#/"
    profile_dir.mkdir(parents=True, exist_ok=True)
    found: list[str] = []

    def on_request(request) -> None:
        if api_host in request.url and "/api/" in request.url:
            value = request.headers.get("authorization", "").strip()
            if value:
                found.append(value)

    with sync_playwright() as pw:
        launch_args = dict(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if browser != "chromium":
            launch_args["channel"] = browser
        try:
            context = pw.chromium.launch_persistent_context(**launch_args)
        except PlaywrightError as exc:
            raise LoginError(
                f"Could not start '{browser}'. Install it, or set [portal] browser in the "
                f"config to another one (chrome, msedge, chromium).\n{exc}"
            ) from exc

        try:
            context.on("request", on_request)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")

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
                    time.sleep(0.5)  # page closed or navigated: keep polling
        finally:
            context.close()

    return found[-1]
