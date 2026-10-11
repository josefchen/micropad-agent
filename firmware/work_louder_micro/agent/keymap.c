/* micropad-agent — per-key agent-status LEDs (Tier B, v2)
 *
 * Copy into: qmk_firmware/keyboards/work_louder/micro/keymaps/agent/
 *   qmk compile -kb work_louder/micro -km agent
 *
 * Two host-side features layered on stock VIA (keys/layers/encoders untouched):
 *   1. LED overlay  — host pushes a 12-byte state frame over a VIA custom
 *      channel; firmware paints + animates the 12 RGB-matrix LEDs locally.
 *   2. Focus        — pressing a lit key reports its LED index over raw HID
 *      (0xF1) and types nothing; the host opens that session. Unlit keys keep
 *      their VIA shortcuts. VAL_BOOTLOADER lets the host start a reflash.
 */
#include QMK_KEYBOARD_H
#include "raw_hid.h"
#include "via.h"

/* Flash-time default keymap. The live keymap is applied at runtime by tools/layers.py
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
    ST_IDLE = 0,       /* 0 dim white */
    ST_THINK_CLAUDE,   /* 1 amber breathe */
    ST_THINK_CODEX,    /* 2 blue breathe */
    ST_NEEDS,          /* 3 yellow blink, unknown source */
    ST_DONE,           /* 4 green */
    ST_ERROR,          /* 5 red/white strobe */
    ST_THINK_CURSOR,   /* 6 violet breathe */
    ST_NEEDS_CLAUDE,   /* 7 amber blink */
    ST_NEEDS_CODEX,    /* 8 blue blink */
    ST_NEEDS_CURSOR,   /* 9 violet blink */
    ST_DONE_CLAUDE,    /* 10 solid amber, not green */
    ST_DONE_CODEX,     /* 11 solid blue */
    ST_DONE_CURSOR,    /* 12 solid violet */
    ST_OFF = 0xFF,
};

/* Bit 6 of a state byte marks the key the scroll wheel has selected. */
#define SEL_BIT 0x40

static uint8_t base_state(uint8_t s) {
    return s == ST_OFF ? s : (uint8_t)(s & ~SEL_BIT);
}

static bool is_selected(uint8_t s) {
    return s != ST_OFF && (s & SEL_BIT);
}

static bool state_needs_input(uint8_t s) {
    s = base_state(s);
    return s == ST_NEEDS || s == ST_NEEDS_CLAUDE || s == ST_NEEDS_CODEX || s == ST_NEEDS_CURSOR;
}

static bool state_done(uint8_t s) {
    s = base_state(s);
    return s == ST_DONE || s == ST_DONE_CLAUDE || s == ST_DONE_CODEX || s == ST_DONE_CURSOR;
}

static uint8_t  st[NLED];      // state code per LED index (0 = idle)
static uint32_t last_rx;       // timer of last host frame

#define VAL_BOOTLOADER 0x02    // host -> pad: reboot into DFU for a hands-free reflash
#define MSG_FOCUS      0xF1    // pad -> host: a lit session key was pressed
#define MSG_RELEASE    0xF3    // pad -> host: that key was let go (host times the hold)
#define MSG_SCROLL     0xF2    // pad -> host: wheel turned, +1 / -1

static int8_t key_led_index(uint8_t r, uint8_t c) {
    static const int8_t map[4][4] = {
        { -1, 10, 11, -1 },
        {  9,  8,  7,  6 },
        {  2,  3,  4,  5 },
        { -1,  1,  0, -1 },
    };
    if (r > 3 || c > 3) return -1;
    return map[r][c];
}

static bool host_alive(void) {
    return timer_elapsed32(last_rx) <= STALE_MS;
}

static bool key_has_session(int8_t led) {
    return led >= 0 && host_alive() && st[led] != ST_OFF && base_state(st[led]) != ST_IDLE;
}

static void send_key_event(uint8_t msg, uint8_t led) {
    uint8_t buf[32] = {0};
    buf[0] = msg;
    buf[1] = led;
    raw_hid_send(buf, sizeof(buf));
}

/* --- layer indicator LEDs (3 discrete PWM LEDs, B5/B6/B7) ------------------
 * Restores the stock behaviour our v1 keymap dropped: one LED lit per active
 * layer (L0 = all off). Verbatim from keyboards/work_louder/micro/keymaps/default. */
typedef union {
    uint32_t raw;
    struct { uint8_t led_level : 3; };
} work_louder_config_t;
static work_louder_config_t work_louder_config;

layer_state_t layer_state_set_user(layer_state_t state) {
    layer_state_cmp(state, 1) ? work_louder_micro_led_1_on() : work_louder_micro_led_1_off();
    layer_state_cmp(state, 2) ? work_louder_micro_led_2_on() : work_louder_micro_led_2_off();
    layer_state_cmp(state, 3) ? work_louder_micro_led_3_on() : work_louder_micro_led_3_off();
    return state;
}

void eeconfig_init_user(void) {
    work_louder_config.raw = 0;
    work_louder_config.led_level = 1;
    eeconfig_update_user(work_louder_config.raw);
}

void matrix_init_user(void) {
    work_louder_config.raw = eeconfig_read_user();
    work_louder_micro_led_all_set((uint8_t)(work_louder_config.led_level * 255 / 4));
}

/* --- underglow as an aggregate alert (perimeter, D2) -----------------------
 * The keycaps show per-session detail; the underglow shows the single most
 * urgent state across all sessions so it reads across a room:
 *   error > needs-input > done > calm (white). Recomputed from the state frame. */
enum { UG_CALM = 0, UG_DONE, UG_NEEDS, UG_ERROR };
static uint8_t ug_last = 0xFF;

static void underglow_set(uint8_t agg) {
    if (agg == ug_last) return;
    ug_last = agg;
    switch (agg) {
        case UG_ERROR: rgblight_sethsv_noeeprom(0,  255, 255); rgblight_mode_noeeprom(RGBLIGHT_MODE_BREATHING + 3); break; // red, fast
        case UG_NEEDS: rgblight_sethsv_noeeprom(43, 255, 255); rgblight_mode_noeeprom(RGBLIGHT_MODE_BREATHING + 2); break; // yellow
        case UG_DONE:  rgblight_sethsv_noeeprom(85, 255, 200); rgblight_mode_noeeprom(RGBLIGHT_MODE_BREATHING + 1); break; // green
        default:       rgblight_sethsv_noeeprom(0,  0,   200); rgblight_mode_noeeprom(RGBLIGHT_MODE_BREATHING + 1); break; // white
    }
}

static void apply_layer_leds(void) {
    layer_state_t state = layer_state;
    layer_state_cmp(state, 1) ? work_louder_micro_led_1_on() : work_louder_micro_led_1_off();
    layer_state_cmp(state, 2) ? work_louder_micro_led_2_on() : work_louder_micro_led_2_off();
    layer_state_cmp(state, 3) ? work_louder_micro_led_3_on() : work_louder_micro_led_3_off();
}

static bool layer_leds_overridden = false;
static uint8_t prev_done = 0;
static uint32_t burst_start = 0;
static bool burst_pending = false;
static bool burst_active = false;

static void set_alert_leds(bool on) {
    if (on) {
        work_louder_micro_led_1_on();
        work_louder_micro_led_2_on();
        work_louder_micro_led_3_on();
    } else {
        work_louder_micro_led_1_off();
        work_louder_micro_led_2_off();
        work_louder_micro_led_3_off();
    }
}

/* Needs-input holds the 3 white LEDs in a blink. A completion plays three
 * flashes, then the layer lights return. */
static void paint_alert_leds(bool needs) {
    if (needs) {
        set_alert_leds((timer_read32() / 180) & 1);
        layer_leds_overridden = true;
        return;
    }
    if (burst_pending) {
        burst_pending = false;
        burst_active = true;
        burst_start = timer_read32();
    }
    if (burst_active) {
        uint32_t elapsed = timer_elapsed32(burst_start);
        if (elapsed >= 1080) {
            burst_active = false;
        } else {
            /* 180 ms on, 180 ms off, three times. */
            set_alert_leds(((elapsed / 180) % 2) == 0);
            layer_leds_overridden = true;
            return;
        }
    }
    if (layer_leds_overridden) {
        apply_layer_leds();
        layer_leds_overridden = false;
    }
}

/* A key that just turned done blinks for DONE_FLASH_MS so you can see which
 * session finished, then stays solid. */
#define DONE_FLASH_MS 10000
static uint8_t  prev_st[NLED];
static uint32_t flash_start[NLED];
static uint16_t flashing;      // bit per LED still in its done flash

static bool done_flashing(uint8_t i) {
    return (flashing >> i) & 1;
}

void housekeeping_task_user(void) {
    uint8_t agg = UG_CALM;
    bool needs = false;
    if (timer_elapsed32(last_rx) <= STALE_MS) {
        bool err = false;
        uint8_t done = 0;
        for (uint8_t i = 0; i < NLED; i++) {
            uint16_t bit = (uint16_t)1 << i;
            if (state_done(st[i]) && !state_done(prev_st[i])) {
                flash_start[i] = timer_read32();
                flashing |= bit;
            } else if (!state_done(st[i]) || timer_elapsed32(flash_start[i]) >= DONE_FLASH_MS) {
                flashing &= ~bit;
            }
            prev_st[i] = st[i];
            if (base_state(st[i]) == ST_ERROR) err = true;
            else if (state_needs_input(st[i])) needs = true;
            else if (state_done(st[i])) done++;
        }
        if (done > prev_done) {
            burst_pending = true;
        }
        prev_done = done;
        agg = err ? UG_ERROR : needs ? UG_NEEDS : done ? UG_DONE : UG_CALM;
    } else {
        prev_done = 0;
        burst_pending = false;
        burst_active = false;
        flashing = 0;
        memcpy(prev_st, st, sizeof(prev_st));
    }
    underglow_set(agg);
    paint_alert_leds(needs);
}

/* Keep VIA: intercept only our channel, defer everything else to VIA core. */
bool via_custom_value_command_kb(uint8_t *data, uint8_t length) {
    if (data[1] != CH_AGENT) return false;          // not ours -> VIA handles it
    if (data[0] == id_custom_set_value && data[2] == VAL_FRAME) {
        memcpy(st, &data[3], NLED);
        last_rx = timer_read32();
    } else if (data[0] == id_custom_set_value && data[2] == VAL_BOOTLOADER) {
        raw_hid_send(data, length);
        wait_ms(50);
        reset_keyboard();
        return true;
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
        uint8_t s = stale ? ST_IDLE : base_state(st[i]);
        const bool sel_white = !stale && is_selected(st[i]) && ((t / 150) & 1);
        const uint8_t done_v = done_flashing(i) && ((t / 200) & 1) ? 0 : 150;
        HSV hsv;
        switch (s) {
            case ST_THINK_CLAUDE: hsv = (HSV){ 28, 255, breath };            break; // amber breathe
            case ST_THINK_CODEX:  hsv = (HSV){170, 255, breath };            break; // blue breathe
            case ST_THINK_CURSOR: hsv = (HSV){213, 255, breath };            break; // violet breathe
            case ST_NEEDS:        hsv = (HSV){ 43, 255, blink ? 150 : 0 };   break; // yellow blink
            case ST_NEEDS_CLAUDE: hsv = (HSV){ 28, 255, blink ? 150 : 0 };   break; // amber blink
            case ST_NEEDS_CODEX:  hsv = (HSV){170, 255, blink ? 150 : 0 };   break; // blue blink
            case ST_NEEDS_CURSOR: hsv = (HSV){213, 255, blink ? 150 : 0 };   break; // violet blink
            case ST_DONE:         hsv = (HSV){  0,   0,  40 };              break; // dim white; green is the backlight
            case ST_DONE_CLAUDE:  hsv = (HSV){ 28, 255, done_v };            break; // amber: flash, then solid
            case ST_DONE_CODEX:   hsv = (HSV){170, 255, done_v };            break; // blue
            case ST_DONE_CURSOR:  hsv = (HSV){213, 255, done_v };            break; // violet
            case ST_ERROR:        hsv = strobe ? (HSV){0,255,150}            // red / white strobe
                                              : (HSV){0,  0,150};            break;
            case ST_OFF:          hsv = (HSV){0, 0, 0};                      break; // off
            default:              hsv = (HSV){0, 0, 14};                     break; // idle dim white
        }
        if (sel_white) hsv = (HSV){0, 0, 200};
        RGB rgb = hsv_to_rgb(hsv);
        rgb_matrix_set_color(i, rgb.r, rgb.g, rgb.b);
    }
    return false;   // we fully own these LEDs
}

/* A lit key belongs to a session: pressing it opens that session and types
 * nothing. A dark key has no session, so it keeps its VIA shortcut. */
static uint16_t swallowed;     // bit per LED whose press we consumed

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    (void)keycode;
    int8_t led = key_led_index(record->event.key.row, record->event.key.col);
    if (led < 0) return true;
    uint16_t bit = (uint16_t)1 << led;
    if (record->event.pressed) {
        if (key_has_session(led)) {
            send_key_event(MSG_FOCUS, (uint8_t)led);
            swallowed |= bit;
            return false;
        }
        return true;
    }
    if (swallowed & bit) {
        swallowed &= ~bit;
        send_key_event(MSG_RELEASE, (uint8_t)led);
        return false;
    }
    return true;
}

/* Encoders: top-left scroll wheel (index 0) = session switcher (mouse wheel
 * when no host); the right knob (index 1) = volume. */
bool encoder_update_user(uint8_t index, bool clockwise) {
    if (index == 0) {
        /* With the host alive the wheel walks through live sessions; without
         * it, it is a plain mouse wheel again. */
        if (host_alive()) {
            uint8_t buf[32] = {0};
            buf[0] = MSG_SCROLL;
            buf[1] = clockwise ? 1 : 0xFF;
            raw_hid_send(buf, sizeof(buf));
        } else {
            tap_code(clockwise ? MS_WHLU : MS_WHLD);
        }
        return false;
    }
    if (index == 1) {
        tap_code(clockwise ? KC_VOLU : KC_VOLD);
        return false;
    }
    return true;
}

void keyboard_post_init_user(void) {
    /* Per-key matrix (12 LEDs, D1): base effect off so our indicators own them. */
    rgb_matrix_mode_noeeprom(RGB_MATRIX_SOLID_COLOR);
    rgb_matrix_sethsv_noeeprom(0, 0, 0);

    /* Underglow / perimeter (8 LEDs, separate D2 chain): ambient white breathing.
     * Independent of the keycap status LEDs — this is the pad's "alive" glow. */
    rgblight_enable_noeeprom();
    rgblight_sethsv_noeeprom(0, 0, 200);              // white (sat 0)
    rgblight_mode_noeeprom(RGBLIGHT_MODE_BREATHING + 1);
}
