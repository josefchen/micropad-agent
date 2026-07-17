#!/usr/bin/env python3
"""micropad-agent — per-key renderer (Tier B, v2). REQUIRES the custom `agent`
firmware flashed (see docs/per-key-led-design.md). Standalone on purpose so it
never disturbs the stock-firmware daemon.py.

  1. launchctl unload the global daemon (it owns :48801 and the pad)
  2. ./run.sh daemon_perkey.py            (or ./bootstrap.sh --per-key)

v2 — one status LED per concurrent session. Hooks POST
{"state":..,"source":..,"session":..}; the daemon assigns each new session a
free LED slot, paints that slot with the session's state, and frees the slot
when the session ends (Claude SessionEnd -> "idle") or a "done" decays.

Graceful degrade: if a POST carries no "session" (older hooks, or a Codex notify
with no thread id), the source name is used as the key — so you still get one
slot for Claude and one for Codex, exactly like v1's two-agent case.
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
DONE_DECAY_S = 45.0      # a finished session fades out (and frees its slot) after this
STALE_SESSION_S = 300.0  # safety net: drop a session gone silent for this long

# state codes — must match enum agent_state in keymap.c
IDLE, THINK_CLAUDE, THINK_CODEX, NEEDS, DONE, ERROR, OFF = 0, 1, 2, 3, 4, 5, 0xFF

# LED indices reserved as per-session status slots, in assignment order.
# See the LED map in docs/per-key-led-design.md. First-seen session grabs SLOTS[0].
SLOTS = [10, 11, 9, 8, 3, 4]


# (source, state) -> code. source only matters while working.
def state_code(source, state):
    if state == "error":                       return ERROR
    if state == "waiting":                     return NEEDS
    if state == "success":                     return DONE
    if state in ("thinking", "coding"):
        return THINK_CODEX if source == "codex" else THINK_CLAUDE
    return IDLE


_lock = threading.Lock()
_sessions = {}   # key -> {"source", "state", "ts"}
_slots = {}      # key -> LED index (a SLOTS entry)


def _free_slot():
    used = set(_slots.values())
    for s in SLOTS:
        if s not in used:
            return s
    return None


def _assign(key):
    """Give `key` a slot: a free one, else evict the least-recently-active
    session so a live one is never starved by a stale slot holder."""
    slot = _free_slot()
    if slot is not None:
        return slot
    if _slots:
        victim = min(_slots, key=lambda k: _sessions.get(k, {}).get("ts", 0.0))
        slot = _slots.pop(victim)
        _sessions.pop(victim, None)
        return slot
    return None


def record(key, source, state):
    now = time.monotonic()
    with _lock:
        # "idle" is only ever posted by Claude's SessionEnd -> session is gone.
        if state == "idle":
            _sessions.pop(key, None)
            _slots.pop(key, None)
            return
        s = _sessions.get(key)
        if s is None:
            _slots[key] = _assign(key)
            _sessions[key] = {"source": source, "state": state, "ts": now}
        else:
            s["source"], s["state"], s["ts"] = source, state, now


def reap():
    now = time.monotonic()
    with _lock:
        for key in list(_sessions):
            s = _sessions[key]
            age = now - s["ts"]
            if (s["state"] == "success" and age > DONE_DECAY_S) or age > STALE_SESSION_S:
                _sessions.pop(key, None)
                _slots.pop(key, None)


def frame():
    """Baseline: the SLOTS keys glow dim-white as a status strip; non-slot keys
    are off. Each active session paints its own slot with its state colour."""
    f = [OFF] * NLED
    for s in SLOTS:
        f[s] = IDLE
    with _lock:
        for key, slot in _slots.items():
            sess = _sessions.get(key)
            if slot is None or sess is None:
                continue
            f[slot] = state_code(sess["source"], sess["state"])
    return f


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


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/state":
            self.send_response(404); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            source = body.get("source", "claude-code")
            state = body.get("state", "idle")
            key = body.get("session") or source   # no session id -> per-source slot
            record(key, source, state)
            self.send_response(204)
        except Exception:
            self.send_response(400)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        if self.path == "/state":       # debug view of slot assignment
            with _lock:
                data = {
                    "slots": {str(slot): key for key, slot in _slots.items()},
                    "sessions": {
                        k: {"source": s["source"], "state": s["state"]}
                        for k, s in _sessions.items()
                    },
                }
            payload = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload); return
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


def main():
    pad = Pad()
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"micropad-agent per-key renderer (v2, per-session slots) on 127.0.0.1:{PORT}")
    last_beat = 0.0
    while True:
        reap()
        f = frame()
        now = time.monotonic()
        if now - last_beat > HEARTBEAT_S:   # periodic resend keeps firmware fresh
            pad._last = None
            last_beat = now
        pad.show(f)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
