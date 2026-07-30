#!/usr/bin/env python3
"""Write all four Micro Pad layers over the VIA protocol.

Layers toggle. The bottom-right key walks forward through them and wraps:

    L0 terminal / agents  ──┐
    L1 Codex app            │  bottom-right = next
    L2 Claude Code          │
    L3 Orca  ───────────────┘

Every shortcut here was read off the thing itself — Codex from its AXMenuItem menus,
Claude Code from the published keybindings docs, Orca from its own
KEYBINDING_DEFINITIONS table. None are guessed.

The LEDs are busy showing agent state, so nothing on the pad can tell you which
layer you're on. The bottom-left key is the answer to that: it is TO(0) on every
layer, so one press always lands you back on terminal no matter where you are.
Press it whenever you've lost count.

Physical grid (matrix positions):

    (knobL)[0,0]  [0,1][0,2]  (knobR)[0,3]
           [1,0]  [1,1][1,2]  [1,3]
           [2,0]  [2,1][2,2]  [2,3]
    HOME   [3,0]  [3,1][3,2]  [3,3]   NEXT

  ./run.sh layers.py --apply
  ./run.sh layers.py --preview     # print without writing
"""
import sys

import hid

VID, PID = 0x574C, 0xE6E3
RAW_USAGE_PAGE, RAW_USAGE = 0xFF60, 0x61
DK_SET_KEYCODE, DK_SET_ENCODER = 0x05, 0x15
DK_MACRO_SET = 0x0F   # id_dynamic_keymap_macro_set_buffer

# --- keycodes -----------------------------------------------------------------
CTL, SFT, ALT, GUI = 0x0100, 0x0200, 0x0400, 0x0800
TRNS = 0x0001
NONE = 0x0000
M0 = 0x7700   # QK_MACRO_0 — /resume (set in apply())
M1 = 0x7701   # QK_MACRO_1 — double-tap Left Control = macOS dictation toggle


def TO(n):
    """Switch to layer n and stay there."""
    return 0x5200 + n


K = {c: 0x04 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}
K.update({str(d): 0x1E + i for i, d in enumerate("1234567890")})
K.update({
    "enter": 0x28, "esc": 0x29, "bspc": 0x2A, "tab": 0x2B, "space": 0x2C,
    "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31,
    "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "pgup": 0x4B, "pgdn": 0x4E, "home": 0x4A, "end": 0x4D,
    "mute": 0xA8, "vol+": 0xA9, "vol-": 0xAA,
    "next": 0xAB, "prev": 0xAC, "play": 0xAE,
})

# --- layers -------------------------------------------------------------------
# Each entry: matrix position -> (keycode, label shown in --preview)

L0 = {  # CMUX — top 10 keys are Agent Keys (firmware: tap=focus / double=front).
        # Their values here are ignored on L0; only the non-status keys apply.
    (0, 0): (GUI | K["k"], "⌘K 클리어"),
    (0, 1): (K["y"], "(agent)"),
    (0, 2): (K["esc"], "(agent)"),
    (0, 3): (K["mute"], "뮤트"),
    (1, 0): (CTL | K["a"], "(agent)"),
    (1, 1): (CTL | K["e"], "(agent)"),
    (1, 2): (CTL | K["w"], "(agent)"),
    (1, 3): (CTL | K["l"], "(agent)"),
    (2, 0): (K["tab"], "(agent)"),
    (2, 1): (K["up"], "(agent)"),
    (2, 2): (K["down"], "(agent)"),
    (2, 3): (CTL | K["c"], "(agent)"),
    (3, 0): (TO(0), "홈 (여기임)"),
    (3, 1): (M1,       "🎤 음성"),      # green cap — fixed accept on every layer
    (3, 2): (K["esc"], "🔴 취소"),      # red cap   — fixed reject/cancel
    (3, 3): (TO(1), "다음 → Claude Code"),
}
E0 = {(0, 0): (GUI | K["["], "⌘[ 이전탭"), (0, 1): (GUI | K["]"], "⌘] 다음탭"),
      (1, 0): (K["pgup"], "스크롤↑"), (1, 1): (K["pgdn"], "스크롤↓")}

L1 = {  # Claude Code (CLI in cmux) — from code.claude.com/docs/en/keybindings, v2.1.220
    (0, 0): (GUI | K["k"], "⌘K 화면클리어"),
    (0, 1): (K["y"], "y 승인"),
    (0, 2): (K["n"], "n 거절"),
    (0, 3): (CTL | K["o"], "⌃O 트랜스크립트"),
    (1, 0): (SFT | K["tab"], "⇧⇥ 모드순환"),   # normal → auto-accept → plan
    (1, 1): (CTL | K["t"], "⌃T 할일목록"),
    (1, 2): (CTL | K["b"], "⌃B 백그라운드"),
    (1, 3): (CTL | K["r"], "⌃R 히스토리검색"),
    (2, 0): (ALT | K["p"], "⌥P 모델선택"),      # chat:modelPicker = Meta+P
    (2, 1): (ALT | K["t"], "⌥T 확장사고"),      # chat:thinkingToggle = Meta+T
    (2, 2): (ALT | K["o"], "⌥O 패스트모드"),     # chat:fastMode = Meta+O
    (2, 3): (K["esc"], "esc 인터럽트"),
    (3, 0): (TO(0), "홈 → CMUX"),
    (3, 1): (M1,       "🎤 음성"),
    (3, 2): (K["esc"], "🔴 취소"),
    (3, 3): (TO(2), "다음 → Codex CLI"),
}
E1 = {(0, 0): (K["up"], "↑ 히스토리"), (0, 1): (K["down"], "↓ 히스토리"),
      (1, 0): (K["pgup"], "스크롤↑"), (1, 1): (K["pgdn"], "스크롤↓")}

L2 = {  # Codex CLI (in cmux) — codex-cli 0.144.6, default keymap
    (0, 0): (NONE, "—"),                       # knob press (turn = mousewheel)
    (0, 1): (CTL | K["c"], "⌃C 취소"),
    (0, 2): (K["esc"], "esc 취소"),
    (0, 3): (NONE, "—"),                       # knob press (turn = volume)
    (1, 0): (CTL | K["r"], "⌃R 히스토리"),
    (1, 1): (CTL | K["l"], "⌃L 클리어"),
    (1, 2): (CTL | K["g"], "⌃G 에디터"),
    (1, 3): (CTL | K["o"], "⌃O 응답복사"),
    (2, 0): (ALT | K[","], "⌥, 추론↓"),        # lower reasoning depth
    (2, 1): (ALT | K["."], "⌥. 추론↑"),        # raise reasoning depth
    (2, 2): (CTL | K["d"], "⌃D 종료"),
    (2, 3): (M0, "/resume⏎ (매크로)"),
    (3, 0): (TO(0), "홈 → CMUX"),
    (3, 1): (M1,       "🎤 음성"),
    (3, 2): (K["esc"], "🔴 취소"),
    (3, 3): (TO(3), "다음 → Codex 앱"),
}
E2 = {(0, 0): (K["up"], "↑ 히스토리"), (0, 1): (K["down"], "↓ 히스토리"),
      (1, 0): (K["pgup"], "스크롤↑"), (1, 1): (K["pgdn"], "스크롤↓")}

L3 = {  # Codex app (ChatGPT.app v26.721) — re-verified from AXMenuItemCmdChar 2026-07-30
    (0, 0): (GUI | ALT | K["b"], "⌥⌘B 리뷰패널"),
    (0, 1): (GUI | K["n"], "⌘N New Chat"),
    (0, 2): (GUI | SFT | K["n"], "⇧⌘N New Window"),
    (0, 3): (GUI | K["f"], "⌘F Find"),
    (1, 0): (GUI | K["b"], "⌘B 사이드바"),
    (1, 1): (GUI | K["j"], "⌘J 하단패널"),
    (1, 2): (GUI | SFT | K["e"], "⇧⌘E 파일트리"),
    (1, 3): (CTL | K["`"], "⌃` 터미널"),
    (2, 0): (GUI | SFT | K["["], "⇧⌘[ 이전채팅"),
    (2, 1): (GUI | SFT | K["]"], "⇧⌘] 다음채팅"),
    (2, 2): (GUI | K[","], "⌘, 설정"),
    (2, 3): (GUI | K["o"], "⌘O 폴더열기"),
    (3, 0): (TO(0), "홈 → CMUX"),
    (3, 1): (M1,       "🎤 음성"),
    (3, 2): (K["esc"], "🔴 취소"),
    (3, 3): (TO(0), "다음 → CMUX (한바퀴)"),
}
E3 = {(0, 0): (GUI | SFT | K["["], "⇧⌘[ 이전채팅"), (0, 1): (GUI | SFT | K["]"], "⇧⌘] 다음채팅"),
      (1, 0): (K["pgup"], "스크롤↑"), (1, 1): (K["pgdn"], "스크롤↓")}

LAYERS = [(L0, E0, "L0 CMUX (탭=포커스/더블탭=전면)"),
          (L1, E1, "L1 Claude Code (cmux)"),
          (L2, E2, "L2 Codex CLI (cmux)"),
          (L3, E3, "L3 Codex 앱 (ChatGPT)")]
# 순환: L0 CMUX → L1 Claude → L2 Codex CLI → L3 Codex 앱 → L0

GRID = """       {0:^14}  {1:^14}{2:^14}  {3:^14}
       {4:^14}  {5:^14}{6:^14}  {7:^14}
       {8:^14}  {9:^14}{10:^14}  {11:^14}
       {12:^14}  {13:^14}{14:^14}  {15:^14}"""


def preview():
    for i, (keys, encs, name) in enumerate(LAYERS):
        print(f"\n━━ 레이어 {i} · {name}")
        labels = [keys[(r, c)][1] for r in range(4) for c in range(4)]
        print(GRID.format(*labels))
        print(f"       노브L {encs[(0,0)][1]} / {encs[(0,1)][1]}"
              f"     노브R {encs[(1,0)][1]} / {encs[(1,1)][1]}")


def apply():
    path = next((d["path"] for d in hid.enumerate(VID, PID)
                 if d.get("usage_page") == RAW_USAGE_PAGE and d.get("usage") == RAW_USAGE), None)
    if not path:
        raise SystemExit("❌ Micro Pad raw HID not found")
    dev = hid.Device(path=path)

    def send(p):
        dev.write(b"\x00" + bytes(p) + b"\x00" * (32 - len(p)))

    # VIA macros. Encoding: 0x01 0x01 <kc> = SS_TAP; 0x01 0x04 <lo> <hi> = SS_DELAY
    # ms = (lo-1)+(hi-1)*255; each macro is null-terminated in the shared buffer.
    m0 = b"/resume" + bytes([0x01, 0x01, 0x28])              # M0: /resume + Enter
    m1 = bytes([0x01, 0x01, 0xE0, 0x01, 0x04, 81, 1, 0x01, 0x01, 0xE0])  # M1: tap LCTL, 80ms, tap LCTL
    buf = m0 + b"\x00" + m1 + b"\x00"                        # macOS "Control twice" = dictation
    send([DK_MACRO_SET, 0x00, 0x00, len(buf)] + list(buf))
    print("✅ 매크로0=/resume⏎  매크로1=받아쓰기토글(⌃⌃)")

    for i, (keys, encs, name) in enumerate(LAYERS):
        for (r, c), (kc, _) in keys.items():
            send([DK_SET_KEYCODE, i, r, c, (kc >> 8) & 0xFF, kc & 0xFF])
        for (idx, d), (kc, _) in encs.items():
            send([DK_SET_ENCODER, i, idx, d, (kc >> 8) & 0xFF, kc & 0xFF])
        print(f"✅ 레이어 {i} 기록됨 — {name}")
    dev.close()


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply()
        preview()
    else:
        preview()
