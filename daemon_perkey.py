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
import os
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import hid

HERE = os.path.dirname(os.path.abspath(__file__))

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

# VIA custom command envelope (matches keymap.c)
ID_CUSTOM_SET_VALUE = 0x07
CH_AGENT, VAL_FRAME = 0x30, 0x01

# device -> host focus event (keymap.c: hold bottom-left touch, tap a status key)
MSG_FOCUS = 0xF1
DOUBLE_TAP_S = 0.4       # two taps within this window -> foreground instead of focus

NLED = 12
PORT = 48801
HEARTBEAT_S = 2.0        # resend so the firmware's STALE fallback never trips
DONE_DECAY_S = 45.0      # a finished session fades out (and frees its slot) after this
STALE_SESSION_S = 300.0  # safety net: drop a session gone silent for this long

# state codes — must match enum agent_state in keymap.c
IDLE, THINK_CLAUDE, THINK_CODEX, NEEDS, DONE, ERROR, OFF = 0, 1, 2, 3, 4, 5, 0xFF

# LED indices reserved as per-session status slots. Position encodes the agent:
# the LEFT half (cols 0-1) is Claude, the RIGHT half (cols 2-3) is Codex, across
# the translucent top 3 rows. See docs/per-key-led-design.md.
#   row0:        [10][11]         left  (Claude): 10,9,8,2,3
#   row1:    [9][ 8][ 7][6]       right (Codex) : 11,7,6,4,5
#   row2:    [2][ 3][ 4][5]       row3 [1]/[0] = opaque green/red action keys
CLAUDE_SLOTS = [10, 9, 8, 2, 3]   # left half, reading order top->bottom
CODEX_SLOTS = [11, 7, 6, 4, 5]    # right half
SLOTS = CLAUDE_SLOTS + CODEX_SLOTS   # full status strip (render baseline)


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


def _pool(source):
    return CODEX_SLOTS if source == "codex" else CLAUDE_SLOTS


def _free_slot(source):
    used = set(_slots.values())
    for s in _pool(source):          # prefer this agent's own cluster
        if s not in used:
            return s
    for s in SLOTS:                  # own cluster full -> spill into any free slot
        if s not in used:
            return s
    return None


def _assign(key, source):
    """Give `key` a slot from its agent's cluster, else any free slot, else evict
    the least-recently-active session so a live one is never starved."""
    slot = _free_slot(source)
    if slot is not None:
        return slot
    if _slots:
        victim = min(_slots, key=lambda k: _sessions.get(k, {}).get("ts", 0.0))
        slot = _slots.pop(victim)
        _sessions.pop(victim, None)
        return slot
    return None


def record(key, source, state, pane=""):
    now = time.monotonic()
    with _lock:
        # "idle" is only ever posted by Claude's SessionEnd -> session is gone.
        if state == "idle":
            _sessions.pop(key, None)
            _slots.pop(key, None)
            return
        s = _sessions.get(key)
        if s is None:
            _slots[key] = _assign(key, source)
            _sessions[key] = {"source": source, "state": state, "ts": now, "pane": pane}
        else:
            s["source"], s["state"], s["ts"] = source, state, now
            if pane:
                s["pane"] = pane   # refresh the focus hint if the hook sent one


def reap():
    now = time.monotonic()
    with _lock:
        for key in list(_sessions):
            s = _sessions[key]
            age = now - s["ts"]
            if (s["state"] == "success" and age > DONE_DECAY_S) or age > STALE_SESSION_S:
                _sessions.pop(key, None)
                _slots.pop(key, None)


# --- focus (v2): a status key was tapped while the bottom-left touch was held.
# The firmware reports the LED index; here we turn it into a background focus,
# a foreground (double-tap), or a spawn (empty slot), via overridable hooks.
_focus_last = {}   # led index -> monotonic time of last tap


def _run_hook(name, args):
    path = os.path.join(HERE, "hooks", name)
    if not os.access(path, os.X_OK):
        return
    try:
        subprocess.Popen([path, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# cmux fast path: talk to the control socket directly instead of shelling out to
# the cmux CLI (which cold-starts ~150 ms per call). The token + socket path are
# refreshed into this file by claude_state.sh, which runs inside cmux.
CMUX_AUTH = os.path.expanduser("~/.cache/micropad/cmux-auth.env")


def _cmux_rpc(method, params, timeout=1.5):
    """One request over the cmux unix socket. Returns the parsed reply or None."""
    cap = sock_path = None
    try:
        with open(CMUX_AUTH) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == "CMUX_SOCKET_CAPABILITY":
                    cap = v
                elif k == "CMUX_SOCKET_PATH":
                    sock_path = v
    except OSError:
        return None
    if not cap or not sock_path:
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        req = "_cmux_capability_v1 " + cap + " " + json.dumps(
            {"id": str(uuid.uuid4()).upper(), "params": params, "method": method}) + "\n"
        s.sendall(req.encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                return None
            buf += chunk
            try:
                return json.loads(buf)
            except json.JSONDecodeError:
                continue
    except Exception:
        return None
    finally:
        s.close()


def handle_focus(led):
    now = time.monotonic()
    double = (now - _focus_last.get(led, 0.0)) < DOUBLE_TAP_S
    _focus_last[led] = now
    with _lock:
        key = next((k for k, s in _slots.items() if s == led), None)
        sess = _sessions.get(key) if key else None
        pane = sess.get("pane", "") if sess else ""
    if sess is None:
        _run_hook("spawn_session.sh", [str(led)])            # empty slot -> new agent
    elif pane.startswith("cmux:"):
        # direct socket = ~5 ms vs ~150 ms for the CLI
        ok = _cmux_rpc("surface.focus", {"surface_id": pane[5:]})
        if double:
            subprocess.Popen(["open", "-b", "com.cmuxterm.app"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ok is None:   # stale token / socket gone -> fall back to the CLI hook
            _run_hook("focus_session.sh", ["foreground" if double else "focus", pane, key])
    else:
        _run_hook("focus_session.sh", ["foreground" if double else "focus", pane, key])


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

    def read(self, timeout_ms=120):
        """Poll for a device->host report (focus events). Also drains the VIA
        echo the firmware sends after each frame; callers filter by MSG_FOCUS."""
        if self.dev is None and not self._open():
            return None
        try:
            d = self.dev.read(32, timeout_ms)
            return bytes(d) if d else None
        except Exception:
            self.dev = None
            return None


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
            record(key, source, state, body.get("pane") or "")
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
                        k: {"source": s["source"], "state": s["state"], "pane": s.get("pane", "")}
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
    print(f"micropad-agent per-key renderer (v2 + focus) on 127.0.0.1:{PORT}")
    last_beat = 0.0
    while True:
        reap()
        f = frame()
        now = time.monotonic()
        if now - last_beat > HEARTBEAT_S:   # periodic resend keeps firmware fresh
            pad._last = None
            last_beat = now
        pad.show(f)
        ev = pad.read(120)                  # focus events + the read paces the loop
        if ev and len(ev) >= 2 and ev[0] == MSG_FOCUS:
            handle_focus(ev[1])


if __name__ == "__main__":
    main()
