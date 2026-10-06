"""Optional same-LAN button remote. No microphone or audio is sent by the phone."""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODES = frozenset(("STANDBY", "NOTICE", "DISCUSSION", "REST", "QUESTION", "CORRECT", "WRONG"))
SCENARIOS = frozenset(("STANDBY", "NOTICE", "DISCUSSION", "REST"))


class MobileRemote:
    def __init__(self, page: Path, on_mode, on_switch=None):
        self.page = Path(page)
        self.on_mode = on_mode
        self.on_switch = on_switch
        self.token = secrets.token_urlsafe(18)
        self._lock = threading.Lock()
        self._last_seen = 0.0
        self._armed = False
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
                        state = dict(owner._state, active=owner._armed)
                    self.reply(200, json.dumps(state).encode("utf-8"))
                else:
                    self.reply(404, b"{}")

            def do_POST(self):
                if self.path not in ("/mode", "/switch") or not self.authorized():
                    self.reply(404, b"{}")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 128:
                        raise ValueError("invalid size")
                    payload = json.loads(self.rfile.read(length))
                    mode = payload["mode"]
                    if mode not in (MODES if self.path == "/mode" else SCENARIOS):
                        raise ValueError("invalid mode")
                    if self.path == "/switch":
                        kind, enabled = payload["kind"], payload["enabled"]
                        if kind not in ("audio", "voice") or not isinstance(enabled, bool):
                            raise ValueError("invalid switch")
                except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                    self.reply(400, b"{}")
                    return
                with owner._lock:
                    owner._last_seen = time.monotonic()
                    armed = owner._armed
                    voice_allowed = owner._state.get("allow_voice", False)
                if not armed or (self.path == "/switch" and kind == "voice" and not voice_allowed):
                    self.reply(409, b'{"ok":false}')
                    return
                if self.path == "/mode":
                    owner.on_mode(mode)
                elif owner.on_switch:
                    owner.on_switch(kind, mode, enabled)
                else:
                    self.reply(501, b'{"ok":false}')
                    return
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

    @property
    def armed(self):
        with self._lock:
            return self._armed

    @property
    def active(self):
        with self._lock:
            return self._armed and time.monotonic() - self._last_seen < 6.0

    def set_armed(self, enabled):
        with self._lock:
            self._armed = enabled

    def update_state(self, current, settings, voice_modes=None, allow_voice=False):
        with self._lock:
            self._state = {**{key: current[key] for key in ("mode", "base_mode", "overlay")},
                           "question_seconds": settings.question_seconds,
                           "feedback_seconds": settings.feedback_seconds,
                           "audio_enabled_modes": dict(settings.audio_enabled_modes),
                           "voice_mode_enabled": dict(voice_modes or {}),
                           "allow_voice": bool(allow_voice)}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
