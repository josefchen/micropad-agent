# Per-key agent-status LEDs (Tier B) — design

Drive **each key's LED independently** from agent state — Codex-Micro "Agent
Keys" style — instead of lighting the whole pad one colour. This is a firmware
change; the stock VIA lighting protocol can only set one global colour.

Status: **design + v1, firmware compile-verified** (QMK `0.33.8-5-ga738fad372`,
27432/28672 B, 95%). Not yet flashed to hardware. See the checklist at the end.

---

## Research findings (confirmed)

The Work Louder × Figma Micro Pad is `work_louder/micro` in mainline QMK — its
USB `VID:PID` `0x574C:0xE6E3` matches this unit exactly.

| Fact | Value | Consequence |
|------|-------|-------------|
| MCU | **atmega32u4** | not RP2040 (that's the newer Creator Micro) |
| Bootloader | **atmel-dfu** | flash `.hex` via QMK Toolbox; low brick risk (recoverable) |
| Bootloader entry | hold **top-left encoder** (scroll wheel) while plugging in | |
| Matrix | 4×4 | 12 keycaps + 4 corner controls |
| RGB Matrix | enabled, **12 WS2812** | per-key addressable via `rgb_matrix_set_color()` |
| Encoders | 2 | scroll wheel + knob |
| Raw HID | via VIA | custom command channel available for our frames |

### LED index → key map (from `keyboards/work_louder/micro/keyboard.json`)

The 12 LEDs land exactly on the 12 keycap positions; the 4 corners have no LED
(scroll wheel, knob, Figma touch button, layer LED touch button).

```
        col0        col1        col2        col3
row0   (wheel)      LED10       LED11       (knob)
row1   LED9         LED8        LED7        LED6
row2   LED2         LED3        LED4        LED5
row3   (figma)      LED1        LED0        (layer LED)
```

| LED | key[r,c] | role (from keycap guide) |
|-----|----------|--------------------------|
| 10  | [0,1] | 승인 (accept) |
| 11  | [0,2] | 거절 (reject) |
| 9   | [1,0] | |
| 8   | [1,1] | |
| 7   | [1,2] | |
| 6   | [1,3] | |
| 2   | [2,0] | |
| 3   | [2,1] | |
| 4   | [2,2] | |
| 5   | [2,3] | 중단 (interrupt) |
| 1   | [3,1] | |
| 0   | [3,2] | |

---

## Key design decision: send **state codes**, not RGB

A raw HID report on atmega32u4 is **32 bytes**. 12 keys × RGB (3 B) = 36 B — too
big for one report. So the host sends **one state code per key (12 B)** and the
firmware owns the colour + animation. Benefits:

- Breathe / blink / strobe run firmware-side at full frame rate.
- Host transmits only on state change (event-driven) + a slow heartbeat — no 20 Hz polling.
- One report, no fragmentation.

### Protocol (host → keyboard, inside the VIA custom envelope)

```
[0x07,      0x30,       0x01,      s0, s1, …, s11]      + zero-pad to 32 B
 set_value  CH_AGENT    VAL_FRAME  state per LED index
```

State codes:

| code | meaning | colour | motion |
|------|---------|--------|--------|
| 0 | idle | dim white | solid |
| 1 | thinking · Claude | amber (hue 20) | breathe |
| 2 | thinking · Codex | blue (hue 170) | breathe |
| 3 | needs input | yellow (hue 43) | 2 Hz blink |
| 4 | done | green (hue 85) | solid |
| 5 | error | red | red⇄white strobe |
| 0xFF | slot unused | off | — |

`s[i]` is indexed by **LED index** (table above). The daemon owns the
logical-slot → LED-index mapping; the firmware just paints LED `i` with `s[i]`.

We keep VIA: `via_custom_value_command_kb` intercepts only channel `0x30` and
returns `false` for everything else, so the VIA app, `layers.py`, and
`via_keymap.py` keep working unchanged. Keys, layers, and shortcuts are untouched
— this only adds an LED overlay.

---

## Firmware (v1)

Drop-in keymap at `firmware/work_louder_micro/agent/`. Copy into
`qmk_firmware/keyboards/work_louder/micro/keymaps/agent/`, then:

```
qmk compile -kb work_louder/micro -km agent      # build only, verify first
# hold top-left encoder, plug in → bootloader
qmk flash   -kb work_louder/micro -km agent      # or QMK Toolbox with the .hex
```

The keymap adds `via_custom_value_command_kb()` (receive frame) and
`rgb_matrix_indicators_advanced_user()` (paint + animate). It falls back to idle
if no frame arrives for `STALE_MS`, so an unplugged host doesn't freeze stale
colours. See `firmware/work_louder_micro/agent/keymap.c`.

> Compile-verified against QMK `0.33.8-5-ga738fad372`. The VIA hook
> (`via_custom_value_command_kb`) and `id_custom_set_value` symbol compile
> correctly for this version. A prebuilt `.hex` sits next to the keymap. Still
> needs a real flash + on-pad check.

---

## Host (daemon)

`daemon_perkey.py` is a **standalone** host-side renderer (v1) that does NOT
touch the running `daemon.py`. Use it only after flashing the custom firmware:

```
launchctl unload ~/Library/LaunchAgents/<label>.plist   # stop global daemon
./run.sh daemon_perkey.py                                # per-key renderer
```

Once validated, fold it into `daemon.py` (behind a flag or firmware probe) and
back into launchd.

### v1 vs v2

- **v1 (implemented, no hook changes):** the winning state fills all 12 keys.
  Visually like today, but proves the per-key pipe end to end.
- **v2 (Codex-Micro style, needs hook changes):** each concurrent session gets
  its own key. Requires the hooks to also POST a session id —
  `session_id` from Claude hook stdin, `thread-id` from the Codex notify payload
  (materials already exist). Then the daemon assigns `session_id → LED slot`,
  frees on `SessionEnd`.

```
SLOTS = [10, 11, 9, 8, 3, 4]   # LED indices reserved as status slots (priority)
assign: session_id → slot       # first-seen grabs a free slot; SessionEnd releases
```

---

## Keycap implication

A key can be BOTH a shortcut and a status light — the LED is independent of the
keypress, so no shortcut is sacrificed. But an opaque colour cap blocks the LED.
So per status key you choose: **translucent/white cap (see status)** or **opaque
colour cap (static meaning)** — not both. Decide which of the 12 keys are status
slots and cap those translucent; the rest can stay opaque per the keycap guide.

---

## Checklist

- [x] Set up a QMK build env; `qmk compile -kb work_louder/micro -km agent` succeeds
- [x] Verify `via_custom_value_command_kb` hook + `id_custom_set_value` against QMK version
- [ ] Flash via QMK Toolbox (hold top-left encoder to enter bootloader)
- [ ] Re-apply the 4-layer keymap (`layers.py --apply`) if VIA keymap resets
- [ ] Run `daemon_perkey.py`, confirm v1 (winner state on all 12 keys)
- [ ] Buy translucent Choc caps for chosen status keys
- [ ] v2: add session id to hooks, implement slot assignment
- [ ] Fold per-key path into `daemon.py` + launchd
```
