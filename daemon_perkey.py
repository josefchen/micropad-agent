#!/usr/bin/env python3
"""micropad-agent — per-key renderer. REQUIRES the custom `agent` firmware
(firmware/work_louder_micro/agent).

One keycap per live agent session, coloured by app (Claude amber, Codex blue,
Cursor violet). Sessions come from two places, merged by "newest record wins":

  * sources.scan()  the session files the Claude, Codex and Cursor apps write,
                    so the pad mirrors their GUIs (including Claude SSH sessions)
  * POST /state     hooks, for instant updates and permission prompts

Key behaviour:
  working  breathes        waiting  blinks + the 3 white LEDs blink
  done     solid, "unread" until you press it (or DONE_SHOW_S passes);
           the underglow goes green and the white LEDs flash 3 times
  press a lit key  -> opens that session (done keys are then cleared)
  unlit keys keep their VIA shortcuts

http://127.0.0.1:48801/ shows a live mirror of the pad.
"""
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hid

import sources

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.expanduser("~/.cache/micropad/state.json")

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

ID_CUSTOM_SET_VALUE = 0x07
CH_AGENT, VAL_FRAME, VAL_BOOTLOADER = 0x30, 0x01, 0x02
MSG_FOCUS = 0xF1        # a lit key went down
MSG_RELEASE = 0xF3      # ...and came back up
HOLD_S = 1.0            # hold a key this long to open its session; letting go sooner cancels
MSG_SCROLL = 0xF2
SEL_BIT = 0x40           # marks the wheel-selected key in a frame (keymap.c SEL_BIT)
SWITCH_DWELL_S = 0.9     # stop turning the wheel this long and the selection opens
SWITCH_RECENT = 6        # recent sessions (not on the pad) the wheel also walks through
DOUBLE_TAP_S = 0.4

NLED = 12
PORT = 48801
HEARTBEAT_S = 2.0
SCAN_S = 2.0

# How long each state keeps its key after the last record that set it.
SHOW_S = {"working": 20 * 60, "waiting": 60 * 60, "done": 30 * 60, "error": 30 * 60}
PRIORITY = {"waiting": 0, "error": 1, "working": 2, "done": 3}

# state codes — must match enum agent_state in keymap.c
IDLE, THINK_CLAUDE, THINK_CODEX, NEEDS, DONE, ERROR = 0, 1, 2, 3, 4, 5
THINK_CURSOR, NEEDS_CLAUDE, NEEDS_CODEX, NEEDS_CURSOR = 6, 7, 8, 9
DONE_CLAUDE, DONE_CODEX, DONE_CURSOR = 10, 11, 12
OFF = 0xFF

CODES = {
    "claude": {"working": THINK_CLAUDE, "waiting": NEEDS_CLAUDE, "done": DONE_CLAUDE},
    "codex":  {"working": THINK_CODEX,  "waiting": NEEDS_CODEX,  "done": DONE_CODEX},
    "cursor": {"working": THINK_CURSOR, "waiting": NEEDS_CURSOR, "done": DONE_CURSOR},
}
LABEL = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}
HOOK_STATE = {"thinking": "working", "coding": "working", "waiting": "waiting",
              "success": "done", "error": "error"}

# Filled bottom-up, left to right, so the first sessions sit nearest the hands.
#   row0:        [10][11]     last
#   row1:    [9][ 8][ 7][6]
#   row2:    [2][ 3][ 4][5]
#   row3:        [ 1][ 0]     first
LED_ORDER = [1, 0, 2, 3, 4, 5, 9, 8, 7, 6, 10, 11]


def family_of(source):
    source = source or ""
    for fam in CODES:
        if source.startswith(fam):
            return fam
    return ""


def code_for(family, state):
    if state == "error":
        return ERROR
    return CODES.get(family, {}).get(state, {"waiting": NEEDS, "done": DONE}.get(state, IDLE))


@dataclass
class Session:
    sid: str
    family: str
    state: str
    ts: float              # wall time of the record that set `state`
    title: str = ""
    workspace: str = ""
    pane: str = ""
    app_id: str = ""
    tokens: int = 0
    cached: int = 0


_lock = threading.Lock()
_files = {}     # sid -> Session from the last file scan
_hooks = {}     # sid -> Session from hook POSTs
_acked = {}     # sid -> wall time the user opened it from the pad
_slots = {}     # sid -> LED index
_view = {}      # sid -> merged Session, rebuilt by refresh()
_last_state = {}
_notified = {}  # sid -> (state, record ts) last announced; one alert per finished turn
_seeded = False
_pad_ok = False
_changed = threading.Condition()   # wakes /events streams
_switch = {"sid": None, "until": 0.0, "mode": "wheel"}   # mode: wheel | hold


def _poke():
    with _changed:
        _changed.notify_all()


def _load():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    _acked.update(d.get("acked", {}))
    _slots.update({k: int(v) for k, v in d.get("slots", {}).items()})
    for s in d.get("hooks", []):
        try:
            _hooks[s["sid"]] = Session(**s)
        except TypeError:
            pass


def _save():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    cutoff = time.time() - 24 * 3600
    data = {
        "acked": {k: v for k, v in _acked.items() if v > cutoff},
        "slots": _slots,
        "hooks": [asdict(s) for s in _hooks.values() if s.ts > cutoff],
    }
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, STATE_FILE)


def _merge(sid):
    f, h = _files.get(sid), _hooks.get(sid)
    if f and h:
        best = h if h.ts > f.ts else f
        family = "cursor" if "cursor" in (f.family, h.family) else f.family
        return Session(sid, family, best.state, best.ts, f.title or h.title,
                       h.workspace or f.workspace, h.pane, f.app_id,
                       f.tokens, f.cached)
    s = f or h
    return Session(**asdict(s))


def _visible(s, now):
    if now - s.ts > SHOW_S.get(s.state, 0):
        return False
    if s.state in ("done", "error") and _acked.get(s.sid, 0) >= s.ts:
        return False
    return True


# --- demo scenes -----------------------------------------------------------
# Staged sessions for photos and screen recordings: they replace the real view
# until "off", never notify, never persist, and pressing their keys opens nothing.

DEMO_TITLES = {
    "claude": ["Refactor auth middleware", "Write the release notes", "Fix flaky CI test"],
    "codex": ["Migrate billing schema", "Port parser to Rust"],
    "cursor": ["Polish onboarding flow", "Add dark mode"],
}
DEMO_SCENES = {
    # (family, state, tokens) per key, filled bottom-up like real sessions
    "showcase": [("claude", "working", 412_000_000), ("codex", "working", 38_400_000),
                 ("cursor", "working", 0), ("claude", "waiting", 96_000_000),
                 ("codex", "done", 12_900_000), ("claude", "done", 1_240_000_000),
                 ("cursor", "working", 0)],
    "working": [("claude", "working", 412_000_000), ("codex", "working", 38_400_000),
                ("cursor", "working", 0), ("claude", "working", 96_000_000),
                ("codex", "working", 12_900_000), ("cursor", "working", 0)],
    "needs": [("claude", "working", 412_000_000), ("codex", "working", 38_400_000),
              ("claude", "waiting", 96_000_000), ("cursor", "working", 0)],
    "done": [("claude", "working", 412_000_000), ("codex", "done", 38_400_000),
             ("cursor", "working", 0), ("claude", "working", 96_000_000)],
}
_demo = None   # sid -> Session while a scene is up


def set_demo(scene):
    """Show a staged scene ("off" returns to real sessions). Returns False for unknown names."""
    global _demo
    if scene == "off":
        _demo = None
    elif scene in DEMO_SCENES:
        now, used = time.time(), {f: 0 for f in DEMO_TITLES}
        sessions = {}
        for i, (family, state, tokens) in enumerate(DEMO_SCENES[scene]):
            title = DEMO_TITLES[family][used[family] % len(DEMO_TITLES[family])]
            used[family] += 1
            sid = f"demo-{i}"
            sessions[sid] = Session(sid, family, state, now - 40 - i * 95, title, tokens=tokens,
                                    cached=int(tokens * 0.97))
        # The pad flashes a key when it turns done, so pass through "working" first.
        _demo = {sid: replace(x, state="working") for sid, x in sessions.items()}
        refresh()
        time.sleep(0.6)
        _demo = sessions
    else:
        return False
    refresh()
    return True


def _refresh_demo():
    with _lock:
        _slots.clear()
        for led, sid in zip(LED_ORDER, _demo):
            _slots[sid] = led
        _view.clear()
        _view.update(_demo)
    _poke()


def refresh():
    """Rebuild the merged view, keep every visible session on the key it already
    had, and hand free keys to newcomers by urgency."""
    global _seeded
    if _demo is not None:
        _refresh_demo()
        return
    now = time.time()
    with _lock:
        merged = {sid: _merge(sid) for sid in set(_files) | set(_hooks)}
        live = {sid: s for sid, s in merged.items() if _visible(s, now)}
        for sid in [k for k in _slots if k not in live]:
            del _slots[sid]
        rank = lambda s: (PRIORITY.get(s.state, 9), -s.ts)
        for s in sorted((s for s in live.values() if s.sid not in _slots), key=rank):
            used = set(_slots.values())
            free = [led for led in LED_ORDER if led not in used]
            if free:
                _slots[s.sid] = free[0]
                continue
            victim = max(_slots, key=lambda k: rank(live[k]))
            if rank(live[victim]) > rank(s):
                _slots[s.sid] = _slots.pop(victim)
        changes = []
        for sid, s in live.items():
            prev = _last_state.get(sid)
            if _seeded and prev != s.state and s.state in ("done", "waiting", "error") and (
                    s.state == "waiting" or prev in ("working", "waiting")) and (
                    _notified.get(sid) != (s.state, s.ts)):
                _notified[sid] = (s.state, s.ts)
                changes.append(s)
        _last_state.clear()
        _last_state.update({sid: s.state for sid, s in live.items()})
        _view.clear()
        _view.update(merged)
        _seeded = True
        try:
            _save()
        except OSError:
            pass
    _poke()
    for s in changes:
        notify(s)


def notify(s):
    if os.environ.get("MICROPAD_NOTIFY", "1") == "0":
        return
    verb = {"done": "finished", "waiting": "needs you", "error": "hit an error"}[s.state]
    title = f"{LABEL.get(s.family, 'Agent')} {verb}"
    body = s.title or os.path.basename(s.workspace) or s.sid[:8]
    sound = "Glass" if s.state == "done" else "Ping"
    script = f"display notification {json.dumps(body)} with title {json.dumps(title)} sound name {json.dumps(sound)}"
    print(f"notify: {title}: {body}", flush=True)
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


_scan_beat = time.monotonic()
WATCHDOG_S = 60.0


_warned = set()


def _alert_format_drift():
    """Tell the user once when a source stops yielding sessions, so a silent
    app-update breakage does not look like 'nothing is running'."""
    current = set(sources.health()["warnings"])
    for w in current - _warned:
        print(f"source warning: {w}", flush=True)
        if os.environ.get("MICROPAD_NOTIFY", "1") != "0":
            script = f"display notification {json.dumps(w)} with title \"Micro Pad can't read a session source\""
            subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _warned.clear()
    _warned.update(current)


def scan_loop():
    global _scan_beat
    while True:
        _scan_beat = time.monotonic()
        try:
            found = {o.sid: Session(o.sid, o.family, o.state, o.ts, o.title, o.workspace, app_id=o.app_id,
                                    tokens=o.tokens, cached=o.cached)
                     for o in sources.scan()}
            with _lock:
                _files.clear()
                _files.update(found)
            refresh()
            _alert_format_drift()
        except Exception as exc:
            print(f"scan loop error: {exc}", flush=True)
        time.sleep(SCAN_S)


def record_hook(sid, source, state, pane="", workspace=""):
    now = time.time()
    with _lock:
        if state == "idle":
            _hooks.pop(sid, None)
            _acked[sid] = now
        else:
            prev = _hooks.get(sid)
            family = family_of(source)
            if prev and prev.family == "cursor":
                family = "cursor"
            _hooks[sid] = Session(sid, family, HOOK_STATE.get(state, "working"), now,
                                  prev.title if prev else "",
                                  workspace or (prev.workspace if prev else ""),
                                  pane or (prev.pane if prev else ""))
    refresh()


def frame():
    f = [OFF] * NLED
    with _lock:
        for sid, led in _slots.items():
            s = _view.get(sid)
            if s:
                f[led] = code_for(s.family, s.state)
        sel = _slots.get(_switch["sid"])
        if sel is not None and f[sel] != OFF:
            f[sel] |= SEL_BIT
    return f


# --- wheel switcher --------------------------------------------------------
# Turning the wheel walks a selection through the lit keys, then the most recent
# sessions that are off the pad (pad + Mac HUD show it); when the wheel rests
# for SWITCH_DWELL_S the selected session opens.

def _switch_order():
    by_led = {led: sid for sid, led in _slots.items()}
    live = [by_led[led] for led in LED_ORDER if led in by_led]
    recent = sorted((s for s in _view.values() if s.sid not in _slots), key=lambda s: -s.ts)
    return live + [s.sid for s in recent[:SWITCH_RECENT]]


def handle_scroll(step):
    now = time.monotonic()
    with _lock:
        order = _switch_order()
        if not order:
            return
        cur = _switch["sid"]
        if cur in order and now < _switch["until"]:
            i = (order.index(cur) + step) % len(order)
        else:
            i = 0 if step > 0 else len(order) - 1
        _switch.update(sid=order[i], until=now + SWITCH_DWELL_S, mode="wheel")
    _poke()


def handle_press(led):
    """Preview the key's session (pad + Mac HUD); it opens only if held for HOLD_S."""
    with _lock:
        sid = next((k for k, v in _slots.items() if v == led), None)
        if sid is None:
            return
        _switch.update(sid=sid, until=time.monotonic() + HOLD_S, mode="hold")
    _poke()


def handle_release(led):
    with _lock:
        if _switch["mode"] != "hold" or _slots.get(_switch["sid"]) != led:
            return
        _switch["sid"] = None
    _poke()


def switcher_tick():
    with _lock:
        sid = _switch["sid"]
        if sid is None or time.monotonic() < _switch["until"]:
            return
        _switch["sid"] = None
    _poke()
    threading.Thread(target=open_session, args=(sid,), daemon=True).start()


# --- opening sessions ------------------------------------------------------
_focus_last = {}


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


DEMO_LINKS = os.path.expanduser("~/.config/micropad/demo.json")


def _open_demo(sid):
    """A demo key opens its app, or the real session named for that app in
    DEMO_LINKS ({"claude": "local_…", "codex": "<thread id>", "cursor": "<composer id>"}),
    so recordings can end on a clean, staged conversation."""
    s = _demo.get(sid)
    if s is None:
        return False
    try:
        with open(DEMO_LINKS) as f:
            target = str(json.load(f).get(s.family) or "")
    except (OSError, ValueError, AttributeError):
        target = ""
    _run_hook("focus_session.sh", ["focus", "", target, s.family, "", ""])
    return True


def open_session(sid):
    if _demo is not None:
        return _open_demo(sid)
    with _lock:
        s = _view.get(sid)
        if s is None:
            return False
        led = _slots.get(sid)
        if s.state in ("done", "error"):
            _acked[sid] = time.time()
    now = time.monotonic()
    double = led is not None and (now - _focus_last.get(led, 0.0)) < DOUBLE_TAP_S
    if led is not None:
        _focus_last[led] = now
    mode = "foreground" if double else "focus"
    hook_state = {"working": "thinking", "done": "success"}.get(s.state, s.state)
    print(f"open {s.family} {sid[:8]} ({s.state}) {s.title!r}", flush=True)
    if s.pane.startswith("cmux:") and _cmux_rpc("surface.focus", {"surface_id": s.pane[5:]}) is not None:
        if double:
            subprocess.Popen(["open", "-b", "com.cmuxterm.app"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        _run_hook("focus_session.sh", [mode, s.pane, s.app_id or sid, s.family, s.workspace, hook_state])
    refresh()
    return True


def _row(s, now):
    return {"sid": s.sid, "family": s.family, "state": s.state, "age": int(now - s.ts), "ts": s.ts,
            "title": s.title, "workspace": s.workspace, "tokens": s.tokens, "cached": s.cached}


def snapshot():
    now = time.time()
    with _lock:
        by_led = {led: sid for sid, led in _slots.items()}
        keys = []
        for led in LED_ORDER:
            sid = by_led.get(led)
            s = _view.get(sid) if sid else None
            keys.append({"led": led, **(_row(s, now) if s else {})})
        recent = sorted(_view.values(), key=lambda s: -s.ts)
        others = [_row(s, now) for s in recent if s.sid not in _slots][:20]
        switcher = {"sid": _switch["sid"], "order": _switch_order() if _switch["sid"] else [],
                    "mode": _switch["mode"], "hold_ms": int(HOLD_S * 1000)}
    return {"pad": _pad_ok, "keys": keys, "others": others, "switcher": switcher,
            "warnings": sources.health()["warnings"]}


def stream_snapshot():
    """snapshot() without the ever-ticking ages, so /events only fires on real change."""
    snap = snapshot()
    for row in snap["keys"] + snap["others"]:
        row.pop("age", None)
    return json.dumps(snap, sort_keys=True)


# --- device ----------------------------------------------------------------

class Pad:
    """Sends 12-byte state frames over the VIA custom channel. Reconnects."""
    def __init__(self):
        self.dev = None
        self._last = None
        self._miss_logged = False
        self.wlock = threading.Lock()

    def _open(self):
        global _pad_ok
        for d in hid.enumerate(VID, PID):
            if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
                self.dev = hid.Device(path=d["path"])
                self._last = None
                self._miss_logged = False
                _pad_ok = True
                print("micropad connected", flush=True)
                return True
        _pad_ok = False
        if not self._miss_logged:
            print("micropad not open (unplugged, or usevia.app still has WebHID)", flush=True)
            self._miss_logged = True
        return False

    def _drop(self):
        global _pad_ok
        self.dev = None
        _pad_ok = False

    def send(self, value, data=b""):
        if self.dev is None and not self._open():
            return False
        payload = bytes([ID_CUSTOM_SET_VALUE, CH_AGENT, value]) + bytes(data)
        try:
            with self.wlock:
                self.dev.write(b"\x00" + payload + b"\x00" * (32 - len(payload)))
            return True
        except Exception:
            self._drop()
            return False

    def show(self, f):
        f = bytes(f)
        if f != self._last and self.send(VAL_FRAME, f):
            self._last = f

    def read(self, timeout_ms=120):
        if self.dev is None and not self._open():
            time.sleep(timeout_ms / 1000)
            return None
        try:
            d = self.dev.read(32, timeout_ms)
            return bytes(d) if d else None
        except Exception:
            self._drop()
            return None


PAD = Pad()
DASHBOARD = os.path.join(HERE, "dashboard.html")


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        try:
            body = self._body()
        except ValueError:
            self.send_response(400); self.end_headers(); return
        if self.path in ("/open", "/bootloader", "/scroll", "/demo") and self.headers.get("X-Micropad") != "1":
            self.send_response(403); self.end_headers(); return
        if self.path == "/state":
            source = body.get("source", "claude-code")
            sid = body.get("session") or source
            record_hook(sid, source, body.get("state", "idle"),
                        body.get("pane") or "", body.get("workspace") or "")
            self.send_response(204); self.end_headers(); return
        if self.path == "/open":
            sid = body.get("sid")
            if sid is None and "led" in body:
                with _lock:
                    sid = next((k for k, v in _slots.items() if v == int(body["led"])), None)
            self._json(200 if sid and open_session(sid) else 404, {}); return
        if self.path == "/demo":
            ok = set_demo(str(body.get("scene", "off")))
            self._json(200 if ok else 404, {"scenes": ["off", *DEMO_SCENES]}); return
        if self.path == "/scroll":
            handle_scroll(1 if int(body.get("step", 1)) > 0 else -1)
            self._json(200, {"switcher": snapshot()["switcher"]["sid"]}); return
        if self.path == "/bootloader":
            self._json(200 if PAD.send(VAL_BOOTLOADER) else 503, {}); return
        self.send_response(404); self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "pad": _pad_ok}); return
        if self.path == "/state":
            self._json(200, snapshot()); return
        if self.path == "/events":
            self._events(); return
        if self.path in ("/", "/index.html"):
            try:
                with open(DASHBOARD, "rb") as f:
                    payload = f.read()
            except OSError:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload); return
        self.send_response(404); self.end_headers()

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        last = None
        try:
            while True:
                payload = stream_snapshot()
                if payload != last:
                    self.wfile.write(b"data: " + payload.encode() + b"\n\n")
                    last = payload
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                with _changed:
                    _changed.wait(15)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def log_message(self, *a):
        pass


def main():
    _load()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True).start()
    print(f"micropad-agent on http://127.0.0.1:{PORT}/", flush=True)
    last_beat = 0.0
    while True:
        try:
            now = time.monotonic()
            if now - _scan_beat > WATCHDOG_S:
                print("watchdog: session scanner stalled, exiting so launchd restarts us", flush=True)
                os._exit(1)
            if now - last_beat > HEARTBEAT_S:
                PAD._last = None
                last_beat = now
            switcher_tick()
            PAD.show(frame())
            ev = PAD.read(60)
            if ev and len(ev) >= 2 and ev[0] == MSG_FOCUS:
                handle_press(ev[1])
            elif ev and len(ev) >= 2 and ev[0] == MSG_RELEASE:
                handle_release(ev[1])
            elif ev and len(ev) >= 2 and ev[0] == MSG_SCROLL:
                handle_scroll(1 if ev[1] == 1 else -1)
        except Exception as exc:
            print(f"render loop error: {exc}", flush=True)
            PAD._drop()
            time.sleep(0.5)


if __name__ == "__main__":
    main()
