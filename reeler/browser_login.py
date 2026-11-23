"""Open a real browser window, let the user log in to Instagram normally, and read the session cookie.

Uses Microsoft Edge (installed on every Windows PC) or Chrome through Playwright, so nothing else
has to be downloaded. The browser profile is kept in the work folder, so next time it is already
logged in.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

INSTAGRAM_LOGIN = "https://www.instagram.com/accounts/login/"


def grab_session_cookie(profile_dir: Path, *, url: str = INSTAGRAM_LOGIN, cookie: str = "sessionid",
                        domain_hint: str = "instagram.com", timeout_s: int = 900,
                        executable_path: str | None = None, headless: bool = False,
                        channels: tuple[str | None, ...] = ("msedge", "chrome", None)) -> str:
    """Return the cookie value once the user has logged in (polls until `timeout_s`)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("pip install playwright") from exc

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx, last_err = None, None
        for ch in ([None] if executable_path else channels):
            try:
                kw = {"headless": headless, "viewport": {"width": 480, "height": 860}}
                if executable_path:
                    kw["executable_path"] = executable_path
                elif ch:
                    kw["channel"] = ch
                ctx = pw.chromium.launch_persistent_context(str(profile_dir), **kw)
                break
            except Exception as exc:  # noqa: BLE001 - try the next browser
                last_err = exc
                log.debug("browser channel %s unavailable: %s", ch, exc)
        if ctx is None:
            raise RuntimeError(f"Could not open a browser window: {last_err}")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            def found() -> str | None:
                for c in ctx.cookies():
                    if c.get("name") == cookie and domain_hint in c.get("domain", "") and c.get("value"):
                        return c["value"]
                return None

            value = found()
            if value:
                return value
            page.goto(url)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                value = found()
                if value:
                    time.sleep(2)  # let Instagram finish setting cookies
                    return found() or value
                try:
                    page.wait_for_timeout(1500)
                except Exception:  # noqa: BLE001 - user closed the window
                    if not ctx.pages:
                        break
                    page = ctx.pages[0]
            raise RuntimeError("Login window closed or timed out before you logged in.")
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
