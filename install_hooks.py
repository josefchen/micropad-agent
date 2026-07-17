#!/usr/bin/env python3
"""Add (or remove) the micropad-agent hooks in ~/.claude/settings.json.

Mirrors the agentkat-bridge pattern: one fire-and-forget curl per event, tagged
`# micropad-agent` so it can be found and removed again. Existing hooks — the
agentkat-bridge and orca ones — are never touched.

  ./run.sh install_hooks.py             # install / refresh
  ./run.sh install_hooks.py --uninstall # remove
  ./run.sh install_hooks.py --dry-run   # show what would change
"""
import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
TAG = "# micropad-agent"
PORT = 48801

# event -> (state, matcher). Matchers follow the convention already in the file.
EVENTS = {
    "UserPromptSubmit":  ("thinking", None),
    "PreToolUse":        ("coding",   "*"),
    "Stop":              ("success",  None),
    "Notification":      ("waiting",  None),
    "PermissionRequest": ("waiting",  "*"),    # richer than agentkat: exact "needs you"
    "StopFailure":       ("error",    None),   # richer than agentkat: real errors
    "SessionEnd":        ("idle",     None),
}


def command_for(state):
    payload = json.dumps({"state": state, "source": "claude-code"}, separators=(",", ":"))
    return (
        f"curl -s -m 1 -X POST http://127.0.0.1:{PORT}/state "
        f"-H 'Content-Type: application/json' "
        f"-d '{payload}' >/dev/null 2>&1 || true  {TAG}"
    )


def is_ours(group):
    return any(TAG in h.get("command", "") for h in group.get("hooks", []))


def main():
    uninstall = "--uninstall" in sys.argv
    dry = "--dry-run" in sys.argv

    settings = json.loads(SETTINGS.read_text())
    hooks = settings.setdefault("hooks", {})

    # Always strip our old entries first, so installing twice is a no-op.
    removed = 0
    for ev in list(hooks):
        before = len(hooks[ev])
        hooks[ev] = [g for g in hooks[ev] if not is_ours(g)]
        removed += before - len(hooks[ev])
        if not hooks[ev]:
            del hooks[ev]

    added = 0
    if not uninstall:
        for ev, (state, matcher) in EVENTS.items():
            group = {"hooks": [{"type": "command", "command": command_for(state)}]}
            if matcher:
                group["matcher"] = matcher
            hooks.setdefault(ev, []).append(group)
            added += 1

    action = "uninstall" if uninstall else "install"
    print(f"{action}: -{removed} old, +{added} new")
    for ev, (state, _) in ([] if uninstall else EVENTS.items()):
        print(f"   {ev:20} -> {state}")

    if dry:
        print("\n(dry run — nothing written)")
        return 0

    backup = SETTINGS.with_suffix(f".json.bak-{int(time.time())}")
    shutil.copy2(SETTINGS, backup)
    SETTINGS.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    print(f"\n✅ wrote {SETTINGS}")
    print(f"   backup: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
