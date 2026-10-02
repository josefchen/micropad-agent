#!/bin/sh
# Cursor hook target. $1 = state (thinking|coding|success|idle|error).
# $2 = sub  → this event is a subagent; ignore it unless it has its own id.
#
# Cursor pipes the hook JSON on stdin. Each conversation (and each subagent)
# is its own source so one finish can flash green while another chat is still
# working. Best-effort: a dead daemon must never block the agent.
STATE="$1"
KIND="$2"
INPUT=$(cat)

field() {
  printf '%s' "$INPUT" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" | head -1
}

CID=$(field conversation_id)
[ -z "$CID" ] && CID=$(field session_id)
WS=$(printf '%s' "$INPUT" | sed -n 's/.*"workspace_roots"[[:space:]]*:[[:space:]]*\["\([^"]*\)".*/\1/p' | head -1)
SUB=$(field subagent_id)
[ -z "$SUB" ] && SUB=$(field agent_id)

if [ "$KIND" = "sub" ]; then
  [ -z "$SUB" ] && exit 0
  CID="sub-$SUB"
fi
[ -z "$CID" ] && CID="default"

if [ "$STATE" = "success" ]; then
  STATUS=$(field status)
  case "$STATUS" in
    error|failed) STATE=error ;;
    aborted|cancelled|canceled) STATE=idle ;;
  esac
fi

curl -s -m 1 -X POST http://127.0.0.1:48801/state \
  -H 'Content-Type: application/json' \
  -d "{\"state\":\"$STATE\",\"source\":\"cursor\",\"session\":\"$CID\",\"workspace\":\"$WS\"}" \
  >/dev/null 2>&1 || true
exit 0
