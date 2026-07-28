#!/bin/sh
# micropad-agent — focus the terminal/pane running an agent session.
#
# Called by daemon_perkey.py when you HOLD the bottom-left touch and TAP a lit
# status key. Single tap -> mode "focus"; double tap -> mode "foreground".
#
#   $1  mode      focus | foreground
#   $2  pane      the hint claude_state.sh captured (e.g. "cmux:<uuid>")
#   $3  session   the session id
#
# The pane hint format is "<kind>:<id>". Runs from the daemon (bare env), so use
# absolute tool paths. Add a case for your terminal/mux if it isn't here.
MODE="$1"; PANE="$2"; SID="$3"
echo "$(date '+%H:%M:%S') focus mode=$MODE pane=$PANE sid=$SID" >> /tmp/micropad-focus.log

CMUX_BIN=/Applications/cmux.app/Contents/Resources/bin/cmux

case "$PANE" in
  cmux:*)    # cmux: switch to the surface. The socket only accepts processes
    # started inside cmux, so we reuse the capability token the hook captured
    # (claude_state.sh refreshes ~/.cache/micropad/cmux-auth.env from inside cmux).
    [ -f "$HOME/.cache/micropad/cmux-auth.env" ] && . "$HOME/.cache/micropad/cmux-auth.env"
    export CMUX_SOCKET_CAPABILITY CMUX_SOCKET_PATH
    "$CMUX_BIN" rpc surface.focus "{\"surface_id\":\"${PANE#cmux:}\"}" >/dev/null 2>&1
    [ "$MODE" = foreground ] && open -b com.cmuxterm.app 2>/dev/null ;;
  orca:*)    orca terminal switch --terminal "${PANE#orca:}" 2>/dev/null ;;   # handle = term_xxx
  tmux:*)    tmux select-pane -t "${PANE#tmux:}" 2>/dev/null ;;
  wezterm:*) wezterm cli activate-pane --pane-id "${PANE#wezterm:}" 2>/dev/null ;;
  kitty:*)   kitten @ focus-window --match "id:${PANE#kitty:}" 2>/dev/null ;;
  *)         : ;;   # unknown terminal — add a case for yours
esac
exit 0
