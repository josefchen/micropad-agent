/* micropad-agent — per-key agent-status LEDs (Tier B, v1)
 *
 * Copy into: qmk_firmware/keyboards/work_louder/micro/keymaps/agent/
 *   qmk compile -kb work_louder/micro -km agent
 *
 * Adds an LED overlay driven by the host over a VIA custom command channel.
 * Keys, layers, and encoders are left to the stock/VIA keymap — this only paints
 * the 12 RGB-matrix LEDs from a 12-byte state frame and animates them locally.
 *
 * DRAFT — written from the QMK API, not yet compiled/flashed. Verify the
 * via_custom_value_command_kb hook and id_custom_set_value symbol against the
 * QMK version you build with.
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

void keyboard_post_init_user(void) {
    /* Turn the base effect off so our indicators are the only thing painting. */
    rgb_matrix_mode_noeeprom(RGB_MATRIX_SOLID_COLOR);
    rgb_matrix_sethsv_noeeprom(0, 0, 0);
}
