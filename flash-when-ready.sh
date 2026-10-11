#!/bin/bash
# Flash the per-key firmware and bring everything back.
# Asks the running firmware to jump to DFU (works once this firmware is on the
# pad); otherwise waits for a manual bootloader entry (hold the top-left wheel
# while plugging in). Then restores the VIA keymap and restarts the daemon.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
HEX="$REPO/firmware/work_louder_micro/agent/work_louder_micro_agent.hex"
LABEL="gui/$(id -u)/dev.micropad.agent"
LOG=/tmp/micropad-flash.log
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date) flash ==="

# The daemon holds the pad's HID interface exclusively; pause it for the backup.
launchctl bootout "$LABEL" 2>/dev/null || true
sleep 1
FRESH=$(cd "$REPO" && ./run.sh via_keymap.py --backup 2>/dev/null | sed -n 's/.*-> //p' || true)
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/dev.micropad.agent.plist" 2>/dev/null \
  || launchctl kickstart -k "$LABEL"
for _ in $(seq 1 30); do
  curl -s -m 1 http://127.0.0.1:48801/health | grep -q '"pad": true' && break
  sleep 0.5
done
if [ -n "$FRESH" ] && [ -f "$FRESH" ]; then
  BACKUP="$FRESH"
else
  BACKUP=$(ls -t "$REPO"/keymap-backup-*.json 2>/dev/null | head -1 || true)
fi
echo "keymap backup: ${BACKUP:-none}"

curl -s -m 2 -X POST -H "X-Micropad: 1" http://127.0.0.1:48801/bootloader >/dev/null && echo "asked pad to enter DFU" || true

found=0
for _ in $(seq 1 6000); do
  if dfu-programmer atmega32u4 get bootloader-version >/dev/null 2>&1; then
    found=1
    break
  fi
  sleep 0.3
done
if [ "$found" != 1 ]; then
  echo "no DFU device: unplug, hold the top-left wheel, plug back in, rerun"
  exit 1
fi

echo "DFU present, flashing"
launchctl bootout "$LABEL" 2>/dev/null || true
dfu-programmer atmega32u4 erase --force
dfu-programmer atmega32u4 flash --force "$HEX"
# reset can hang after the pad has already rebooted into the new firmware
dfu-programmer atmega32u4 reset & RESET_PID=$!
( sleep 5; kill "$RESET_PID" 2>/dev/null ) &
wait "$RESET_PID" 2>/dev/null || true

echo "waiting for Micro Pad to come back"
for _ in $(seq 1 40); do
  ioreg -p IOUSB -l -w 0 | grep -q "Micro Pad" && break
  sleep 0.5
done
sleep 1

if [ -f "$BACKUP" ]; then
  (cd "$REPO" && ./run.sh via_keymap.py --restore "$BACKUP") || echo "keymap restore failed"
fi

launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/dev.micropad.agent.plist" 2>/dev/null \
  || launchctl kickstart -k "$LABEL"
sleep 2
curl -s -m 2 http://127.0.0.1:48801/health || true
echo
echo "=== $(date) flash done ==="
