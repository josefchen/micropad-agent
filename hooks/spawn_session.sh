#!/bin/sh
# micropad-agent — an empty (off) agent slot was tapped: launch a new agent.
#
# Called by daemon_perkey.py when you HOLD the bottom-left touch and TAP a slot
# key that has no session assigned. Turns an empty status key into a launcher.
#
#   $1  slot   the LED slot index that was tapped (10,9,8 = Claude / 11,7,6 = Codex)
#
# TAILOR to how you start agents. Left cluster = Claude, right = Codex (matches
# CLAUDE_SLOTS / CODEX_SLOTS in daemon_perkey.py).
SLOT="$1"
echo "$(date '+%H:%M:%S') spawn request slot=$SLOT" >> /tmp/micropad-focus.log

# examples — uncomment / adapt one:
#   orca terminal create --command "claude" 2>/dev/null
#   tmux new-window "claude"                2>/dev/null
#   open -na WezTerm --args start -- claude 2>/dev/null
exit 0
