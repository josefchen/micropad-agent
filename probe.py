#!/usr/bin/env python3
"""Probe the Work Louder Micro Pad's VIA lighting protocol over raw HID.

Answers three questions:
  1. Can we open the raw HID interface at all?
  2. Does the firmware answer the VIA protocol handshake?
  3. Which lighting channel (rgb_matrix / rgblight) actually responds?

Run:  ./run.sh probe.py
"""
import sys
import time

import hid

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

# VIA command ids
GET_PROTOCOL_VERSION = 0x01
CUSTOM_SET_VALUE = 0x07
CUSTOM_GET_VALUE = 0x08
CUSTOM_SAVE = 0x09

# VIA channel ids
CH_BACKLIGHT, CH_RGBLIGHT, CH_RGB_MATRIX = 0x01, 0x02, 0x03

# rgb_matrix / rgblight value ids (same numbering)
V_BRIGHTNESS, V_EFFECT, V_SPEED, V_COLOR = 0x01, 0x02, 0x03, 0x04

# QMK hue is 0-255
HUE_ORANGE, HUE_BLUE = 12, 170


def find_raw_path():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
            return d["path"], d
    return None, None


def xfer(dev, payload, read=True):
    """Send a 32-byte VIA packet (with macOS report-id prefix) and read the reply."""
    buf = bytes(payload) + b"\x00" * (32 - len(payload))
    dev.write(b"\x00" + buf)
    if not read:
        return None
    try:
        return bytes(dev.read(32, 500))
    except Exception:
        return None


def main():
    path, info = find_raw_path()
    if not path:
        print("❌ raw HID interface (0xFF60/0x61) not found — is the pad plugged in?")
        return 1

    print(f"✅ raw HID found: {info['manufacturer_string']} {info['product_string']}")
    print(f"   path={path.decode(errors='replace')}\n")

    dev = hid.Device(path=path)

    # 1. protocol handshake
    r = xfer(dev, [GET_PROTOCOL_VERSION])
    if not r or r[0] != GET_PROTOCOL_VERSION:
        print("❌ no VIA handshake reply — firmware may not speak VIA raw protocol")
        dev.close()
        return 1
    version = (r[1] << 8) | r[2]
    print(f"✅ VIA protocol version: {version}\n")

    # 2. which lighting channel answers?
    print("--- probing lighting channels ---")
    live = []
    for name, ch in (("rgb_matrix", CH_RGB_MATRIX), ("rgblight", CH_RGBLIGHT),
                     ("backlight", CH_BACKLIGHT)):
        vals = {}
        for vname, vid in (("brightness", V_BRIGHTNESS), ("effect", V_EFFECT),
                           ("speed", V_SPEED), ("color(h,s)", V_COLOR)):
            r = xfer(dev, [CUSTOM_GET_VALUE, ch, vid])
            # reply echoes: [cmd, channel, value_id, data...]
            if r and r[0] == CUSTOM_GET_VALUE and r[1] == ch and r[2] == vid:
                vals[vname] = (r[3], r[4]) if vid == V_COLOR else r[3]
        if vals:
            live.append((name, ch))
            print(f"  ✅ {name:<11} (ch {ch}) -> {vals}")
        else:
            print(f"  ·  {name:<11} (ch {ch}) -> no reply")

    if not live:
        print("\n❌ no lighting channel responded. Tier A via VIA protocol is out;")
        print("   we'd need custom QMK firmware (Tier B) to drive the LEDs.")
        dev.close()
        return 1

    ch_name, ch = live[0]
    print(f"\n→ using '{ch_name}' (channel {ch}) for the colour test\n")

    # 3. visual test — flip between Claude orange and Codex blue
    print("--- visual test: watch the pad ---")
    saved_effect = xfer(dev, [CUSTOM_GET_VALUE, ch, V_EFFECT])
    saved_color = xfer(dev, [CUSTOM_GET_VALUE, ch, V_COLOR])
    saved_bright = xfer(dev, [CUSTOM_GET_VALUE, ch, V_BRIGHTNESS])

    xfer(dev, [CUSTOM_SET_VALUE, ch, V_EFFECT, 1], read=False)  # 1 = solid colour
    xfer(dev, [CUSTOM_SET_VALUE, ch, V_BRIGHTNESS, 150], read=False)

    for label, hue in (("🟠 Claude orange", HUE_ORANGE), ("🔵 Codex blue", HUE_BLUE),
                       ("🟠 Claude orange", HUE_ORANGE), ("🔵 Codex blue", HUE_BLUE)):
        print(f"   {label}  (hue={hue})")
        xfer(dev, [CUSTOM_SET_VALUE, ch, V_COLOR, hue, 255], read=False)
        time.sleep(1.2)

    # restore what was there before
    if saved_effect:
        xfer(dev, [CUSTOM_SET_VALUE, ch, V_EFFECT, saved_effect[3]], read=False)
    if saved_color:
        xfer(dev, [CUSTOM_SET_VALUE, ch, V_COLOR, saved_color[3], saved_color[4]], read=False)
    if saved_bright:
        xfer(dev, [CUSTOM_SET_VALUE, ch, V_BRIGHTNESS, saved_bright[3]], read=False)
    print("\n   (restored previous lighting — nothing saved to EEPROM)")

    dev.close()
    print("\n✅ Tier A is viable: the host can drive pad colour over raw HID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
