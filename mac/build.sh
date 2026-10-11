#!/bin/sh
# Build Micropad.app (menu bar + wheel switcher HUD) into ~/Applications and
# (re)start its LaunchAgent so it runs at login and restarts if it dies.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Micropad.app"
LABEL=dev.micropad.menubar
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$APP/Contents/MacOS" "$HOME/Library/Logs/micropad"
swiftc -O -swift-version 5 -target arm64-apple-macos14 \
  -o "$APP/Contents/MacOS/Micropad" "$HERE/main.swift"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
codesign --force --sign - "$APP" >/dev/null

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/Contents/MacOS/Micropad</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/micropad/menubar.err</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
i=0
while launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "built $APP and started $LABEL"
