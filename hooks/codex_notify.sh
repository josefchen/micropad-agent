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

curl -s -m 1 -X POST http://127.0.0.1:48801/state \
  -H 'Content-Type: application/json' \
  -d '{"state":"success","source":"codex"}' >/dev/null 2>&1 || true

exit 0
