"""Read live agent sessions straight from the files each app already writes.

Hooks only see what runs on this Mac and miss events. The apps' own session
files are what their GUIs render from, so scanning them makes the pad show what
the Claude, Codex and Cursor windows show, including Claude.app sessions that
run over SSH (Claude.app mirrors those transcripts locally).

scan() returns one Observation per session active within RECENT_S. State is one of
"working", "waiting", "done", "error"; ts is the wall time of the record that
decided it.
"""
import glob
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

HOME = os.path.expanduser("~")
RECENT_S = 3 * 3600          # files untouched for longer are not looked at
SUBAGENT_LIVE_S = 180        # a subagent that wrote this recently keeps its parent "working"
APP_TURN_LIVE_S = 600        # an open Claude.app turn with activity this recent counts as running
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


@dataclass
class Observation:
    family: str              # claude | codex | cursor
    sid: str
    state: str
    ts: float
    title: str = ""
    workspace: str = ""
    app_id: str = ""         # id the owning app's deep link expects, when it differs from sid


def _iso(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _tail_records(path, want=lambda d: True, limit=400, max_bytes=8 << 20):
    """Parsed JSON records from the end of a JSONL file, newest first.
    Grows the read window until something matching `want` turns up, because
    one tool result line can be megabytes long."""
    out = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            window = 256 << 10
            while True:
                start = max(0, size - window)
                f.seek(start)
                lines = f.read(size - start).split(b"\n")
                if start > 0:
                    lines = lines[1:]
                out = []
                for raw in reversed(lines):
                    if not raw.strip():
                        continue
                    try:
                        out.append(json.loads(raw))
                    except ValueError:
                        continue
                    if len(out) >= limit:
                        break
                if start == 0 or any(want(d) for d in out) or window >= max_bytes:
                    return out
                window *= 4
    except OSError:
        return out


class _Cache:
    """Re-parse a file only when its size or mtime changed."""
    def __init__(self):
        self._d = {}

    def get(self, path, st, fn):
        k = (st.st_size, st.st_mtime)
        hit = self._d.get(path)
        if hit and hit[0] == k:
            return hit[1]
        val = fn(path)
        self._d[path] = (k, val)
        return val


_cache = _Cache()


# --- Claude (Claude.app local + SSH sessions, Claude Code CLI) -------------

def _claude_turn(d):
    return (d.get("type") in ("user", "assistant") and not d.get("isSidechain")) or (
        d.get("type") == "system" and d.get("subtype") == "stop_hook_summary")


def _claude_file(path):
    recs = _tail_records(path, _claude_turn)
    title = next((d.get("customTitle") for d in recs if d.get("type") == "custom-title"), "")
    cwd = next((d.get("cwd") for d in recs if d.get("cwd")), "")
    last = next((d for d in recs if _claude_turn(d)), None)
    if last is None:
        return None
    ts = _iso(last.get("timestamp", ""))
    msg = last.get("message") or {}
    if last["type"] == "system":
        state = "done"
    elif last["type"] == "assistant":
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        tools = [c.get("name") for c in content if c.get("type") == "tool_use"]
        if msg.get("stop_reason") == "tool_use" or tools:
            state = "waiting" if WAITING_TOOLS & set(tools) else "working"
        elif last.get("isApiErrorMessage"):
            state = "error"
        elif msg.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens"):
            state = "done"
        else:
            state = "working"
    else:
        text = msg.get("content")
        if isinstance(text, list):
            text = " ".join(c.get("text", "") for c in text if isinstance(c, dict))
        state = "done" if "[Request interrupted by user" in (text or "") else "working"
    return state, ts, title, cwd


def _claude_subagent_parent(path):
    recs = _tail_records(path, lambda d: "sessionId" in d, limit=5)
    d = next((r for r in recs if r.get("sessionId")), None)
    return (d["sessionId"], _iso(d.get("timestamp", ""))) if d else None


CLAUDE_APP_SESSIONS = os.path.join(HOME, "Library", "Application Support", "Claude", "claude-code-sessions")


def _claude_app_record(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    last = d.get("lastAssistantUuid")
    turn_open = bool(last) and d.get("postTurnSummaryFor") != last
    return (d.get("cliSessionId"), d.get("sessionId"), d.get("title") or "", bool(d.get("isArchived")),
            (d.get("lastActivityAt") or 0) / 1000, turn_open, d.get("cwd") or "")


def claude_app_index():
    """Claude.app's own session list: transcript id -> (app session id, title, archived).
    Its deep links only accept the app id (local_...), and its titles are the
    ones shown in the sidebar."""
    index = {}
    for path in glob.glob(os.path.join(CLAUDE_APP_SESSIONS, "*", "*", "local_*.json")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        rec = _cache.get(path, st, _claude_app_record)
        if rec and rec[0]:
            index[rec[0]] = rec[1:]
    return index


def scan_claude(now):
    out = []
    app = claude_app_index()
    sub_last = {}   # parent session id -> newest subagent record time
    root = os.path.join(HOME, ".claude", "projects")
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")) + glob.glob(
            os.path.join(root, "*", "*", "subagents", "*.jsonl")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime > RECENT_S:
            continue
        name = os.path.basename(path)
        if name.startswith("agent-"):
            if now - st.st_mtime <= 3600:
                hit = _cache.get(path, st, _claude_subagent_parent)
                if hit:
                    sub_last[hit[0]] = max(sub_last.get(hit[0], 0.0), hit[1])
            continue
        res = _cache.get(path, st, _claude_file)
        if res is None:
            continue
        state, ts, title, cwd = res
        sid = name[:-6]
        app_id, app_title, archived = app.get(sid, ("", "", False))[:3]
        if archived:
            continue
        out.append(Observation("claude", sid, state, ts, app_title or title, cwd, app_id or ""))
    for o in out:
        sub = sub_last.get(o.sid, 0.0)
        if o.state == "done" and sub > o.ts:
            o.state = "working" if now - sub <= SUBAGENT_LIVE_S else "done"
            o.ts = sub
    # Claude.app's session record is what its sidebar "Running" reads from, and
    # it stays current even when the local transcript mirror lags behind.
    seen = {o.sid: o for o in out}
    for sid, (app_id, title, archived, activity, turn_open, cwd) in app.items():
        if archived or not turn_open or now - activity > APP_TURN_LIVE_S:
            continue
        o = seen.get(sid)
        if o is None:
            out.append(Observation("claude", sid, "working", activity, title, cwd, app_id))
        elif o.state != "waiting" and activity > o.ts:
            o.state, o.ts = "working", activity
    return out


# --- Codex desktop / CLI ---------------------------------------------------

def _codex_file(path):
    recs = _tail_records(path, lambda d: (d.get("payload") or {}).get("type") in (
        "task_started", "task_complete", "turn_aborted"))
    for d in recs:
        kind = (d.get("payload") or {}).get("type")
        if kind == "task_started":
            return "working", _iso(d.get("timestamp", ""))
        if kind == "task_complete":
            return "done", _iso(d.get("timestamp", ""))
        if kind == "turn_aborted":
            return "done", _iso(d.get("timestamp", ""))
    return None


def scan_codex(now):
    dbs = sorted(glob.glob(os.path.join(HOME, ".codex", "state_*.sqlite")),
                 key=lambda p: int(p.rsplit("_", 1)[1].split(".")[0]) if p.rsplit("_", 1)[1].split(".")[0].isdigit() else -1)
    if not dbs:
        return []
    db = dbs[-1]
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        rows = con.execute(
            "SELECT id, rollout_path, COALESCE(NULLIF(name,''), NULLIF(title,''), first_user_message, ''), cwd "
            "FROM threads WHERE archived = 0 AND updated_at_ms > ?",
            (int((now - RECENT_S) * 1000),)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    out = []
    for tid, path, title, cwd in rows:
        try:
            st = os.stat(path)
        except (OSError, TypeError):
            continue
        res = _cache.get(path, st, _codex_file)
        if res:
            out.append(Observation("codex", tid, res[0], res[1], (title or "")[:80], cwd or ""))
    return out


# --- Cursor agent chats ----------------------------------------------------

def _cursor_title(path):
    try:
        with open(path, "rb") as f:
            first = json.loads(f.readline())
        text = first["message"]["content"][0]["text"]
    except Exception:
        return ""
    if "<user_query>" in text:
        text = text.split("<user_query>", 1)[1].split("</user_query>", 1)[0]
    return " ".join(text.split())[:80]


def _cursor_file(path):
    recs = _tail_records(path, limit=3)
    last = recs[0] if recs else {}
    mtime = os.path.getmtime(path)
    if last.get("type") == "turn_ended":
        state = "error" if last.get("status") == "error" else "done"
    else:
        state = "working"
    return state, mtime, _cursor_title(path)


_ws_cache = {}


def _cursor_workspace(project_dir):
    """Cursor names the project folder after the workspace path with every
    separator turned into '-'. Walk the filesystem to find the real path."""
    if project_dir in _ws_cache:
        return _ws_cache[project_dir]
    parts = project_dir.split("-")

    def walk(base, i):
        if i == len(parts):
            return base
        for j in range(len(parts), i, -1):
            for joiner in ("-", " ", ".", "_"):
                cand = os.path.join(base, joiner.join(parts[i:j]))
                if os.path.isdir(cand):
                    found = walk(cand, j)
                    if found:
                        return found
        return None

    found = walk("/", 0) or ""
    _ws_cache[project_dir] = found
    return found


def scan_cursor(now):
    out = []
    for path in glob.glob(os.path.join(HOME, ".cursor", "projects", "*", "agent-transcripts", "*", "*.jsonl")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime > RECENT_S:
            continue
        sid = os.path.basename(path)[:-6]
        if os.path.basename(os.path.dirname(path)) != sid:
            continue
        state, ts, title = _cache.get(path, st, _cursor_file)
        project = path.split(os.sep + "projects" + os.sep, 1)[1].split(os.sep, 1)[0]
        out.append(Observation("cursor", sid, state, ts, title, _cursor_workspace(project)))
    return out


def scan(now=None):
    now = now or time.time()
    out = []
    for fn in (scan_claude, scan_codex, scan_cursor):
        try:
            out.extend(fn(now))
        except Exception as exc:
            print(f"scan {fn.__name__} failed: {exc}", flush=True)
    return out


if __name__ == "__main__":
    now = time.time()
    for o in sorted(scan(now), key=lambda o: -o.ts):
        print(f"{o.family:6} {o.state:8} {int(now - o.ts):>6}s  {o.sid[:12]}  {o.title[:50]!r}  {o.workspace}")
