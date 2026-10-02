#!/bin/sh
# micropad-agent — Claude Code hook target.  $1 = state.
#
# Claude pipes the hook JSON to us on stdin; we pull session_id out of it so the
# per-key daemon (daemon_perkey.py, v2) can give each concurrent session its own
# LED. Pure sed (BSD-safe, no jq/python) so it stays cheap on hot events like
# PreToolUse. If session_id is missing the daemon falls back to per-source.
STATE="$1"
INPUT=$(cat)
field() {
  printf '%s' "$INPUT" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" | head -1
}
SID=$(field session_id)
CWD=$(field cwd)

# Best-effort pane hint so tap-to-focus (v2) can find this session's terminal.
# Whatever the terminal/mux exports into the environment; focus_session.sh reads it.
PANE=""
[ -n "${CMUX_SURFACE_ID:-}" ]    && PANE="cmux:$CMUX_SURFACE_ID"
[ -z "$PANE" ] && [ -n "${ORCA_PANE_KEY:-}" ]    && PANE="orca:$ORCA_PANE_KEY"
[ -z "$PANE" ] && [ -n "${TMUX_PANE:-}" ]        && PANE="tmux:$TMUX_PANE"
[ -z "$PANE" ] && [ -n "${WEZTERM_PANE:-}" ]     && PANE="wezterm:$WEZTERM_PANE"
[ -z "$PANE" ] && [ -n "${KITTY_WINDOW_ID:-}" ]  && PANE="kitty:$KITTY_WINDOW_ID"
[ -z "$PANE" ] && [ -n "${TERM_SESSION_ID:-}" ]  && PANE="termid:$TERM_SESSION_ID"

# cmux's socket only admits processes started inside cmux, so hand the daemon
# (which runs under launchd, outside cmux) the capability token to reuse.
if [ -n "${CMUX_SOCKET_CAPABILITY:-}" ]; then
  mkdir -p "$HOME/.cache/micropad" 2>/dev/null
  ( umask 077
    printf 'CMUX_SOCKET_CAPABILITY=%s\nCMUX_SOCKET_PATH=%s\n' \
      "$CMUX_SOCKET_CAPABILITY" "${CMUX_SOCKET_PATH:-}" > "$HOME/.cache/micropad/cmux-auth.env" ) 2>/dev/null
fi

curl -s -m 1 -X POST http://127.0.0.1:48801/state \
  -H 'Content-Type: application/json' \
  -d "{\"state\":\"$STATE\",\"source\":\"claude-code\",\"session\":\"$SID\",\"pane\":\"$PANE\",\"workspace\":\"$CWD\"}" \
  >/dev/null 2>&1 || true
exit 0
