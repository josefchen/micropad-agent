#!/bin/sh
# Open the agent session assigned to a Micro Pad key.
#
#   $1 mode        focus | foreground
#   $2 pane        terminal hint, or empty
#   $3 session     session / thread / conversation id
#   $4 source      claude-code | cursor | codex
#   $5 workspace   project folder, or empty
#   $6 state       thinking | coding | waiting | success | error
MODE="$1"
PANE="$2"
SID="$3"
SOURCE="$4"
WS="$5"
STATE="$6"
echo "$(date '+%H:%M:%S') focus mode=$MODE source=$SOURCE state=$STATE sid=$SID ws=$WS pane=$PANE" >> /tmp/micropad-focus.log

enc() {
  /usr/bin/python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

case "$SOURCE" in
  cursor*)
    # Cursor's built-in deeplink extension runs in the Agents window too, so
    # this lands there without spawning an editor window.
    if [ -n "$SID" ]; then
      open "cursor://anysphere.cursor-deeplink/agent?id=$(enc "$SID")"
    fi
    osascript -e 'tell application "Cursor" to activate' \
      -e 'tell application "System Events" to tell process "Cursor" to perform action "AXRaise" of window "Cursor Agents"' \
      >/dev/null 2>&1 || open -a "Cursor"
    ;;
  claude*)
    if [ -n "$SID" ]; then
      if [ "$STATE" = "waiting" ]; then
        open "claude://code/needs-input?session=$(enc "$SID")&source=micropad"
      else
        open "claude://code/continue?session=$(enc "$SID")&source=micropad"
      fi
    else
      open -a "Claude"
    fi
    ;;
  codex*)
    if [ -n "$SID" ] && [ "$SID" != "codex" ]; then
      open "codex://threads/$(enc "$SID")"
    else
      open -a "Codex" 2>/dev/null || open -a "ChatGPT"
    fi
    ;;
esac

CMUX_BIN=/Applications/cmux.app/Contents/Resources/bin/cmux
case "$PANE" in
  cmux:*)
    [ -f "$HOME/.cache/micropad/cmux-auth.env" ] && . "$HOME/.cache/micropad/cmux-auth.env"
    export CMUX_SOCKET_CAPABILITY CMUX_SOCKET_PATH
    "$CMUX_BIN" rpc surface.focus "{\"surface_id\":\"${PANE#cmux:}\"}" >/dev/null 2>&1
    [ "$MODE" = "foreground" ] && open -b com.cmuxterm.app 2>/dev/null
    ;;
  orca:*)    orca terminal switch --terminal "${PANE#orca:}" 2>/dev/null ;;
  tmux:*)    tmux select-pane -t "${PANE#tmux:}" 2>/dev/null ;;
  wezterm:*) wezterm cli activate-pane --pane-id "${PANE#wezterm:}" 2>/dev/null ;;
  kitty:*)   kitten @ focus-window --match "id:${PANE#kitty:}" 2>/dev/null ;;
esac
exit 0
