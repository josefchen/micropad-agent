# work_louder/micro — `agent` keymap (per-key agent-status LEDs)

**Compile-verified** against QMK `0.33.8-5-ga738fad372` (avr-gcc 8.5.0),
28020/28672 bytes (97%, 652 free). **Flash-tested on hardware** (atmel-dfu,
validate OK) — keycap states, focus events, layer LEDs, and the aggregate
underglow all verified under `daemon_perkey.py`. Adds a host-driven LED overlay
on top of stock VIA. Full design in
[`docs/per-key-led-design.md`](../../../docs/per-key-led-design.md).

A prebuilt `work_louder_micro_agent.hex` is included for direct flashing (built
from the commit above — rebuild if your QMK differs).

## Flash the prebuilt hex (fastest)

1. Hold the **top-left encoder (scroll wheel)** while plugging in → DFU bootloader.
2. QMK Toolbox → load `work_louder_micro_agent.hex` → Flash. (Or `qmk flash` below.)

## Build from source

```sh
qmk setup                                        # once
cp -R firmware/work_louder_micro/agent \
      "$(qmk config user.qmk_home | cut -d= -f2)/keyboards/work_louder/micro/keymaps/agent"
qmk compile -kb work_louder/micro -km agent      # 95% flash, 1240 B free
qmk flash   -kb work_louder/micro -km agent      # hold scroll wheel to enter bootloader
```

> macOS: avr-gcc from the `osx-cross/avr` tap is keg-only — prepend it to PATH:
> `export PATH="$(brew --prefix avr-gcc@8)/bin:$(brew --prefix avr-binutils)/bin:$PATH"`

atmega32u4 / atmel-dfu — recoverable, low brick risk. If the VIA keymap resets,
re-apply the 4 layers with `./run.sh layers.py --apply`.

## What it does

- `via_custom_value_command_kb` — receives a 12-byte state frame on VIA custom
  channel `0x30`; all other channels fall through to VIA unchanged.
- `rgb_matrix_indicators_advanced_user` — paints + animates the 12 keycap LEDs
  (D1) from the frame (breathe / blink / strobe), idle after 4 s of no host.
- `process_record_user` — hold the bottom-left touch and tap a status key to
  emit a focus event to the host over raw HID (`0xF1`); tap alone = HOME.
- `layer_state_set_user` + `matrix_init_user` — the 3 discrete layer LEDs
  (B5/B6/B7) light one-per-active-layer (restored from the stock keymap).
- `housekeeping_task_user` — drives the 8-LED underglow (separate D2 chain) as
  an aggregate alert: white (calm) → green (done) → yellow (needs) → red (error),
  most-urgent-wins, so state reads from across a room.

Drive it from the host with `daemon_perkey.py`.

## Build notes (fixes applied to get a clean compile)

- Every QMK keymap must define `keymaps[]` even under VIA — added 4 default
  layers (VIA/`layers.py` overrides them at runtime).
- `sin8`/`scale8` live in `lib/lib8tion` which isn't on the keymap include path;
  replaced with an inline integer triangle wave (no dependency).
- `MOUSEKEY_ENABLE = no` to fit the DFU-reduced flash (unused by this keymap).
- Confirmed compiling for this QMK: `via_custom_value_command_kb`,
  `id_custom_set_value`, `hsv_to_rgb`, `rgb_matrix_set_color`, `raw_hid_send`.
