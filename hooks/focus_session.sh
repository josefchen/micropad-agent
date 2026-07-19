#!/bin/sh
# micropad-agent — focus the terminal/pane running an agent session.
#
# Called by daemon_perkey.py when you HOLD the bottom-left touch and TAP a lit
# status key. Single tap -> mode "focus"; double tap -> mode "foreground".
#
#   $1  mode      focus | foreground
#   $2  pane      the hint claude_state.sh captured (e.g. "tmux:%3", "orca:...")
#   $3  session   the session id
#
# TAILOR the cases below to your terminal / multiplexer. The pane hint format is
# "<kind>:<id>"; add your own `kind` if it isn't here. This ships as best-effort.
MODE="$1"; PANE="$2"; SID="$3"
echo "$(date '+%H:%M:%S') focus mode=$MODE pane=$PANE sid=$SID" >> /tmp/micropad-focus.log

case "$PANE" in
  orca:*)    orca terminal focus "${PANE#orca:}" 2>/dev/null ;;   # verify against your orca CLI
  tmux:*)    tmux select-pane -t "${PANE#tmux:}" 2>/dev/null ;;
  wezterm:*) wezterm cli activate-pane --pane-id "${PANE#wezterm:}" 2>/dev/null ;;
  kitty:*)   kitten @ focus-window --match "id:${PANE#kitty:}" 2>/dev/null ;;
  *)         : ;;   # unknown terminal — add a case for yours
esac

# Double-tap also raises the terminal GUI app to the front. Set TERM_APP to your
# terminal's app name (e.g. "WezTerm", "Ghostty", "iTerm2") to enable.
if [ "$MODE" = foreground ] && [ -n "${TERM_APP:-}" ]; then
  osascript -e "tell application \"$TERM_APP\" to activate" 2>/dev/null
fi
exit 0
