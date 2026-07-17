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

The design has two axes — **hue = who**, **motion = what**:

| Agent  | Hue    |
|--------|--------|
| Claude Code | amber |
| Codex CLI   | blue  |

| State | Motion |
|-------|--------|
| working (thinking / coding) | breathing (coding breathes a little faster) |
| wants you (permission / notification) | 2 Hz blink |
| done | solid, then fades to idle after ~45 s |
| error | red⇄white strobe (white is what makes it read as an error, not as amber) |
| idle | barely lit |

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
git clone <this-repo> micropad-agent
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

## Optional: keymap & app routing

`via_keymap.py` and `layers.py` can rewrite the pad's keymap over the same VIA
protocol (with `--backup` / `--restore` for safety). `gen_karabiner.py` emits
Karabiner-Elements rules for per-app shortcut routing. **These encode one
person's app stack and shortcuts** — treat them as a worked example and edit the
tables to fit your own setup. They are not required for the LED feature.

## Gotchas

- **Close the VIA web app while the daemon runs** — WebHID takes exclusive access to the pad and the LEDs go dark.
- **`pkill` won't stop the daemon** — launchd `KeepAlive` restarts it. Use `launchctl unload ~/Library/LaunchAgents/dev.micropad.agent.plist`.
- **Codex lights up only from the CLI.** The ChatGPT desktop app is Electron and doesn't read `~/.codex/config.toml`, so it can't fire `notify`.
- If Codex Computer Use is installed, it rewrites `config.toml` on startup to put itself first and chains this hook behind its `--previous-notify` flag. That's expected — `codex_notify.sh` only reports and exits, so nothing double-fires.

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

두 개의 축 — **색조 = 누구**, **거동 = 무엇**:

| 에이전트 | 색조 |
|---------|------|
| Claude Code | 앰버(주황) |
| Codex CLI   | 파랑 |

| 상태 | 거동 |
|------|------|
| 작업 중 (사고 / 코딩) | 숨쉬기 (코딩이 조금 더 빠름) |
| 승인 대기 (권한 / 알림) | 2 Hz 깜빡임 |
| 완료 | solid → 약 45초 후 idle로 페이드 |
| 에러 | 빨강⇄하양 스트로브 (하양이 있어야 앰버 작업중과 구분됨) |
| 대기 | 아주 희미하게 점등 |

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
git clone <이-레포> micropad-agent
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

### (선택) 키맵 & 앱 라우팅

`via_keymap.py`·`layers.py`는 같은 VIA 프로토콜로 패드 키맵을 다시 쓸 수 있고
(`--backup` / `--restore`로 안전하게), `gen_karabiner.py`는 앱별 단축키 라우팅용
Karabiner-Elements 규칙을 생성합니다. **이 파일들은 특정 개인의 앱 스택·단축키를
담고 있으니** 예시로 보고 자기 환경에 맞게 표를 고쳐 쓰세요. LED 기능에는 필요
없습니다.

### 함정

- **데몬이 도는 동안 VIA 웹앱을 닫으세요** — WebHID가 패드를 독점하면 LED가 꺼집니다.
- **`pkill`로는 데몬이 안 죽습니다** — launchd `KeepAlive`가 되살립니다. `launchctl unload ~/Library/LaunchAgents/dev.micropad.agent.plist`를 쓰세요.
- **Codex는 CLI일 때만 점등됩니다.** ChatGPT 데스크탑 앱은 Electron이라 `~/.codex/config.toml`을 안 읽어 `notify`를 못 쏩니다.
- Codex Computer Use가 설치돼 있으면 시작 시 `config.toml`을 자기 우선으로 덮어쓰고 이 훅을 `--previous-notify`로 뒤에 물립니다. 정상 동작이며, `codex_notify.sh`는 보고만 하고 종료하므로 중복 발화하지 않습니다.

### 라이선스

MIT — [LICENSE](LICENSE) 참고.
