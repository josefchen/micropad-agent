#!/usr/bin/env python3
"""Compare error-signal candidates against the amber reference.

Red and amber wash out into each other through the pad's diffusers, so the error
state needs a signal that doesn't rely on hue distance alone. Each candidate is
shown right after 3s of solid amber so the contrast is judged directly.

Run with the daemon stopped:  pkill -f daemon.py && ./run.sh tools/errortest.py
"""
import sys
import time

import hid

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61
CUSTOM_SET_VALUE = 0x07
CH_RGB_MATRIX, CH_RGBLIGHT = 0x03, 0x02
V_BRIGHTNESS, V_EFFECT, V_COLOR = 0x01, 0x02, 0x04
MAX_BRIGHT, EFFECT_SOLID = 150, 1

HUE_AMBER, HUE_RED, HUE_MAGENTA = 28, 0, 213


def open_pad():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE:
            return hid.Device(path=d["path"])
    raise SystemExit("❌ Micro Pad raw HID not found")


class Pad:
    def __init__(self):
        self.dev = open_pad()
        for ch in (CH_RGB_MATRIX, CH_RGBLIGHT):
            self._send([CUSTOM_SET_VALUE, ch, V_EFFECT, EFFECT_SOLID])

    def _send(self, p):
        self.dev.write(b"\x00" + bytes(p) + b"\x00" * (32 - len(p)))

    def show(self, hue, sat, bright):
        for ch in (CH_RGB_MATRIX, CH_RGBLIGHT):
            self._send([CUSTOM_SET_VALUE, ch, V_COLOR, hue, sat])
            self._send([CUSTOM_SET_VALUE, ch, V_BRIGHTNESS, bright])


def burst(pad, hue, sat, secs):
    """3 rapid flashes, then a pause."""
    end = time.time() + secs
    t0 = time.time()
    while time.time() < end:
        t = time.time() - t0
        cycle = t % 1.2
        on = cycle < 0.6 and int(cycle * 10) % 2 == 0
        pad.show(hue, sat, MAX_BRIGHT if on else 0)
        time.sleep(0.02)


def two_tone(pad, secs):
    """Alternating red / white strobe — reads like an emergency light."""
    end = time.time() + secs
    t0 = time.time()
    while time.time() < end:
        t = time.time() - t0
        pad.show(HUE_RED, 255, MAX_BRIGHT) if int(t * 6) % 2 else pad.show(0, 0, MAX_BRIGHT)
        time.sleep(0.02)


CANDIDATES = [
    ("1. 빨강 버스트 (지금 설정)", "빨강 3연발 — 앰버랑 헷갈리는 그 후보",
     lambda p, s: burst(p, HUE_RED, 255, s)),
    ("2. 빨강⇄하양 스트로브", "소방차 경광등. 하양이 섞여서 앰버와 절대 안 겹침",
     two_tone),
    ("3. 마젠타 버스트", "hue 213 — 앰버(28)에서도 파랑(170)에서도 먼 색",
     lambda p, s: burst(p, HUE_MAGENTA, 255, s)),
    ("4. 하양 버스트", "채도 0. 색상환 밖이라 어떤 색과도 안 겹침",
     lambda p, s: burst(p, 0, 0, s)),
    ("5. 앰버 버스트 (색 포기)", "에러도 에이전트 색 유지 — 대신 '누가' 났는지 알 수 있음",
     lambda p, s: burst(p, HUE_AMBER, 255, s)),
]


def main():
    pad = Pad()
    print("패드 봐줘. 각 후보 앞에 앰버 기준색을 3초 보여줄게.\n")
    time.sleep(2)

    for title, note, fn in CANDIDATES:
        print(f"\n{'─' * 50}")
        print(f"  🟠 앰버 기준색 (Claude 완료) ... 3초")
        pad.show(HUE_AMBER, 255, MAX_BRIGHT)
        time.sleep(3)
        print(f"  ▶ {title}")
        print(f"    {note} ... 6초")
        fn(pad, 6)

    pad.show(HUE_AMBER, 255, 6)
    print(f"\n{'─' * 50}\n끝. 몇 번이 제일 확실했어?")
    pad.dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
