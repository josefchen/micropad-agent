#!/bin/sh
# micropad-agent — Claude Code hook target.  $1 = state.
#
# Claude pipes the hook JSON to us on stdin; we pull session_id out of it so the
# per-key daemon (daemon_perkey.py, v2) can give each concurrent session its own
# LED. Pure sed (BSD-safe, no jq/python) so it stays cheap on hot events like
# PreToolUse. If session_id is missing the daemon falls back to per-source.
STATE="$1"
SID="$(sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"

curl -s -m 1 -X POST http://127.0.0.1:48801/state \
  -H 'Content-Type: application/json' \
  -d "{\"state\":\"$STATE\",\"source\":\"claude-code\",\"session\":\"$SID\"}" \
  >/dev/null 2>&1 || true
exit 0
