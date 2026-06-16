"""
Persistent browser context helper for Playwright-based AI web CLIs.

Instead of launching a new browser every call (3-5s), this keeps a persistent
user-data dir. First call creates the profile; subsequent calls reuse it.
A file lock prevents simultaneous access from crashing Chromium.

Usage in CLI scripts:
    from chrome_context import persistent_browser

    with persistent_browser(profile_dir="/path/to/profile") as (pw, ctx, pg):
        pg.goto("https://example.com")
        ...

Chrome daemon (even faster): launch chrome_daemon.py once, then use
CHROME_DAEMON_PORT env var to connect via CDP.
"""

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

CHROME_ARGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-features=TranslateUI",
    "--disable-accelerated-2d-canvas",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-field-trial-config",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
]

# Resource types to block during page load for speed
_BLOCKED_RESOURCES = {"image", "font", "media", "stylesheet", "websocket"}


def block_slow_resources(page):
    """Block images, fonts, media to speed up page loads by 2-4s."""
    def route_handler(route):
        if route.request.resource_type in _BLOCKED_RESOURCES:
            route.abort()
        else:
            route.continue_()
    page.route("**/*", route_handler)


def fast_goto(page, url: str, wait_selector: str = None, timeout: int = 30000):
    """
    Navigate with blocked resources + smart wait instead of fixed sleep.
    
    If wait_selector is provided, waits for that element to appear instead
    of using domcontentloaded + sleep. Saves 3-5s per page load.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=15000)
        except Exception:
            pass  # Continue even if selector not found


@contextmanager
def persistent_browser(profile_dir: Path, viewport: dict = None):
    """
    Launch a persistent browser context with file locking.
    
    First call: creates profile (~3-5s). Subsequent calls: instant (~0.5s).
    Only one process can use the profile at a time (fcntl lock).
    """
    from playwright.sync_api import sync_playwright

    if viewport is None:
        viewport = {"width": 1280, "height": 800}

    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / ".lock"
    lock_fh = None

    try:
        # Acquire exclusive lock (wait up to 30s)
        lock_fh = open(lock_path, "w")
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.2)
        else:
            raise RuntimeError(f"Profile {profile_dir} locked by another process")

        pw = sync_playwright().start()
        ctx = None
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=True,
                viewport=viewport,
                args=CHROME_ARGS,
            )
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            yield pw, ctx, pg
        finally:
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass
            try:
                pw.stop()
            except Exception:
                pass
    finally:
        if lock_fh:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                lock_fh.close()
            except Exception:
                pass


def connect_to_daemon(port: int = None):
    """
    Connect to a running chrome_daemon.py via CDP.
    Even faster than persistent context — no browser startup at all.
    
    Set CHROME_DAEMON_PORT env var or pass port explicitly.
    Returns (browser, context, page) tuple.
    """
    from playwright.sync_api import sync_playwright

    if port is None:
        port = int(os.environ.get("CHROME_DAEMON_PORT", "0"))
    if port == 0:
        # Try to auto-detect from PID file
        pid_file = Path.home() / ".chrome-daemon" / "daemon.port"
        if pid_file.exists():
            port = int(pid_file.read_text().strip())

    if port == 0:
        raise RuntimeError("No daemon port configured")

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    return pw, browser, ctx, pg
