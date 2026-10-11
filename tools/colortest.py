#!/usr/bin/env python3
"""Slow, obvious colour test for the Micro Pad. Watch the pad.

Run:  ./run.sh tools/colortest.py
"""
import sys
import time

import hid

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61

CUSTOM_SET_VALUE, CUSTOM_GET_VALUE = 0x07, 0x08
CH_RGB_MATRIX, CH_RGBLIGHT = 0x03, 0x02
V_BRIGHTNESS, V_EFFECT, V_SPEED, V_COLOR = 0x01, 0x02, 0x03, 0x04

MAX_BRIGHT = 150  # keyboard.json: rgb_matrix.max_brightness
EFFECT_SOLID = 1


def open_pad():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
            return hid.Device(path=d["path"])
    raise SystemExit("❌ Micro Pad raw HID not found — is it plugged in?")


def send(dev, payload, read=False):
    dev.write(b"\x00" + bytes(payload) + b"\x00" * (32 - len(payload)))
    if read:
        try:
            return bytes(dev.read(32, 500))
        except Exception:
            return None


def set_all(dev, hue, sat=255, bright=MAX_BRIGHT, effect=EFFECT_SOLID):
    """Drive BOTH channels — whichever one the pad's LEDs actually follow will win."""
    for ch in (CH_RGB_MATRIX, CH_RGBLIGHT):
        send(dev, [CUSTOM_SET_VALUE, ch, V_EFFECT, effect])
        send(dev, [CUSTOM_SET_VALUE, ch, V_BRIGHTNESS, bright])
        send(dev, [CUSTOM_SET_VALUE, ch, V_COLOR, hue, sat])


def main():
    dev = open_pad()
    print("패드를 보고 있어줘. 3초 뒤 시작할게.\n")
    for i in (3, 2, 1):
        print(f"   {i}...", flush=True)
        time.sleep(1)

    steps = [
        ("⬜  하양  — 다 켜짐 (LED가 살아있는지부터 확인)", 0, 0),
        ("🟠  주황  — Claude Code 색", 12, 255),
        ("🔵  파랑  — Codex 색", 170, 255),
        ("🟠  주황", 12, 255),
        ("🔵  파랑", 170, 255),
        ("🔴  빨강  — 에러 상태 색", 0, 255),
    ]
    for label, hue, sat in steps:
        print(f"\n{label}")
        set_all(dev, hue, sat)
        time.sleep(3)

    print("\n--- 깜빡임 테스트 (입력 필요 상태) : 주황 2Hz ---")
    for _ in range(6):
        set_all(dev, 12, 255, MAX_BRIGHT)
        time.sleep(0.25)
        set_all(dev, 12, 255, 0)
        time.sleep(0.25)

    print("\n끝. LED는 주황으로 켜둘게 — 껐다 켰다 확실히 보이는지 알려줘.")
    set_all(dev, 12, 255, MAX_BRIGHT)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
