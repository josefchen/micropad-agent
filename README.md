# micropad-agent

**English** · [한국어](#한국어)

Turn a **Work Louder × Figma Micro Pad** into a live status light for your AI
coding agents. The pad's LEDs breathe, blink, and strobe to show what Claude
Code and the Codex CLI are doing right now — no firmware flashing, no Micro
Codex module required. It's a small Python daemon that renders the LEDs
host-side over raw HID.

> Built so other Figma × Work Louder owners can **reuse the pad they already
> have** instead of buying the dedicated Micro Codex.

## What you see

Colour encodes **state** (Codex-Micro style); only the working states keep the
agent's identity hue, so the pad reads as *what's happening* first and *who* only
while it's actually running:

| State | Colour | Motion |
|-------|--------|--------|
| idle | dim white | solid |
| working (thinking / coding) | amber (Claude) · blue (Codex) | breathing (coding a little faster) |
| needs you (permission / notification) | yellow | 2 Hz blink |
| done | green | solid, fades to idle after ~45 s |
| error | red | red⇄white strobe (the white half is what makes it read as an error) |

When both agents are active, the most urgent state wins the pad; a fresh "done"
briefly announces itself so a finish isn't invisible while the other agent works.

## Requirements

- macOS (Apple Silicon or Intel)
- [Homebrew](https://brew.sh)
- A Work Louder × Figma Micro Pad (VIA-enabled QMK firmware, the stock one is fine)
- Optionally: [Claude Code](https://claude.com/claude-code) and/or the Codex CLI

### Hardware facts (measured on the stock pad)

- USB `VID:PID` = `0x574C:0xE6E3`, raw HID usage page/usage `0xFF60`/`0x61`
- VIA protocol v12; 16 keys (4×4) + 2 knobs + 12 WS2812 LEDs (inner 12 keys only)
- `rgb_matrix` (channel 3) and `rgblight` (channel 2) both respond; `backlight` does not
- `max_brightness` = 150

The daemon renders solid colour + brightness modulation host-side at 20 Hz, so it
never depends on firmware effect ids.

## Install

```bash
git clone https://github.com/kimtoma/micropad-agent.git
cd micropad-agent
./bootstrap.sh
```

`bootstrap.sh` is idempotent and path-agnostic — it derives everything from
`$HOME`, your username, and `brew --prefix`, so it works under any account on
Apple Silicon or Intel. It will:

1. install `hidapi` via Homebrew
2. create a Python venv and install the `hid` package
3. generate and load a launchd agent (`dev.micropad.agent`) that keeps the daemon running
4. install the Claude Code hooks into `~/.claude/settings.json`
5. wire the Codex CLI `notify` into `~/.codex/config.toml` (only if you don't already have a `notify` set)

Then plug in the pad and drive Claude Code or the Codex CLI.

To remove everything: `./bootstrap.sh --uninstall`.

## The pad roams between machines

The Micro Pad is one physical device. The daemon idles harmlessly when the pad
is unplugged and **auto-reconnects** when it reappears, so you can run
`./bootstrap.sh` on every machine you carry the pad to — only the machine the
pad is currently plugged into lights up.

## How it works

```
Claude Code hooks ─┐
                   ├─▶ POST 127.0.0.1:48801/state  ─▶  daemon.py  ─▶  raw HID  ─▶  pad LEDs
Codex CLI notify  ─┘        {state, source}            (20 Hz render loop)
```

- `daemon.py` — the render loop + a tiny HTTP server on `127.0.0.1:48801`. `GET /health` and `GET /state` are available for debugging.
- `install_hooks.py` — adds/removes the seven Claude Code hooks (tagged `# micropad-agent`, so it never touches your other hooks).
- `hooks/codex_notify.sh` — the Codex `notify` target; reports turn-end.
- `probe.py`, `colortest.py`, `errortest.py` — bring-up / diagnostic tools.
- `via_keymap.py`, `layers.py`, `gen_karabiner.py` — optional keymap tooling (see below).

## Optional: per-key LEDs (Tier B)

By default the daemon paints the **winning state across all 12 keys** on stock
firmware — that's the no-flashing path above. **Tier B** drives each key
independently, which needs a small custom QMK build (the `agent` keymap) flashed
to the pad. A prebuilt, compile-verified hex ships in the repo.

```bash
# 1. enter the bootloader: hold the top-left encoder (scroll wheel) while plugging in
# 2. flash the prebuilt hex (atmega32u4 / atmel-dfu — low brick risk)
qmk flash firmware/work_louder_micro/agent/work_louder_micro_agent.hex   # or QMK Toolbox
# 3. install the per-key renderer instead of the stock one
./bootstrap.sh --per-key
```

Tier B gives **each concurrent session its own LED**: the daemon assigns every
Claude/Codex session a key from a reserved strip and paints it with that
session's state, so two agents show up on different keys (a 1-byte state code per
key on VIA custom channel `0x30`; your VIA keymap keeps working untouched). Full
protocol in [docs/per-key-led-design.md](docs/per-key-led-design.md); flashing
details in
[firmware/work_louder_micro/agent/readme.md](firmware/work_louder_micro/agent/readme.md).

With Tier B the top-left wheel becomes a **session switcher** while the daemon is
running: each notch moves a white highlight through the lit keys, and resting the
wheel for ~0.9 s opens the highlighted session. With no daemon it is a mouse wheel
again.

### Menu bar app

`mac/build.sh` builds `~/Applications/Micropad.app` (one AppKit file, ~14 MB
resident) and installs it as the `dev.micropad.menubar` LaunchAgent. It shows a
dot per live session in the menu bar (◆ blinking = needs you, ● working,
○ done), a menu to open any session, and a Cmd-Tab-style panel while you turn the
wheel. It listens to the daemon's `GET /events` stream rather than polling.

## Optional: keymap & app routing

`via_keymap.py` and `layers.py` can rewrite the pad's keymap over the same VIA
protocol (with `--backup` / `--restore` for safety). `layers.py` also writes VIA
macros — a `/resume` string and a double-tap-Control trigger for macOS dictation.
`gen_karabiner.py` emits Karabiner-Elements rules for per-app shortcut routing.
**These encode one person's app stack and shortcuts** — treat them as a worked
example and edit the tables to fit your own setup. They are not required for the
LED feature.

Keys that fire an app's own shortcut only work if that app actually has one. A
global hotkey you record inside the app (ChatGPT's voice chat hotkey, say) has to
match the combo the pad sends, or the key silently does nothing.

## Gotchas

- **Close the VIA web app while the daemon runs** — WebHID takes exclusive access to the pad and the LEDs go dark.
- **`pkill` won't stop the daemon** — launchd `KeepAlive` restarts it. Use `launchctl unload ~/Library/LaunchAgents/dev.micropad.agent.plist`.
- **Codex lights up only from the CLI.** The ChatGPT desktop app is Electron and doesn't read `~/.codex/config.toml`, so it can't fire `notify`.
- If Codex Computer Use is installed, it rewrites `config.toml` on startup to put itself first and chains this hook behind its `--previous-notify` flag. That's expected — `codex_notify.sh` only reports and exits, so nothing double-fires.
- **VIA macro delays are ASCII, not the two bytes VIA documents.** The firmware replays macros through `send_string_with_delay_impl`, whose `SS_DELAY` branch parses decimal digits up to a non-digit and consumes that terminator — QMK's `SS_DELAY(n)` is `"\1\4" #n "|"`. Writing the `(lo-1)+(hi-1)*255` byte pair doesn't merely drop the delay: it desyncs the stream and everything after it replays as garbage, so the macro looks completely dead rather than mistimed.
- **Read the keymap back after writing it.** Individual writes land instantly, but a full `--apply` sweep has been observed to lose its tail — the bottom row stayed at stale values on every layer while everything above it was correct. `0x04` (get keycode) and `0x0E` (get macro buffer) are read-only and settle it in seconds. Unload the daemon first; it holds the device exclusively, for reads too.

## License

MIT — see [LICENSE](LICENSE).

---

## 한국어

[English](#micropad-agent) · **한국어**

**Work Louder × Figma Micro Pad**를 AI 코딩 에이전트의 실시간 상태등으로
바꿔줍니다. 펌웨어 플래싱도, 별도의 Micro Codex 모듈도 필요 없어요. 작은 파이썬
데몬이 raw HID로 LED를 호스트에서 직접 렌더링해서, Claude Code와 Codex CLI가
지금 무엇을 하는지 LED가 숨쉬고·깜빡이고·번쩍이며 보여줍니다.

> Figma × Work Louder 키패드를 이미 가진 분들이 **Micro Codex를 따로 사지 않고
> 재활용**할 수 있게 만들었습니다.

### 무엇이 보이나

**색 = 상태** (Codex Micro 방식). 작업 중일 때만 에이전트 색을 유지해, 패드가
*무슨 일인지*를 먼저, *누구인지*는 도는 동안에만 보여줍니다:

| 상태 | 색 | 거동 |
|------|----|------|
| 대기(idle) | 흐린 화이트 | solid |
| 작업 중 (thinking / coding) | 앰버(Claude) · 파랑(Codex) | 숨쉬기 (코딩이 조금 빠름) |
| 승인 대기 (권한 / 알림) | 노랑 | 2 Hz 깜빡임 |
| 완료 | 초록 | solid → 약 45초 후 페이드 |
| 에러 | 빨강 | 빨강⇄하양 스트로브 (하양이 에러로 읽히게 함) |

두 에이전트가 동시에 활동하면 가장 급한 상태가 패드를 차지하고, 방금 끝난
"완료"는 잠깐 자기를 알립니다(다른 에이전트가 작업 중이어도 완료가 묻히지 않게).

### 요구사항

- macOS (애플 실리콘 또는 인텔)
- [Homebrew](https://brew.sh)
- Work Louder × Figma Micro Pad (VIA 지원 QMK 펌웨어, 공장 기본 그대로 OK)
- (선택) [Claude Code](https://claude.com/claude-code) 및/또는 Codex CLI

**하드웨어 사실 (공장 패드 실측):** USB `VID:PID` = `0x574C:0xE6E3`, raw HID
usage page/usage `0xFF60`/`0x61`, VIA 프로토콜 v12, 16키(4×4) + 노브 2 + WS2812
12개(안쪽 12키에만), `max_brightness` 150. 데몬은 펌웨어 effect를 안 쓰고
호스트에서 20Hz로 solid 색상 + 밝기 변조를 렌더링합니다.

### 설치

```bash
git clone https://github.com/kimtoma/micropad-agent.git
cd micropad-agent
./bootstrap.sh
```

`bootstrap.sh`는 멱등(여러 번 실행해도 안전)하고 경로에 구애받지 않아요 —
`$HOME`·사용자명·`brew --prefix`에서 전부 유도하므로 어떤 계정이든, 애플
실리콘/인텔이든 동작합니다. 하는 일: (1) Homebrew로 `hidapi` 설치, (2) 파이썬
venv 생성 + `hid` 설치, (3) 데몬을 상주시키는 launchd agent(`dev.micropad.agent`)
생성·로드, (4) Claude Code 훅을 `~/.claude/settings.json`에 설치, (5) Codex CLI
`notify`를 `~/.codex/config.toml`에 연결(기존 `notify`가 없을 때만).

제거: `./bootstrap.sh --uninstall`.

### 패드는 기기 사이를 옮겨다닙니다

Micro Pad는 물리적으로 한 대뿐인 장치예요. 데몬은 패드가 빠져 있으면 조용히
대기하다가 다시 꽂으면 **자동 재연결**하므로, 패드를 들고 다니는 모든 기계에
`./bootstrap.sh`를 한 번씩 돌려두면 됩니다 — 그 순간 패드가 꽂힌 기계만
점등됩니다.

### 동작 방식

```
Claude Code 훅 ─┐
                ├─▶ POST 127.0.0.1:48801/state ─▶ daemon.py ─▶ raw HID ─▶ 패드 LED
Codex CLI notify ─┘        {state, source}         (20Hz 렌더 루프)
```

- `daemon.py` — 렌더 루프 + `127.0.0.1:48801` 초소형 HTTP 서버 (`GET /health`, `GET /state`로 디버깅)
- `install_hooks.py` — Claude Code 훅 7개 설치/제거 (`# micropad-agent` 태그라 다른 훅은 안 건드림)
- `hooks/codex_notify.sh` — Codex `notify` 타깃, 턴 종료 보고
- `probe.py`·`colortest.py`·`errortest.py` — 초기 점검/진단 도구
- `via_keymap.py`·`layers.py`·`gen_karabiner.py` — (선택) 키맵 도구, 아래 참고

### (선택) 키별 LED (Tier B)

기본값은 공장 펌웨어에서 **승자 상태를 12키 전부에 동일하게** 칠하는 방식이라
플래싱이 필요 없습니다(위 설치 경로). **Tier B**는 각 키를 독립적으로 제어하며,
그러려면 작은 커스텀 QMK 빌드(`agent` 키맵)를 패드에 플래시해야 합니다. 컴파일
검증된 프리빌트 hex가 레포에 포함돼 있습니다.

```bash
# 1. 부트로더 진입: 좌상단 인코더(스크롤휠)를 누른 채로 패드 연결
# 2. 프리빌트 hex 플래시 (atmega32u4 / atmel-dfu — 브릭 위험 낮음)
qmk flash firmware/work_louder_micro/agent/work_louder_micro_agent.hex   # 또는 QMK Toolbox
# 3. stock 대신 per-key 렌더러 설치
./bootstrap.sh --per-key
```

Tier B는 **동시 세션마다 자기 LED**를 가집니다. 데몬이 각 Claude/Codex 세션에
예약된 스트립에서 키 하나를 배정하고 그 세션의 상태색으로 칠하므로, 두 에이전트가
서로 다른 키에 표시됩니다(VIA 커스텀 채널 `0x30`에 키당 1바이트 상태코드; 기존 VIA
키맵은 그대로 유지). 프로토콜 전문은
[docs/per-key-led-design.md](docs/per-key-led-design.md), 플래싱 상세는
[firmware/work_louder_micro/agent/readme.md](firmware/work_louder_micro/agent/readme.md).

### (선택) 키맵 & 앱 라우팅

`via_keymap.py`·`layers.py`는 같은 VIA 프로토콜로 패드 키맵을 다시 쓸 수 있고
(`--backup` / `--restore`로 안전하게), `layers.py`는 VIA 매크로도 씁니다 —
`/resume` 문자열과 macOS 받아쓰기용 Control 두 번 누르기. `gen_karabiner.py`는
앱별 단축키 라우팅용 Karabiner-Elements 규칙을 생성합니다. **이 파일들은 특정
개인의 앱 스택·단축키를 담고 있으니** 예시로 보고 자기 환경에 맞게 표를 고쳐
쓰세요. LED 기능에는 필요 없습니다.

앱 자체 단축키를 부르는 키는 그 앱에 실제로 단축키가 있어야 동작합니다. 앱 안에서
녹화하는 전역 핫키(예: ChatGPT의 음성 채팅 핫키)라면 패드가 보내는 조합과 일치해야
하고, 아니면 키가 조용히 아무것도 안 합니다.

### 함정

- **데몬이 도는 동안 VIA 웹앱을 닫으세요** — WebHID가 패드를 독점하면 LED가 꺼집니다.
- **`pkill`로는 데몬이 안 죽습니다** — launchd `KeepAlive`가 되살립니다. `launchctl unload ~/Library/LaunchAgents/dev.micropad.agent.plist`를 쓰세요.
- **Codex는 CLI일 때만 점등됩니다.** ChatGPT 데스크탑 앱은 Electron이라 `~/.codex/config.toml`을 안 읽어 `notify`를 못 쏩니다.
- Codex Computer Use가 설치돼 있으면 시작 시 `config.toml`을 자기 우선으로 덮어쓰고 이 훅을 `--previous-notify`로 뒤에 물립니다. 정상 동작이며, `codex_notify.sh`는 보고만 하고 종료하므로 중복 발화하지 않습니다.
- **VIA 매크로 딜레이는 VIA 문서의 2바이트가 아니라 ASCII입니다.** 펌웨어는 매크로를 `send_string_with_delay_impl`로 재생하고, 그 `SS_DELAY` 분기는 숫자가 아닌 문자를 만날 때까지 십진수를 파싱한 뒤 그 종결자를 소비합니다 — QMK의 `SS_DELAY(n)`은 `"\1\4" #n "|"`입니다. `(lo-1)+(hi-1)*255` 바이트 쌍을 쓰면 딜레이만 무시되는 게 아니라 바이트 스트림이 어긋나 이후 전체가 쓰레기로 재생되므로, 타이밍이 안 맞는 게 아니라 매크로가 아예 죽은 것처럼 보입니다.
- **키맵을 쓴 뒤에는 되읽어 확인하세요.** 개별 쓰기는 즉시 반영되지만, `--apply` 전체 스윕이 뒷부분을 잃는 경우가 관측됐습니다 — 위쪽은 다 맞는데 하단 행만 전 레이어에서 옛 값으로 남아 있었습니다. `0x04`(키코드 조회)·`0x0E`(매크로 버퍼 조회)는 읽기 전용이라 몇 초면 판별됩니다. 읽기도 데몬이 장치를 독점하므로 먼저 unload 하세요.

### 라이선스

MIT — [LICENSE](LICENSE) 참고.
