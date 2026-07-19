/* micropad-agent — per-key agent-status LEDs (Tier B, v2)
 *
 * Copy into: qmk_firmware/keyboards/work_louder/micro/keymaps/agent/
 *   qmk compile -kb work_louder/micro -km agent
 *
 * Two host-side features layered on stock VIA (keys/layers/encoders untouched):
 *   1. LED overlay  — host pushes a 12-byte state frame over a VIA custom
 *      channel; firmware paints + animates the 12 RGB-matrix LEDs locally.
 *   2. Focus (v2)   — hold the bottom-left touch and tap a status key; firmware
 *      reports which slot over raw HID (0xF1) and the host focuses that session.
 *      Tap the touch (no status key) = HOME (layer 0).
 */
#include QMK_KEYBOARD_H
#include "raw_hid.h"
#include "via.h"

/* Flash-time default keymap. The live keymap is applied at runtime by layers.py
 * over VIA, so these are just sane defaults (4 layers, bottom-left = home,
 * bottom-right = next). [0,3] has no key (knob). */
const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    [0] = LAYOUT(
        KC_MPLY, KC_Y,    KC_ESC,  KC_NO,
        KC_A,    KC_E,    KC_W,    KC_L,
        KC_TAB,  KC_UP,   KC_DOWN, KC_C,
        TO(0),   KC_T,    KC_D,    TO(1)
    ),
    [1] = LAYOUT(
        KC_TRNS, KC_TRNS, KC_TRNS, KC_NO,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        TO(0),   KC_TRNS, KC_TRNS, TO(2)
    ),
    [2] = LAYOUT(
        KC_TRNS, KC_TRNS, KC_TRNS, KC_NO,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        TO(0),   KC_TRNS, KC_TRNS, TO(3)
    ),
    [3] = LAYOUT(
        KC_TRNS, KC_TRNS, KC_TRNS, KC_NO,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        KC_TRNS, KC_TRNS, KC_TRNS, KC_TRNS,
        TO(0),   KC_TRNS, KC_TRNS, TO(0)
    ),
};

#define CH_AGENT  0x30   // our private VIA custom channel (unused by QMK 1..5)
#define VAL_FRAME 0x01
#define NLED      12
#define STALE_MS  4000   // no host frame this long -> fall back to idle

enum agent_state {
    ST_IDLE = 0, ST_THINK_CLAUDE, ST_THINK_CODEX,
    ST_NEEDS, ST_DONE, ST_ERROR, ST_OFF = 0xFF,
};

static uint8_t  st[NLED];      // state code per LED index (0 = idle)
static uint32_t last_rx;       // timer of last host frame

/* --- focus (v2) ----------------------------------------------------------- */
#define MSG_FOCUS 0xF1         // device -> host: status key pressed while touch held
#ifndef TAPPING_TERM
#    define TAPPING_TERM 200
#endif

static bool     focus_held  = false;
static uint16_t focus_timer  = 0;
static bool     focus_used  = false;

/* matrix position -> RGB-matrix LED index, for the six status keys only */
static int8_t status_led_index(uint8_t r, uint8_t c) {
    if (r == 0 && c == 1) return 10;
    if (r == 0 && c == 2) return 11;
    if (r == 1 && c == 0) return 9;
    if (r == 1 && c == 1) return 8;
    if (r == 1 && c == 2) return 7;
    if (r == 1 && c == 3) return 6;
    return -1;
}

static void send_focus(uint8_t led) {
    uint8_t buf[32] = {0};
    buf[0] = MSG_FOCUS;
    buf[1] = led;
    raw_hid_send(buf, sizeof(buf));
}

/* Keep VIA: intercept only our channel, defer everything else to VIA core. */
bool via_custom_value_command_kb(uint8_t *data, uint8_t length) {
    if (data[1] != CH_AGENT) return false;          // not ours -> VIA handles it
    if (data[0] == id_custom_set_value && data[2] == VAL_FRAME) {
        memcpy(st, &data[3], NLED);
        last_rx = timer_read32();
    }
    raw_hid_send(data, length);                      // VIA expects an echo
    return true;                                     // consumed
}

bool rgb_matrix_indicators_advanced_user(uint8_t led_min, uint8_t led_max) {
    const uint32_t t   = timer_read32();
    // breathing: integer triangle wave 12..150, no lib8tion dependency
    uint16_t ph  = (t >> 3) & 0x1FF;                              // slow 0..511 ramp
    uint8_t  tri = ph < 256 ? (uint8_t)ph : (uint8_t)(511 - ph);  // 0..255..0
    const uint8_t breath = 12 + (uint8_t)(((uint16_t)tri * 138) / 255);  // 12..150
    const bool    blink   = (t / 250) & 1;                        // ~2 Hz
    const bool    strobe  = (t /  80) & 1;                        // fast
    const bool    stale   = timer_elapsed32(last_rx) > STALE_MS;

    for (uint8_t i = led_min; i < led_max && i < NLED; i++) {
        uint8_t s = stale ? ST_IDLE : st[i];
        HSV hsv;
        switch (s) {
            case ST_THINK_CLAUDE: hsv = (HSV){ 20, 255, breath };            break; // amber breathe
            case ST_THINK_CODEX:  hsv = (HSV){170, 255, breath };            break; // blue breathe
            case ST_NEEDS:        hsv = (HSV){ 43, 255, blink ? 150 : 0 };   break; // yellow blink
            case ST_DONE:         hsv = (HSV){ 85, 255, 150 };               break; // green solid
            case ST_ERROR:        hsv = strobe ? (HSV){0,255,150}            // red / white strobe
                                              : (HSV){0,  0,150};            break;
            case ST_OFF:          hsv = (HSV){0, 0, 0};                      break; // off
            default:              hsv = (HSV){0, 0, 14};                     break; // idle dim white
        }
        RGB rgb = hsv_to_rgb(hsv);
        rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
    }
    return false;   // we fully own these LEDs
}

/* Bottom-left touch [3,0]: hold = focus modifier, tap = HOME. While held, the
 * six status keys emit a focus event to the host instead of their shortcut. */
bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    const uint8_t r = record->event.key.row;
    const uint8_t c = record->event.key.col;

    if (r == 3 && c == 0) {
        if (record->event.pressed) {
            focus_held  = true;
            focus_used  = false;
            focus_timer = timer_read();
        } else {
            focus_held = false;
            if (!focus_used && timer_elapsed(focus_timer) < TAPPING_TERM) {
                layer_move(0);      // quick tap still goes HOME
            }
        }
        return false;               // we own this key
    }

    if (focus_held) {
        int8_t led = status_led_index(r, c);
        if (led >= 0) {
            if (record->event.pressed) {
                focus_used = true;
                send_focus((uint8_t)led);
            }
            return false;           // consume; suppress the normal shortcut
        }
    }
    return true;
}

void keyboard_post_init_user(void) {
    /* Turn the base effect off so our indicators are the only thing painting. */
    rgb_matrix_mode_noeeprom(RGB_MATRIX_SOLID_COLOR);
    rgb_matrix_sethsv_noeeprom(0, 0, 0);
}
