#!/usr/bin/env python3
"""
Persistent Chromium daemon for AI web CLIs (Qwen, MiniMax, MiMo, Kimi).

Keeps a headless Chromium alive on a debug port. CLI scripts connect via CDP
instead of launching their own browser — saving 3-5s per query.

Usage:
  python chrome_daemon.py              # Start daemon
  python chrome_daemon.py --stop       # Stop daemon
  python chrome_daemon.py --status     # Check if running

CLI scripts use persistent context via CHROME_DAEMON_PORT env var.
"""

import argparse
import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

DAEMON_PORT = 9223
PID_FILE = Path.home() / ".chrome-daemon" / "daemon.pid"
PORT_FILE = Path.home() / ".chrome-daemon" / "daemon.port"
LOG_FILE = Path.home() / ".chrome-daemon" / "daemon.log"


def is_port_open(port: int) -> bool:
    """Check if something is listening on the port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def find_free_port(start: int = 9223, max_attempts: int = 10) -> int:
    """Find a free port starting from `start`."""
    for port in range(start, start + max_attempts):
        if not is_port_open(port):
            return port
    raise RuntimeError(f"No free port in range {start}-{start + max_attempts}")


def launch_chromium(port: int) -> subprocess.Popen:
    """Launch headless Chromium with remote debugging."""
    # Find chromium binary
    chromium_paths = [
        "/home/peter/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
    ]
    chromium = None
    for p in chromium_paths:
        if Path(p).exists():
            chromium = p
            break
    if not chromium:
        raise RuntimeError("Chromium not found. Install: playwright install chromium")

    user_data = str(Path.home() / ".chrome-daemon" / "user-data")
    Path(user_data).mkdir(parents=True, exist_ok=True)

    cmd = [
        chromium,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-sync",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--disable-extensions",
        "about:blank",
    ]

    log_fh = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        preexec_fn=os.setsid,
    )
    return proc


def daemonize():
    """Fork into background (double-fork)."""
    # First fork
    if os.fork() > 0:
        return False  # Parent exits

    # Second fork
    if os.fork() > 0:
        sys.exit(0)  # First child exits

    # Daemon process
    os.setsid()
    os.chdir("/")
    os.umask(0)

    # Redirect stdio
    sys.stdin = open("/dev/null", "r")
    sys.stdout = open(LOG_FILE, "a")
    sys.stderr = open(LOG_FILE, "a")

    return True


def run_daemon(port: int):
    """Main daemon loop."""
    Path(PID_FILE).parent.mkdir(parents=True, exist_ok=True)

    proc = launch_chromium(port)
    pid = proc.pid

    # Write PID and port
    PID_FILE.write_text(str(pid))
    PORT_FILE.write_text(str(port))

    print(f"Chrome daemon started (PID={pid}, port={port})")

    def cleanup():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        PID_FILE.unlink(missing_ok=True)
        PORT_FILE.unlink(missing_ok=True)

    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))

    # Wait for Chromium to be ready
    deadline = time.time() + 15
    while time.time() < deadline:
        if is_port_open(port):
            print(f"Chrome ready on port {port}")
            break
        time.sleep(0.5)
    else:
        print("Chrome failed to start")
        sys.exit(1)

    # Keep alive
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass


def stop_daemon():
    """Stop the running daemon."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            PID_FILE.unlink(missing_ok=True)
            PORT_FILE.unlink(missing_ok=True)
            print(f"Stopped daemon (PID={pid})")
            return
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    print("No daemon running")


def status():
    """Check daemon status."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            port = PORT_FILE.read_text().strip() if PORT_FILE.exists() else "?"
            print(f"Daemon running (PID={pid}, port={port})")
            return
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    print("Daemon not running")


def get_daemon_port() -> int | None:
    """Get the daemon port if running. Returns None if not."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if alive
        if PORT_FILE.exists():
            return int(PORT_FILE.read_text().strip())
    except (ProcessLookupError, ValueError):
        pass
    return None


def ensure_daemon(port: int = 0) -> int:
    """Ensure daemon is running. Returns port number."""
    existing = get_daemon_port()
    if existing:
        return existing

    if port == 0:
        port = find_free_port()

    # Launch daemon as background process
    log_path = str(LOG_FILE)
    subprocess.Popen(
        [sys.executable, __file__, "--port", str(port)],
        stdout=open(log_path, "a"),
        stderr=open(log_path, "a"),
        start_new_session=True,
    )

    # Wait for it to be ready
    deadline = time.time() + 15
    while time.time() < deadline:
        if is_port_open(port):
            return port
        time.sleep(0.5)
    raise RuntimeError("Daemon failed to start")


def main():
    parser = argparse.ArgumentParser(description="Chrome daemon for AI web CLIs")
    parser.add_argument("--port", type=int, default=0, help="Debug port")
    parser.add_argument("--stop", action="store_true", help="Stop daemon")
    parser.add_argument("--status", action="store_true", help="Check status")
    parser.add_argument("--foreground", action="store_true", help="Run in foreground")
    args = parser.parse_args()

    if args.stop:
        stop_daemon()
        return
    if args.status:
        status()
        return

    port = args.port or find_free_port()

    if args.foreground:
        run_daemon(port)
    else:
        # Background mode
        if daemonize():
            run_daemon(port)


if __name__ == "__main__":
    main()
