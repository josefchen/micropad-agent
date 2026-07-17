#!/usr/bin/env python3
"""micropad-agent — per-key renderer (Tier B, v1). REQUIRES the custom `agent`
firmware flashed (see docs/per-key-led-design.md). Standalone on purpose so it
never disturbs the stock-firmware daemon.py.

  1. launchctl unload the global daemon (it owns :48801 and the pad)
  2. ./run.sh daemon_perkey.py

Hooks POST {"state":..,"source":..} exactly as today. v1 shows the winning state
on all 12 keys — proving the per-key pipe end to end without any hook changes.
v2 (one session per key) needs the hooks to also POST a session id; the slot
scaffold is stubbed below.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import hid

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

# VIA custom command envelope (matches keymap.c)
ID_CUSTOM_SET_VALUE = 0x07
CH_AGENT, VAL_FRAME = 0x30, 0x01

NLED = 12
PORT = 48801
HEARTBEAT_S = 2.0        # resend so the firmware's STALE fallback never trips
DONE_DECAY_S = 45.0

# state codes — must match enum agent_state in keymap.c
IDLE, THINK_CLAUDE, THINK_CODEX, NEEDS, DONE, ERROR, OFF = 0, 1, 2, 3, 4, 5, 0xFF

# (source, state) -> code. source only matters while working.
def state_code(source, state):
    if state == "error":                       return ERROR
    if state == "waiting":                     return NEEDS
    if state == "success":                     return DONE
    if state in ("thinking", "coding"):
        return THINK_CODEX if source == "codex" else THINK_CLAUDE
    return IDLE

# Higher wins the pad. Matches daemon.py's vocabulary.
PRIORITY = {"idle": 0, "success": 1, "thinking": 2, "coding": 2, "waiting": 3, "error": 4}
SUCCESS_ANNOUNCE_S, SUCCESS_ANNOUNCE_PRIORITY = 6.0, 2.5

# v2 scaffold: LED indices reserved as per-session slots, highest priority first.
SLOTS = [10, 11, 9, 8, 3, 4]

_lock = threading.Lock()
_agents = {}   # source -> {"state", "ts"}


class Pad:
    """Sends 12-byte state frames over the VIA custom channel. Reconnects."""
    def __init__(self):
        self.dev = None
        self._last = None

    def _open(self):
        for d in hid.enumerate(VID, PID):
            if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
                self.dev = hid.Device(path=d["path"])
                self._last = None
                return True
        return False

    def show(self, frame):
        frame = bytes(frame)
        if frame == self._last:
            return
        if self.dev is None and not self._open():
            return
        try:
            payload = bytes([ID_CUSTOM_SET_VALUE, CH_AGENT, VAL_FRAME]) + frame
            self.dev.write(b"\x00" + payload + b"\x00" * (32 - len(payload)))
            self._last = frame
        except Exception:
            self.dev = None   # unplugged; retry next tick


def record(source, state):
    if state in PRIORITY:
        with _lock:
            _agents[source] = {"state": state, "ts": time.monotonic()}


def winner():
    now, best = time.monotonic(), None
    with _lock:
        for source, s in _agents.items():
            state, ts = s["state"], s["ts"]
            prio = PRIORITY[state]
            if state == "success":
                age = now - ts
                if age > DONE_DECAY_S:
                    state, prio = "idle", 0
                elif age < SUCCESS_ANNOUNCE_S:
                    prio = SUCCESS_ANNOUNCE_PRIORITY
            key = (prio, ts)
            if best is None or key > best[0]:
                best = (key, source, state)
    return (None, "idle") if best is None else (best[1], best[2])


def frame():
    """v1: winning state on every key. (v2 would paint SLOTS per session.)"""
    src, st = winner()
    return [state_code(src, st)] * NLED


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/state":
            self.send_response(404); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            record(body.get("source", "claude-code"), body.get("state", "idle"))
            self.send_response(204)
        except Exception:
            self.send_response(400)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


def main():
    pad = Pad()
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"micropad-agent per-key renderer on 127.0.0.1:{PORT}")
    last_beat = 0.0
    while True:
        f = frame()
        now = time.monotonic()
        if now - last_beat > HEARTBEAT_S:   # periodic resend keeps firmware fresh
            pad._last = None
            last_beat = now
        pad.show(f)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
