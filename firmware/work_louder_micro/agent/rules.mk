# micropad-agent per-key LED keymap.
# VIA pulls in RAW_ENABLE (needed for the custom command channel) and keeps the
# app + tools/layers.py / tools/via_keymap.py tooling working. RGB Matrix is already enabled
# at the keyboard level for work_louder/micro.
VIA_ENABLE = yes

# atmega32u4 flash is tight under the DFU bootloader. The agent keymap doesn't
# use mouse keys, so drop them to make room (frees ~1 KB).
MOUSEKEY_ENABLE = yes
