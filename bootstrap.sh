#!/bin/bash
# bootstrap.sh — stand up micropad-agent on THIS machine, wherever it lives.
#
# The Micro Pad is a single physical device that roams between machines. The
# daemon idles harmlessly when the pad is unplugged and auto-reconnects when it
# reappears, so the fix is simply: install this stack once on every machine you
# carry the pad to. Only the machine the pad is currently plugged into lights up.
#
# All paths are derived from where this script lives ($HOME, arch, repo dir), so
# it works under any username on Apple Silicon or Intel. Safe to re-run.
#
#   ./bootstrap.sh              # install / refresh (stock firmware — no flashing)
#   ./bootstrap.sh --per-key    # Tier B: per-key LEDs (needs the custom `agent` firmware flashed)
#   ./bootstrap.sh --uninstall  # tear down (daemon + Claude hooks)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="dev.micropad.agent"
PORT=48801
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CODEX_CFG="$HOME/.codex/config.toml"
NOTIFY_SH="$HERE/hooks/codex_notify.sh"

log()  { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# --- flags ------------------------------------------------------------------
# Default renderer is the stock-firmware daemon (no flashing). --per-key opts
# into the Tier B per-key renderer, which REQUIRES the custom `agent` firmware.
DAEMON="daemon.py"
UNINSTALL=""
for arg in "$@"; do
  case "$arg" in
    --per-key)   DAEMON="daemon_perkey.py" ;;
    --uninstall) UNINSTALL=1 ;;
    *) die "unknown flag: $arg (use --per-key or --uninstall)" ;;
  esac
done

# ---- uninstall -------------------------------------------------------------
if [ -n "$UNINSTALL" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true   # NB: pkill won't kill it (KeepAlive)
  rm -f "$PLIST" && log "daemon removed"
  [ -x "$HERE/run.sh" ] && "$HERE/run.sh" install_hooks.py --uninstall || true
  warn "Codex notify in $CODEX_CFG left untouched — remove the notify line by hand if you want."
  log "done."
  exit 0
fi

# ---- 1. Homebrew + hidapi --------------------------------------------------
command -v brew >/dev/null || die "Homebrew not found. Install it first: https://brew.sh"
BREW_PREFIX="$(brew --prefix)"
if brew list hidapi >/dev/null 2>&1; then
  log "hidapi already installed"
else
  log "installing hidapi…"; brew install hidapi
fi

# ---- 2. python venv + hid --------------------------------------------------
if [ ! -x "$HERE/.venv/bin/python" ]; then
  log "creating venv…"; python3 -m venv "$HERE/.venv"
fi
"$HERE/.venv/bin/pip" install --quiet --upgrade pip hid
log "python deps ready"

# ---- 3. launchd daemon (paths + DYLD baked in for THIS machine) ------------
log "renderer: $DAEMON$([ "$DAEMON" = daemon_perkey.py ] && echo '  (Tier B — needs the custom `agent` firmware flashed)')"
mkdir -p "$HOME/Library/LaunchAgents"
launchctl unload "$PLIST" 2>/dev/null || true
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$HERE/run.sh</string>
        <string>$DAEMON</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$HERE</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>DYLD_LIBRARY_PATH</key>
        <string>$BREW_PREFIX/lib</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/tmp/micropad-agent.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/micropad-agent.err</string>
</dict>
</plist>
EOF
launchctl load "$PLIST"
log "daemon loaded ($PLIST)"

# ---- 4. Claude Code hooks --------------------------------------------------
mkdir -p "$HOME/.claude"
[ -f "$HOME/.claude/settings.json" ] || echo '{}' > "$HOME/.claude/settings.json"
"$HERE/run.sh" install_hooks.py

# ---- 5. Codex CLI notify (careful: never clobber an existing notify) -------
mkdir -p "$HOME/.codex"
touch "$CODEX_CFG"
chmod +x "$NOTIFY_SH"
if grep -q 'micropad-agent/hooks/codex_notify.sh' "$CODEX_CFG"; then
  log "codex notify already wired"
elif grep -qE '^[[:space:]]*notify[[:space:]]*=' "$CODEX_CFG"; then
  warn "an existing 'notify' is set in $CODEX_CFG — leaving it as-is."
  warn "to add blue LEDs, chain this into that notify line manually:"
  warn "  $NOTIFY_SH"
else
  printf '\nnotify = ["%s"]\n' "$NOTIFY_SH" >> "$CODEX_CFG"
  log "codex notify wired"
fi
warn "reminder: Codex lights up only from the CLI. The ChatGPT app can't fire notify."

# ---- 6. health check -------------------------------------------------------
for _ in 1 2 3; do
  if curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    log "daemon healthy on 127.0.0.1:$PORT"
    echo
    log "Done. Plug in the Micro Pad and drive Claude Code / Codex CLI to see it light up."
    log "Manage: launchctl unload/load $PLIST   (never pkill — KeepAlive restarts it)"
    exit 0
  fi
  sleep 1
done
die "daemon not answering on :$PORT — check /tmp/micropad-agent.err"
