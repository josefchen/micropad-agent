#!/bin/sh
# Codex notify hook: report turn-end to the Micro Pad daemon.
#
# NOTE: this used to call SkyComputerUseClient itself, but Codex Computer Use
# rewrites config.toml on startup to put itself first and chain us behind its
# own `--previous-notify` flag:
#
#   notify = [SkyComputerUseClient, "turn-ended", "--previous-notify", "[this script]"]
#
# So Computer Use now invokes us. Calling it back from here would fire a second,
# duplicate notification — hence this script only reports and exits.

echo "$(date '+%H:%M:%S') notify fired: $*" >> /tmp/micropad-codex.log

# Pull a stable thread/conversation id out of the notify JSON so the per-key
# daemon (v2) gives Codex its own slot. BSD sed has no alternation, so try each
# field name in turn; fall back to a constant so Codex always gets one slot.
ARG="$*"
field() { printf '%s' "$ARG" | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1; }
SID="$(field thread_id)"
[ -z "$SID" ] && SID="$(field thread-id)"
[ -z "$SID" ] && SID="$(field conversation_id)"
[ -z "$SID" ] && SID="$(field session_id)"
[ -z "$SID" ] && SID="codex"

curl -s -m 1 -X POST http://127.0.0.1:48801/state \
  -H 'Content-Type: application/json' \
  -d "{\"state\":\"success\",\"source\":\"codex\",\"session\":\"$SID\"}" >/dev/null 2>&1 || true

exit 0
