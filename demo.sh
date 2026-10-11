#!/bin/sh
# Stage the pad + menu bar app for photos and screen recordings.
#   ./demo.sh showcase   mixed: working, needs-you, done across Claude / Codex / Cursor
#   ./demo.sh working    everything breathing
#   ./demo.sh needs      one session waiting on you (key + white LEDs blink)
#   ./demo.sh done       a session just finished (green underglow, white LEDs flash 3x)
#   ./demo.sh off        back to your real sessions
# Re-run a scene to replay its transitions (e.g. the done flash).
# Holding a demo key opens its app; to open a specific staged session instead, put
# {"claude": "local_…", "codex": "<thread id>", "cursor": "<composer id>"} in
# ~/.config/micropad/demo.json.
set -eu
curl -s -X POST -H "X-Micropad: 1" -d "{\"scene\": \"${1:-showcase}\"}" http://127.0.0.1:48801/demo
echo
