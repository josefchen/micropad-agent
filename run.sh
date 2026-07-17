#!/bin/bash
# hidapi lives in Homebrew's lib dir; the `hid` python package must find it at
# process start, so we set it here rather than inside the script. Probe both
# Homebrew prefixes so this works on Apple Silicon (/opt/homebrew) and Intel
# (/usr/local) without needing `brew` on PATH — launchd runs with a bare PATH.
cd "$(dirname "$0")"
for p in /opt/homebrew/lib /usr/local/lib; do
  [ -d "$p" ] && export DYLD_LIBRARY_PATH="$p:${DYLD_LIBRARY_PATH:-}"
done
exec ./.venv/bin/python "$@"
