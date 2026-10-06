"""Optional same-LAN button remote. No microphone or audio is sent by the phone."""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODES = frozenset(("STANDBY", "NOTICE", "DISCUSSION", "REST", "QUESTION", "CORRECT", "WRONG"))


class MobileRemote:
    def __init__(self, page: Path, on_mode):
        self.page = Path(page)
        self.on_mode = on_mode
        self.token = secrets.token_urlsafe(18)
        self._lock = threading.Lock()
        self._last_seen = 0.0
        self._state = {"mode": "STANDBY", "base_mode": "STANDBY", "overlay": None}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, data, content_type="application/json; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                return secrets.compare_digest(self.headers.get("X-Remote-Token", ""), owner.token)

            def do_GET(self):
                if self.path == "/":
                    self.reply(200, owner.page.read_bytes(), "text/html; charset=utf-8")
                elif self.path == "/state" and self.authorized():
                    with owner._lock:
                        owner._last_seen = time.monotonic()
                        state = dict(owner._state)
                    self.reply(200, json.dumps(state).encode("utf-8"))
                else:
                    self.reply(404, b"{}")

            def do_POST(self):
                if self.path != "/mode" or not self.authorized():
                    self.reply(404, b"{}")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 128:
                        raise ValueError("invalid size")
                    mode = json.loads(self.rfile.read(length))["mode"]
                    if mode not in MODES:
                        raise ValueError("invalid mode")
                except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                    self.reply(400, b"{}")
                    return
                with owner._lock:
                    owner._last_seen = time.monotonic()
                owner.on_mode(mode)
                self.reply(200, b'{"ok":true}')

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_port

    @property
    def connected(self):
        with self._lock:
            return time.monotonic() - self._last_seen < 6.0

    def update_state(self, current):
        with self._lock:
            self._state = {key: current[key] for key in ("mode", "base_mode", "overlay")}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
