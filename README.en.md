# Aero Hand RPS — Rock-Paper-Scissors with a Tendon-Driven Dexterous Hand

[中文说明](README.md) ｜ **English**

A rock-paper-scissors demo for an open-source tendon-driven dexterous hand
(**Aero Hand Open**, 16 joints / 7 bus servos, underactuated).
The camera reads *your* gesture, the robot **commits its move first** (using only match
history — it physically cannot see your current throw), then the real hand plays the pose
and talks trash in a chosen persona.

> This is second-layer work on top of Aero Hand Open: the hardware, firmware and sim are
> upstream; what lives here is the "make it runnable, demonstrable and verifiable" layer.

## What it does

| Feature | Notes |
|---|---|
| **RPS match** | camera gesture → strategy decision → real hand plays pose; 10 rounds per bout |
| **Gesture recognition (2 builds)** | RPS-only (3 classes, very stable) / full set (12 classes) |
| **Manual console** | 7-channel sliders, 17 measured poses, sequence playback, save/override |
| **Local HTTP API** | 24 endpoints, token auth, FIFO job queue |
| **Safety** | emergency stop (temp 52 °C / current 320×3 / flag file), release-on-exit, one-click stop-all |

## Quick start (play only, no robot)

```bash
# 1) install uv  (https://docs.astral.sh/uv/)
# 2) create the vision env — Python 3.12 is required (mediapipe has no 3.13 wheel)
uv venv --python 3.12 .venv312
uv pip install --python .venv312/bin/python -r requirements-vision.txt
# 3) make sure models/hand_landmarker.task exists (7.5 MB, not in requirements)
# 4) run scripts/30_rps_game.py --cam   (Windows: double-click the camera .bat)
```

## With the real hand (optional)

You need the physical Aero Hand Open, a 6 V supply, and the `agent/` + `sim2real/` folders
(both included). Run the self-check first, then the match entry point; `急停.bat` / the
stop-all script releases the hand immediately.

## Why it can't cheat

The decision signature is `decide(history, my_moves)` — **the current human throw is not a
parameter**, so it is not reachable by construction. Right after deciding, the move is
written to a lock file with a timestamp; only then is the human given the throw window.
Every round carries four timestamps `decide ≤ lock ≤ human ≤ reveal`, hard-asserted in code.

## How it is verified

| Item | Result |
|---|---|
| Contract tests (`tests/test_agent_dryrun.py`) | 32 assertions, run in CI on Linux + Windows, Python 3.12 / 3.13 |
| Real-machine match | 72 rounds, 4-timestamp ordering 72/72, gesture recognition 72/72, 0 timeouts |
| Pose library | 17 poses, all hand-measured, every channel inside `sim2real/limits.json` |

## Public numbers (please quote them this way)

- Poses: **17 system poses** (measured on the real hand)
- Gesture classes: **12**
- LLM: **cloud Flash + rule fallback** (no local model lane)
- Win rate: **72.4 % vs Hedge 64.9 %** head-to-head; **50 % is the ceiling against a random human**

## Layout

```
agent/          the only layer that touches the serial port (8 public APIs, 5 error codes)
scripts/rps/    match core: 4 rule strategies, 13 simulated humans, LLM chain, 5 personas
scripts/        game GUI, two gesture recognizers, manual console, HTTP API, safety, tools
sim2real/       pose library + safety limits
models/         MediaPipe hand landmarker
tests/          contract tests (dry-run, no hardware)
```

## Credits & licence

- Hardware / firmware / simulation: **Aero Hand Open** (follow its licence)
- This repository: **MIT** — see [LICENSE](LICENSE)
- Model: MediaPipe HandLandmarker (Google, Apache-2.0)

## Disclaimer

Demo-grade device: fingers move, servos pinch, and the mechanism can get hot.
Keep a human supervising whenever the hand is powered; read the operator manual before
first power-on.
