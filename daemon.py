#!/usr/bin/env python3
"""micropad-agent — drives the Work Louder Micro Pad's LEDs from agent state.

Mirrors the agentkat-bridge pattern: hooks POST {"state":..,"source":..} to a
local port; this daemon renders the winning state onto the pad.

  colour = agent state    (white idle · green done · yellow needs-you · red error)
  working states keep the agent's own hue (amber Claude / blue Codex) so you can
  still see who is running; motion reinforces (breathe / blink / strobe).

Everything is rendered host-side as solid-colour + brightness modulation, so we
never depend on firmware effect ids. Listens on 127.0.0.1:48801.
"""
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import hid

# ---------------------------------------------------------------- device

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

CUSTOM_SET_VALUE = 0x07
CH_RGB_MATRIX, CH_RGBLIGHT = 0x03, 0x02
V_BRIGHTNESS, V_EFFECT, V_COLOR = 0x01, 0x02, 0x04

MAX_BRIGHT = 150          # keyboard.json: rgb_matrix.max_brightness
EFFECT_SOLID = 1

# QMK hue is 0-255 (red 0, yellow 45, green 85, blue 170). Colour encodes STATE;
# only the working states keep the agent's identity hue, Codex-Micro style, so the
# pad reads as "what's happening" first and "who" only while it's actually running.
# Claude's orange is pushed up to amber (28) — at hue 12 it washed into red on the pad.
HUE_CLAUDE, HUE_CODEX, HUE_CURSOR = 28, 170, 213   # amber / blue / violet
HUE_DONE, HUE_INPUT, HUE_ERROR = 85, 45, 0          # green done / yellow needs-you / red error
FAMILY_HUE = (("claude", HUE_CLAUDE), ("cursor", HUE_CURSOR), ("codex", HUE_CODEX))

PORT = 48801
FRAME_HZ = 20
DONE_DECAY_S = 45.0       # "success" fades to idle after this long


class Pad:
    """Thin VIA raw-HID wrapper. Reconnects if the pad is unplugged."""

    def __init__(self):
        self.dev = None
        self._last = None   # (hue, sat, bright) — skip redundant writes
        self._miss_logged = False

    def _open(self):
        for d in hid.enumerate(VID, PID):
            if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
                self.dev = hid.Device(path=d["path"])
                self._last = None
                for ch in (CH_RGB_MATRIX, CH_RGBLIGHT):
                    self._send([CUSTOM_SET_VALUE, ch, V_EFFECT, EFFECT_SOLID])
                print("micropad connected", flush=True)
                self._miss_logged = False
                return True
        if not self._miss_logged:
            print("micropad not open (unplugged, or usevia.app still has WebHID)", flush=True)
            self._miss_logged = True
        return False

    def _send(self, payload):
        self.dev.write(b"\x00" + bytes(payload) + b"\x00" * (32 - len(payload)))

    def show(self, hue, sat, bright):
        frame = (hue, sat, bright)
        if frame == self._last:
            return
        if self.dev is None and not self._open():
            return
        try:
            for ch in (CH_RGB_MATRIX, CH_RGBLIGHT):
                self._send([CUSTOM_SET_VALUE, ch, V_COLOR, hue, sat])
                self._send([CUSTOM_SET_VALUE, ch, V_BRIGHTNESS, bright])
            self._last = frame
        except Exception as exc:
            print(f"micropad write failed: {exc}", flush=True)
            self.dev = None   # unplugged; retry on the next frame


# ---------------------------------------------------------------- state

# Higher number wins the pad. Vocabulary matches agentkat-bridge.
PRIORITY = {"idle": 0, "success": 1, "thinking": 2, "coding": 2, "waiting": 3, "error": 4}

# "I just finished" is news, so a fresh success outranks the other agent's ongoing
# work for a few seconds — otherwise Codex finishing is invisible whenever Claude
# happens to be mid-task, which is most of the time. It stays below `waiting`: an
# agent that needs you always matters more than one that's done.
SUCCESS_ANNOUNCE_S = 6.0
SUCCESS_ANNOUNCE_PRIORITY = 2.5
AGENT_HUE = {"claude-code": HUE_CLAUDE, "codex": HUE_CODEX, "cursor": HUE_CODEX}


def hue_for(source):
    """Identity hue while an agent is working."""
    source = source or ""
    if source.startswith("cursor"):
        return HUE_CURSOR
    if source.startswith("codex"):
        return HUE_CODEX
    if source.startswith("claude"):
        return HUE_CLAUDE
    return AGENT_HUE.get(source, HUE_CLAUDE)


def working_hues():
    """One hue per AI that is working right now, in a stable order."""
    present = set()
    with _lock:
        for source, s in _agents.items():
            if s["state"] in ("thinking", "coding"):
                present.add(source.split(":", 1)[0])
    hues = [hue for name, hue in FAMILY_HUE if any(p.startswith(name) for p in present)]
    return hues

_lock = threading.Lock()
_agents = {}   # source -> {"state": str, "ts": float}


def record(source, state):
    if state not in PRIORITY:
        return
    with _lock:
        _agents[source] = {"state": state, "ts": time.monotonic()}


def winner():
    """Most urgent state across all agents; ties break toward the most recent."""
    now = time.monotonic()
    best = None
    with _lock:
        for source, s in _agents.items():
            state, ts = s["state"], s["ts"]
            prio = PRIORITY[state]
            if state == "success":
                age = now - ts
                if age > DONE_DECAY_S:
                    state, prio = "idle", PRIORITY["idle"]
                elif age < SUCCESS_ANNOUNCE_S:
                    prio = SUCCESS_ANNOUNCE_PRIORITY
            key = (prio, ts)
            if best is None or key > best[0]:
                best = (key, source, state)
    if best is None:
        return None, "idle"
    return best[1], best[2]


def frame_for(source, state, t):
    """Map (agent, state) -> (hue, sat, brightness) for time t.

    Colour encodes STATE; only the working states keep the agent's identity hue.
        idle       dim white
        thinking   agent colour (amber Claude / blue Codex), breathing
        coding     same, breathing a little faster
        waiting    yellow, blink        — "I need you"
        success    green, solid         — "done"
        error      red, strobed white
    """
    if state == "error":
        # Red strobed against white. Red and amber wash into each other through the
        # pad's diffusers, so the white half — not the hue — is what makes this read
        # as an error rather than as an agent working.
        if int(t * 6) % 2:
            return HUE_ERROR, 255, MAX_BRIGHT
        return 0, 0, MAX_BRIGHT   # sat 0 = white
    if state == "waiting":
        # yellow, 2Hz blink — needs your input
        return HUE_INPUT, 255, MAX_BRIGHT if int(t * 4) % 2 else 0
    if state in ("thinking", "coding"):
        # One breath per working AI, then the next colour. A single AI stays put.
        hues = working_hues() or [hue_for(source or "")]
        period = 1.6
        slot = int(t / period) % len(hues)
        local = (t % period) / period
        phase = (math.sin(2 * math.pi * local) + 1) / 2
        return hues[slot], 255, int(MAX_BRIGHT * (0.12 + 0.88 * phase))
    if state == "success":
        return HUE_DONE, 255, MAX_BRIGHT      # green, solid
    return 0, 0, 14   # idle — dim white standby


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/state":
            self.send_response(404); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            source = body.get("source") or "claude-code"
            session = body.get("session") or ""
            key = f"{source}:{session}" if session else source
            record(key, body.get("state", "idle"))
            self.send_response(204)
        except Exception:
            self.send_response(400)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        if self.path == "/state":
            with _lock:
                snap = {k: dict(v) for k, v in _agents.items()}
            src, st = winner()
            body = json.dumps({"agents": snap, "winner": {"source": src, "state": st}},
                              default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass   # keep the daemon quiet


def main():
    pad = Pad()
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"micropad-agent listening on 127.0.0.1:{PORT}")

    t0 = time.monotonic()
    while True:
        src, st = winner()
        pad.show(*frame_for(src, st, time.monotonic() - t0))
        time.sleep(1 / FRAME_HZ)


if __name__ == "__main__":
    main()
