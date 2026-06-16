#!/usr/bin/env python3
"""
Fast page server for MiMo — keeps chat page loaded, accepts queries via HTTP.
Run once, then CLI scripts use HTTP instead of Playwright (no EPIPE).

Usage:
  python mimo_server.py              # Start (port 9872)
  python mimo_server.py --stop       # Stop
  python mimo_server.py --status     # Check
"""

import json
import os
import signal
import sys
import time
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread, Lock

MIMO_HOME = Path.home() / ".mimo-cli"
MIMO_AUTH = MIMO_HOME / "auth.json"
MIMO_URL = "https://aistudio.xiaomimimo.com"
PORT = 9872
PID_FILE = MIMO_HOME / "server.pid"

_pg = None
_ctx = None
_pw = None
_lock = Lock()
_conv_url = None  # Track conversation for multi-turn


def load_auth():
    if MIMO_AUTH.exists():
        return json.loads(MIMO_AUTH.read_text())
    return {}


def setup_cookies(ctx, auth):
    for n, v in auth.get("cookies", {}).items():
        d = ".xiaomimimo.com"
        if n in ("passToken", "pass_ua", "deviceId", "state", "tick", "sns-bind-step"):
            d = ".account.xiaomi.com"
        ctx.add_cookies([{"name": n, "value": v, "domain": d, "path": "/",
                          "httpOnly": False, "secure": True, "sameSite": "Lax"}])


def init_browser():
    global _pw, _ctx, _pg

    from playwright.sync_api import sync_playwright

    auth = load_auth()
    if not auth:
        raise RuntimeError("No MiMo auth. Run mimo.py login flow first.")

    profile_dir = str(MIMO_HOME / "browser-profile")
    _pw = sync_playwright().start()
    _ctx = _pw.chromium.launch_persistent_context(
        profile_dir, headless=True,
        viewport={"width": 1280, "height": 800},
        args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
    setup_cookies(_ctx, auth)
    _pg = _ctx.pages[0] if _ctx.pages else _ctx.new_page()

    # Block slow resources
    try:
        def _abort(route):
            if route.request.resource_type in {"image", "font", "media"}:
                route.abort()
            else:
                route.continue_()
        _pg.route("**/*", _abort)
    except Exception: pass

    _pg.goto(f"{MIMO_URL}/#/c", timeout=30000)
    try: _pg.wait_for_selector('textarea', timeout=10000)
    except: time.sleep(4)
    
    # Check auth
    ta = _pg.locator("textarea").first
    if ta.count() > 0:
        ph = ta.get_attribute("placeholder") or ""
        if "sign in" in ph.lower():
            raise RuntimeError("MiMo auth expired — re-login via Firefox")
    print(f"MiMo server ready — auth OK", flush=True)


def send_query(prompt: str, new_conversation: bool = False) -> str:
    global _pg, _conv_url

    if new_conversation:
        _conv_url = None
        _pg.goto(f"{MIMO_URL}/#/c", timeout=30000)
        time.sleep(4)
    
    ta = _pg.locator("textarea").first
    if ta.count() == 0:
        raise RuntimeError("Textarea not found")
    
    ta.fill(prompt); time.sleep(0.1)
    ta.press("Enter")

    # Save conversation URL from URL bar after first message
    time.sleep(2)
    current_url = _pg.url
    m = re.search(r'/#/chat/([a-f0-9-]+)', current_url)
    if m and not _conv_url:
        _conv_url = f"{MIMO_URL}/#/chat/{m.group(1)}"

    # Wait for response
    deadline = time.time() + 120
    pre_len = len(_pg.locator("body").inner_text())
    while time.time() < deadline:
        body = _pg.locator("body").inner_text()
        if "Thought for" in body and len(body) > pre_len + 30:
            time.sleep(2)
            body = _pg.locator("body").inner_text()
            parts = body.split("Thought for")
            if len(parts) > 1:
                last = parts[-1]
                lines = last.split("\n")
                result = []
                found_sec = False
                stop = ["We use", "Cookie", "Citation", "Flagship", "Kingsoft",
                        "Flexible", "Daily", "TokenPlan", "Try Now", "API", "Learn", "Developer"]
                for line in lines:
                    s = line.strip()
                    if not found_sec and ("second" in s.lower()):
                        found_sec = True
                        continue
                    if found_sec and s:
                        if any(s.startswith(w) for w in stop):
                            break
                        if "Accept All" in s or "Decline All" in s:
                            break
                        if not s.startswith("MiMo"):
                            result.append(s)
                return "\n".join(result).strip()
        time.sleep(0.5)
    return ""


def cleanup():
    global _ctx, _pw
    if _ctx:
        try: _ctx.close()
        except: pass
    if _pw:
        try: _pw.stop()
        except: pass


class Handler(BaseHTTPRequestHandler):
    allow_reuse_address = True
    def do_POST(self):
        if self.path not in ("/query", "/query/new"):
            self.send_error(404); return
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)
        try:
            data = json.loads(body)
            prompt = data.get("prompt", "")
        except json.JSONDecodeError:
            self.send_error(400); return

        if not prompt:
            self.send_error(400); return

        new_conv = self.path == "/query/new"
        with _lock:
            try:
                text = send_query(prompt, new_conversation=new_conv)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "err": str(e)}).encode())
                return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "text": text}).encode())

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        elif self.path == "/stop":
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
            Thread(target=self.server.shutdown).start()
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def run_server():
    global _pg, _ctx, _pw
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    def on_exit(*args):
        cleanup()
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_exit)
    signal.signal(signal.SIGINT, on_exit)

    try:
        init_browser()
    except Exception as e:
        print(f"Init failed: {e}", file=sys.stderr)
        PID_FILE.unlink(missing_ok=True)
        sys.exit(1)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"MiMo server ready on :{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        cleanup()
        PID_FILE.unlink(missing_ok=True)


def stop_server():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            PID_FILE.unlink(missing_ok=True)
            print(f"Stopped PID={pid}")
            return
        except: pass
        PID_FILE.unlink(missing_ok=True)
    print("Not running")


def query(prompt: str, new_conv: bool = False) -> dict:
    import urllib.request
    data = json.dumps({"prompt": prompt}).encode()
    path = "/query/new" if new_conv else "/query"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "err": str(e)}


def ensure_server():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
            return True
        except: pass
        PID_FILE.unlink(missing_ok=True)
    return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--stop":
        stop_server(); return
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        print("Running" if ensure_server() else "Not running"); return
    if len(sys.argv) > 1 and sys.argv[1] == "--query":
        prompt = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read().strip()
        if ensure_server():
            print(json.dumps(query(prompt), ensure_ascii=False))
        else:
            print(json.dumps({"ok": False, "err": "server not running"}))
            sys.exit(1)
        return
    run_server()


if __name__ == "__main__":
    main()
