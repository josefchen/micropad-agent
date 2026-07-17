#!/usr/bin/env python3
"""Generate the Karabiner rules for the Micro Pad from one readable table.

The pad sends nothing but meaningless signals; every real shortcut is decided here,
by whichever app happens to be frontmost. Edit MAPPINGS and re-run — hand-editing
60+ Karabiner manipulators is how mistakes get in.

  ./run.sh gen_karabiner.py > ~/Projects/input-setup/creator-micro-routing.json
"""
import json

# ---------------------------------------------------------------- signals
#
# 16 matrix positions + 2 encoders = 18 signals needed, but F13-F24 is only 12.
# So the 12 LED keys take the plain F-keys, and the knobs take Hyper+F13.. — a
# combination nothing else on the system can produce by accident.
#
#          (knob L)  [K01][K02]  (knob R)      <- no LEDs on the 4 corners
#          [K03][K04][K05][K06]
#          [K07][K08][K09][K10]
#          (key)[K11][K12](key)                <- bottom corners: firmware-local
#
KEYS = {
    "K01": "f13", "K02": "f14",
    "K03": "f15", "K04": "f16", "K05": "f17", "K06": "f18",
    "K07": "f19", "K08": "f20", "K09": "f21", "K10": "f22",
    "K11": "f23", "K12": "f24",
}
HYPER = ["left_command", "left_option", "left_control", "left_shift"]
KNOBS = {
    "knobL_ccw": "f13", "knobL_cw": "f14", "knobL_push": "f15",
    "knobR_ccw": "f16", "knobR_cw": "f17", "knobR_push": "f18",
}

APPS = {
    "figma":      r"^com\.figma\.Desktop$",
    "slack":      r"^com\.tinyspeck\.slackmacgap$",
    "ghostty":    r"^com\.mitchellh\.ghostty$",
    "jira":       r"^com\.google\.Chrome\.app\.JIRA_PWA_ID$",
    "confluence": r"^com\.google\.Chrome\.app\.CONFLUENCE_PWA_ID$",
}


def k(key, *mods):
    return {"key_code": key, "modifiers": list(mods)} if mods else {"key_code": key}


# ---------------------------------------------------------------- the table
#
# What each physical key does, per app. Anything left out simply doesn't fire there.
MAPPINGS = {
    # Terminal = the agent layer. This is where Claude Code and Codex actually live.
    "ghostty": {
        "K01": k("y"),                      # accept
        "K02": k("escape"),                 # reject / interrupt
        "K03": k("1", "left_command"),      # cmux session 1
        "K04": k("2", "left_command"),
        "K05": k("3", "left_command"),
        "K06": k("4", "left_command"),
        "K07": k("d", "left_command"),      # split
        "K08": k("t", "left_command"),      # new tab
        "K09": k("w", "left_command"),      # close
        "K10": k("f", "left_command"),      # search scrollback
        "K11": k("c", "left_control"),      # interrupt
        "K12": k("l", "left_command"),      # clear
    },
    "figma": {
        "K01": k("f"),                                  # frame
        "K02": k("a", "left_shift"),                    # auto layout
        "K03": k("k", "left_command", "left_option"),   # create component
        "K04": k("1", "left_shift"),                    # zoom to fit
        "K05": k("p"),                                  # pen
        "K06": k("t"),                                  # text
        "K07": k("g", "left_command"),                  # group
        "K08": k("d", "left_command"),                  # duplicate
        "K09": k("m", "left_control", "left_command"),  # mask
        "K10": k("e", "left_shift", "left_command"),    # export
        "K11": k("z", "left_command"),                  # undo
        "K12": k("backslash", "left_command"),          # toggle UI
    },
    "slack": {
        "K01": k("k", "left_command"),                  # quick switcher
        "K02": k("k", "left_command", "left_shift"),    # DMs
        "K03": k("up_arrow", "left_command"),           # edit last
        "K04": k("f", "left_command"),                  # search
    },
    "jira": {
        "K01": k("c"),          # create issue
        "K02": k("period"),     # command palette
        "K03": k("i"),          # assign to me
        "K04": k("slash"),      # search
    },
    "confluence": {
        "K01": k("e"),                              # edit
        "K02": k("slash"),                          # slash menu
        "K03": k("return_or_enter", "left_command"),  # publish
        "K04": k("f", "left_command"),              # find
    },
}

# Knobs. Left knob rides Codex Micro's idea: a dial for reasoning effort.
KNOB_MAPPINGS = {
    "figma": {
        "knobL_ccw": k("hyphen", "left_command"),      # zoom out
        "knobL_cw": k("equal_sign", "left_command"),   # zoom in
        "knobL_push": k("1", "left_shift"),            # zoom to fit
    },
}
# Fallbacks used when the frontmost app has no knob mapping of its own.
KNOB_DEFAULT = {
    "knobL_ccw": k("up_arrow"),
    "knobL_cw": k("down_arrow"),
    "knobR_ccw": k("volume_decrement"),
    "knobR_cw": k("volume_increment"),
    "knobR_push": k("mute"),
}


def build():
    rules = [{
        "description": "Caps Lock → Hyper (⌘⌥⌃⇧), tap = Escape",
        "manipulators": [{
            "type": "basic",
            "from": {"key_code": "caps_lock", "modifiers": {"optional": ["any"]}},
            "to": [{"key_code": "left_shift",
                    "modifiers": ["left_command", "left_control", "left_option"]}],
            "to_if_alone": [{"key_code": "escape"}],
        }],
    }]

    # One rule per physical key, so a key can be enabled/disabled on its own in the UI.
    for name, sig in KEYS.items():
        man = []
        for app, bundle in APPS.items():
            action = MAPPINGS.get(app, {}).get(name)
            if action:
                man.append({
                    "type": "basic",
                    "from": {"key_code": sig},
                    "to": [action],
                    "conditions": [{"type": "frontmost_application_if",
                                    "bundle_identifiers": [bundle]}],
                })
        if man:
            rules.append({"description": f"Micro Pad {name} ({sig.upper()}) — app routed",
                          "manipulators": man})

    # Knobs: app-specific first, then the catch-all. Karabiner takes the first match,
    # so ordering here is what makes the fallback a fallback.
    man = []
    for name, sig in KNOBS.items():
        for app, bundle in APPS.items():
            action = KNOB_MAPPINGS.get(app, {}).get(name)
            if action:
                man.append({
                    "type": "basic",
                    "from": {"key_code": sig, "modifiers": {"mandatory": HYPER}},
                    "to": [action],
                    "conditions": [{"type": "frontmost_application_if",
                                    "bundle_identifiers": [bundle]}],
                })
        default = KNOB_DEFAULT.get(name)
        if default:
            man.append({
                "type": "basic",
                "from": {"key_code": sig, "modifiers": {"mandatory": HYPER}},
                "to": [default],
            })
    rules.append({"description": "Micro Pad knobs (Hyper+F13..F18) — app routed",
                  "manipulators": man})

    return {"title": "Micro Pad — Hyper + App Routing", "rules": rules}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
