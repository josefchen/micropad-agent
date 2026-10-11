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
import re
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

HOME = os.path.expanduser("~")
RECENT_S = 3 * 3600          # files untouched for longer are not looked at
SUBAGENT_LIVE_S = 180        # a subagent that wrote this recently keeps its parent "working"
PERMISSION_WAIT_S = 15        # unanswered tool call in a prompting mode -> waiting on you
PROMPTING_MODES = {"default", "acceptEdits", "plan"}
NO_PROMPT_TOOLS = {"Read", "Glob", "Grep", "LS", "TodoWrite", "Task", "Agent", "WebSearch", "Monitor"}
CURSOR_ACTIVE_S = 600         # a Cursor checkpoint this recent with no turn end counts as running
CURSOR_END_SLACK_S = 30       # turn_ended may land a little before the final checkpoint
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
    tokens: int = 0          # tokens processed so far (input incl. cache + output); 0 = unknown
    cached: int = 0          # how many of `tokens` were cache reads


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


class _Usage:
    """Running token totals for append-only JSONL transcripts. Each call parses
    only the lines added since the last one, one line in memory at a time
    (transcripts reach hundreds of MB), so a long session costs nothing once read."""

    def __init__(self, extract):
        self._extract = extract     # raw line -> (key, total, cached) or None
        self._d = {}                # path -> [offset, {key: (total, cached)}]

    def get(self, path):
        """(tokens, cached tokens) for the transcript at `path`."""
        st = self._d.setdefault(path, [0, {}])
        try:
            with open(path, "rb") as f:
                f.seek(st[0])
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break
                    st[0] += len(raw)
                    hit = self._extract(raw) if b'"usage"' in raw else None
                    if hit:
                        st[1][hit[0]] = (hit[1], hit[2])
        except OSError:
            pass
        totals = st[1].values()
        return sum(t for t, _ in totals), sum(c for _, c in totals)



def _usage_object(raw):
    """Decode just the `"usage": {...}` object inside a raw transcript line.
    Lines carry multi-MB tool payloads; decoding a whole line to read one
    small field bloats the heap for good."""
    # Last match: usage follows the message content, where escaped copies can appear.
    m = None
    for m in _USAGE_KEY.finditer(raw):
        pass
    if m is None:
        return None
    try:
        obj, _ = _decoder.raw_decode(raw[m.end() - 1:].decode("utf-8", "replace"))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


_decoder = json.JSONDecoder()
_MSG_ID = re.compile(rb'"id":\s*"(msg_[A-Za-z0-9]+)"')
_USAGE_KEY = re.compile(rb'(?<!\\)"usage":\s*\{')
_ASSISTANT = re.compile(rb'"type":\s*"assistant"')


def _claude_usage_record(raw):
    # Claude writes one line per content block, all carrying the same message's usage.
    if not _ASSISTANT.search(raw):
        return None
    mid = _MSG_ID.search(raw)
    u = _usage_object(raw) if mid else None
    if not u:
        return None
    cached = u.get("cache_read_input_tokens") or 0
    total = (u.get("input_tokens") or 0) + cached + (u.get("cache_creation_input_tokens") or 0) + (
        u.get("output_tokens") or 0)
    return mid.group(1), total, cached


_claude_usage = _Usage(_claude_usage_record)


# --- Claude (Claude.app local + SSH sessions, Claude Code CLI) -------------

def _claude_turn(d):
    return (d.get("type") in ("user", "assistant") and not d.get("isSidechain")) or (
        d.get("type") == "system" and d.get("subtype") == "stop_hook_summary")


def _claude_file(path):
    """(state, ts, title, cwd, pending tool names) from the newest turn record."""
    recs = _tail_records(path, _claude_turn)
    title = next((d.get("customTitle") for d in recs if d.get("type") == "custom-title"), "")
    cwd = next((d.get("cwd") for d in recs if d.get("cwd")), "")
    last = next((d for d in recs if _claude_turn(d)), None)
    if last is None:
        return None
    ts = _iso(last.get("timestamp", ""))
    msg = last.get("message") or {}
    tools = []
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
    return state, ts, title, cwd, tools


def _claude_subagent_parent(path):
    recs = _tail_records(path, lambda d: "sessionId" in d, limit=5)
    d = next((r for r in recs if r.get("sessionId")), None)
    return (d["sessionId"], _iso(d.get("timestamp", ""))) if d else None


CLAUDE_APP_SESSIONS = os.path.join(HOME, "Library", "Application Support", "Claude", "claude-code-sessions")


@dataclass
class ClaudeAppSession:
    app_id: str
    title: str
    archived: bool
    activity: float
    turn_open: bool
    cwd: str
    permission_mode: str
    error_at: float


def _claude_app_record(path):
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    last = d.get("lastAssistantUuid")
    return d.get("cliSessionId"), ClaudeAppSession(
        app_id=d.get("sessionId") or "",
        title=d.get("title") or "",
        archived=bool(d.get("isArchived")),
        activity=(d.get("lastActivityAt") or 0) / 1000,
        turn_open=bool(last) and d.get("postTurnSummaryFor") != last,
        cwd=d.get("cwd") or "",
        permission_mode=d.get("permissionMode") or "",
        error_at=(d.get("errorAt") or 0) / 1000 if d.get("error") else 0.0,
    )


def claude_app_index():
    """Claude.app's own session list, keyed by transcript id. Its deep links
    only accept the app id (local_...), its titles are the sidebar's, and it
    knows when a turn is open even if the transcript mirror lags."""
    index = {}
    for path in glob.glob(os.path.join(CLAUDE_APP_SESSIONS, "*", "*", "local_*.json")):
        try:
            st = os.stat(path)
        except OSError:
            continue
        rec = _cache.get(path, st, _claude_app_record)
        if rec and rec[0]:
            index[rec[0]] = rec[1]
    return index


_claude_paths = {}   # session id -> transcript path


def scan_claude(now):
    out = []
    app = claude_app_index()
    sub_last = {}   # parent session id -> newest subagent record time
    pending = {}    # session id -> tool names awaiting a result
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
        _health["claude"]["files"] += 1
        res = _cache.get(path, st, _claude_file)
        if res is None:
            continue
        state, ts, title, cwd, tools = res
        sid = name[:-6]
        _claude_paths[sid] = path
        a = app.get(sid)
        if a and a.archived:
            continue
        pending[sid] = tools
        out.append(Observation("claude", sid, state, ts, (a.title if a else "") or title, cwd,
                               a.app_id if a else ""))
    for o in out:
        path = _claude_paths.get(o.sid)
        if path:
            o.tokens, o.cached = _claude_usage.get(path)
            for sub_path in glob.glob(os.path.join(path[:-6], "subagents", "*.jsonl")):
                tokens, cached = _claude_usage.get(sub_path)
                o.tokens += tokens
                o.cached += cached
        sub = sub_last.get(o.sid, 0.0)
        if o.state == "done" and sub > o.ts:
            o.state = "working" if now - sub <= SUBAGENT_LIVE_S else "done"
            o.ts = sub
    seen = {o.sid: o for o in out}
    for sid, a in app.items():
        if a.archived:
            continue
        o = seen.get(sid)
        if a.turn_open and now - a.activity <= APP_TURN_LIVE_S:
            if o is None:
                o = Observation("claude", sid, "working", a.activity, a.title, a.cwd, a.app_id)
                out.append(o)
                seen[sid] = o
            elif o.state != "waiting" and a.activity > o.ts:
                o.state, o.ts = "working", a.activity
        if o is None:
            continue
        if a.error_at and a.error_at >= o.ts and now - a.error_at <= RECENT_S:
            o.state, o.ts = "error", a.error_at
        # Permission prompts are never written to disk. In a mode that can
        # prompt, a tool call left unanswered this long is almost always one.
        if (o.state == "working" and a.permission_mode in PROMPTING_MODES and pending.get(sid)
                and not set(pending[sid]) <= NO_PROMPT_TOOLS
                and now - max(o.ts, a.activity) >= PERMISSION_WAIT_S):
            o.state = "waiting"
    return out


# --- Codex desktop / CLI ---------------------------------------------------

def _codex_usage(recs):
    """(total, cached) tokens from the newest token_count event."""
    for d in recs:
        p = d.get("payload") or {}
        info = p.get("info") if p.get("type") == "token_count" else None
        if info:
            total = info.get("total_token_usage") or {}
            return total.get("total_tokens") or 0, total.get("cached_input_tokens") or 0
    return 0, 0


def _codex_file(path):
    recs = _tail_records(path, lambda d: (d.get("payload") or {}).get("type") in (
        "task_started", "task_complete", "turn_aborted"))
    usage = _codex_usage(recs)
    for d in recs:
        kind = (d.get("payload") or {}).get("type")
        if kind == "task_started":
            return "working", _iso(d.get("timestamp", "")), usage
        if kind in ("task_complete", "turn_aborted"):
            return "done", _iso(d.get("timestamp", "")), usage
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
    _health["codex"]["files"] = len(rows)
    out = []
    for tid, path, title, cwd in rows:
        try:
            st = os.stat(path)
        except (OSError, TypeError):
            continue
        res = _cache.get(path, st, _codex_file)
        if res:
            tokens, cached = res[2]
            out.append(Observation("codex", tid, res[0], res[1], (title or "")[:80], cwd or "",
                                   tokens=tokens, cached=cached))
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


CURSOR_DB = os.path.join(HOME, "Library", "Application Support", "Cursor", "User", "globalStorage", "state.vscdb")


def cursor_headers(now):
    """Cursor's chat list as the Agents window sees it: composer id -> header."""
    if not os.path.exists(CURSOR_DB):
        return {}
    con = sqlite3.connect(f"file:{CURSOR_DB}?mode=ro", uri=True, timeout=1)
    try:
        rows = con.execute(
            "SELECT composerId, value FROM composerHeaders WHERE isSubagent = 0 AND isArchived = 0 "
            "AND MAX(lastUpdatedAt, COALESCE(checkpointAt, 0)) > ?",
            (int((now - RECENT_S) * 1000),)).fetchall()
    finally:
        con.close()
    out = {}
    for cid, value in rows:
        try:
            out[cid] = json.loads(value)
        except (TypeError, ValueError):
            continue
    return out


def scan_cursor(now):
    files = {}
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
        files[sid] = (state, ts, title, project)
    _health["cursor"]["files"] = len(files)
    try:
        heads = cursor_headers(now)
    except sqlite3.Error as exc:
        _health["cursor"]["error"] = f"composerHeaders: {exc}"
        heads = {}

    out = []
    for sid in set(files) | set(heads):
        h = heads.get(sid, {})
        state, ts, title, project = files.get(sid, ("working", 0.0, "", ""))
        ws = ((h.get("workspaceIdentifier") or {}).get("uri") or {}).get("fsPath") or (
            _cursor_workspace(project) if project else "")
        activity = max(h.get("conversationCheckpointLastUpdatedAt") or 0, h.get("lastUpdatedAt") or 0) / 1000
        if h.get("hasBlockingPendingActions"):
            state, ts = "waiting", max(activity, ts)
        elif sid in files and state in ("done", "error") and ts >= activity - CURSOR_END_SLACK_S:
            pass
        elif activity > ts and now - activity <= CURSOR_ACTIVE_S:
            state, ts = "working", activity
        elif sid not in files:
            continue
        out.append(Observation("cursor", sid, state, ts, h.get("name") or title, ws))
    return out


_health = {}


def health():
    """Per-source counts from the last scan, plus a warning when a source has
    files to read but yields nothing (an app update probably changed a format)."""
    warnings = []
    for name, h in _health.items():
        if h.get("error"):
            warnings.append(f"{name}: {h['error']}")
        elif h.get("files", 0) > 0 and h.get("parsed", 0) == 0:
            warnings.append(f"{name}: {h['files']} recent session files but none could be read")
    return {"sources": _health, "warnings": warnings}


def scan(now=None):
    now = now or time.time()
    out = []
    for name, fn in (("claude", scan_claude), ("codex", scan_codex), ("cursor", scan_cursor)):
        _health[name] = {"files": 0, "parsed": 0, "error": ""}
        try:
            found = fn(now)
        except Exception as exc:
            _health[name]["error"] = f"{type(exc).__name__}: {exc}"
            print(f"scan {name} failed: {exc}", flush=True)
            continue
        _health[name]["parsed"] = len(found)
        out.extend(found)
    return out


if __name__ == "__main__":
    now = time.time()
    for o in sorted(scan(now), key=lambda o: -o.ts):
        print(f"{o.family:6} {o.state:8} {int(now - o.ts):>6}s  {o.sid[:12]}  {o.title[:50]!r}  {o.workspace}")
    print(json.dumps(health(), indent=1))
