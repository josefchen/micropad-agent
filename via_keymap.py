#!/usr/bin/env python3
"""Read and write the Micro Pad's keymap over the VIA protocol.

usevia.app does this by hand; this does the same thing over the same raw-HID
protocol, but from a table we can review and re-run.

  ./run.sh via_keymap.py --backup            # dump current keymap to JSON
  ./run.sh via_keymap.py --show              # print current layer 0
  ./run.sh via_keymap.py --apply             # write our signal map
  ./run.sh via_keymap.py --restore FILE      # put a backup back

Stop the LED daemon first — two processes interleaving 32-byte reports on the
same interface will scramble each other's replies:
  launchctl unload ~/Library/LaunchAgents/dev.micropad.agent.plist
"""
import json
import sys
import time
from pathlib import Path

import hid

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

GET_PROTOCOL_VERSION = 0x01
DK_GET_KEYCODE, DK_SET_KEYCODE = 0x04, 0x05
DK_GET_LAYER_COUNT = 0x11
DK_GET_ENCODER, DK_SET_ENCODER = 0x14, 0x15

ROWS, COLS = 4, 4
ENCODERS = 2

# QMK keycodes
KC_F13 = 0x68                       # F13..F24 are contiguous from here
HYPER = 0x0F00                      # LCTL|LSFT|LALT|LGUI applied to a keycode
KC_NO, KC_TRNS = 0x0000, 0x0001


def F(n):
    """F13 -> 0x68, F14 -> 0x69, ..."""
    assert 13 <= n <= 24, n
    return KC_F13 + (n - 13)


# ---------------------------------------------------------------- the map
#
#        (knobL)  [K01][K02]  (knobR)
#        [K03][K04][K05][K06]
#        [K07][K08][K09][K10]
#        (corner)[K11][K12](corner)
#
# 12 LED keys take the plain F-keys; the knobs take Hyper+F13.. because F13-F24
# alone isn't enough signals for 16 keys + 2 encoders, and Hyper+F-key is a
# combination no human hand can produce by accident.
LAYER0 = {
    (0, 1): F(13),  (0, 2): F(14),                                   # K01 K02
    (1, 0): F(15),  (1, 1): F(16),  (1, 2): F(17),  (1, 3): F(18),   # K03..K06
    (2, 0): F(19),  (2, 1): F(20),  (2, 2): F(21),  (2, 3): F(22),   # K07..K10
    (3, 1): F(23),  (3, 2): F(24),                                   # K11 K12
    (0, 0): HYPER | F(15),   # knob L push
    (0, 3): HYPER | F(18),   # knob R push
    # (3,0) and (3,3) are left exactly as the firmware shipped them — those are the
    # layer and LED-brightness keys, and they're more useful as-is.
}
ENCODER_MAP = {
    (0, 0): HYPER | F(13),   # knob L  ccw
    (0, 1): HYPER | F(14),   # knob L  cw
    (1, 0): HYPER | F(16),   # knob R  ccw
    (1, 1): HYPER | F(17),   # knob R  cw
}


class Via:
    def __init__(self):
        path = None
        for d in hid.enumerate(VID, PID):
            if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
                path = d["path"]
                break
        if not path:
            raise SystemExit("❌ Micro Pad raw HID not found")
        self.dev = hid.Device(path=path)

    def xfer(self, payload):
        self.dev.write(b"\x00" + bytes(payload) + b"\x00" * (32 - len(payload)))
        try:
            return bytes(self.dev.read(32, 500))
        except Exception:
            return None

    def protocol(self):
        r = self.xfer([GET_PROTOCOL_VERSION])
        return (r[1] << 8) | r[2] if r else None

    def layer_count(self):
        r = self.xfer([DK_GET_LAYER_COUNT])
        return r[1] if r else 0

    def get_key(self, layer, row, col):
        r = self.xfer([DK_GET_KEYCODE, layer, row, col])
        return (r[4] << 8) | r[5] if r else None

    def set_key(self, layer, row, col, kc):
        self.xfer([DK_SET_KEYCODE, layer, row, col, (kc >> 8) & 0xFF, kc & 0xFF])

    def get_enc(self, layer, idx, cw):
        r = self.xfer([DK_GET_ENCODER, layer, idx, cw])
        return (r[4] << 8) | r[5] if r else None

    def set_enc(self, layer, idx, cw, kc):
        self.xfer([DK_SET_ENCODER, layer, idx, cw, (kc >> 8) & 0xFF, kc & 0xFF])


def name(kc):
    if kc is None:
        return "?"
    base, mods = kc & 0xFF, kc & 0xFF00
    label = f"F{13 + base - KC_F13}" if KC_F13 <= base <= KC_F13 + 11 else f"0x{base:02X}"
    if mods == HYPER:
        return f"Hyper+{label}"
    if mods:
        return f"0x{kc:04X}"
    if kc == KC_NO:
        return "----"
    if kc == KC_TRNS:
        return "trns"
    return label


def dump(via, layers):
    snap = {"layers": [], "encoders": []}
    for L in range(layers):
        snap["layers"].append([[via.get_key(L, r, c) for c in range(COLS)] for r in range(ROWS)])
        snap["encoders"].append([[via.get_enc(L, i, d) for d in (0, 1)] for i in range(ENCODERS)])
    return snap


def show(via):
    print(f"VIA protocol {via.protocol()} · layers {via.layer_count()}\n")
    print("layer 0 keymap:")
    for r in range(ROWS):
        print("   " + "  ".join(f"{name(via.get_key(0, r, c)):>10}" for c in range(COLS)))
    print("\nlayer 0 encoders:")
    for i in range(ENCODERS):
        print(f"   enc{i}  ccw={name(via.get_enc(0, i, 0)):>10}   cw={name(via.get_enc(0, i, 1)):>10}")


def main():
    args = sys.argv[1:]
    via = Via()

    if "--show" in args:
        show(via)
        return 0

    if "--backup" in args:
        layers = via.layer_count()
        snap = dump(via, layers)
        out = Path(__file__).parent / f"keymap-backup-{int(time.time())}.json"
        out.write_text(json.dumps(snap, indent=2))
        print(f"✅ {layers} layers backed up -> {out}")
        return 0

    if "--restore" in args:
        src = Path(args[args.index("--restore") + 1])
        snap = json.loads(src.read_text())
        for L, grid in enumerate(snap["layers"]):
            for r, row in enumerate(grid):
                for c, kc in enumerate(row):
                    if kc is not None:
                        via.set_key(L, r, c, kc)
        for L, encs in enumerate(snap["encoders"]):
            for i, dirs in enumerate(encs):
                for d, kc in enumerate(dirs):
                    if kc is not None:
                        via.set_enc(L, i, d, kc)
        print(f"✅ restored from {src}")
        return 0

    if "--apply" in args:
        print("writing layer 0...")
        for (r, c), kc in LAYER0.items():
            via.set_key(0, r, c, kc)
            print(f"   [{r},{c}] -> {name(kc)}")
        for (i, d), kc in ENCODER_MAP.items():
            via.set_enc(0, i, d, kc)
            print(f"   enc{i} {'cw' if d else 'ccw'} -> {name(kc)}")
        print("\nreading back:\n")
        show(via)
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
