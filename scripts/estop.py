"""统一急停（E-Stop）：一个后台线程盯三件事，任一触发就卸力并硬退出

为什么要它（2026-11-21 用户要求"所有的脚本都要有紧急停止"）：
· 之前的 SafeGuard 只在**脚本自己退出**时卸力，管不了"脚本还在跑但手在烧"；
· 这一天真实事故两次：中断后 ch4 卡住烧到 54°C、ch0 顶到止挡烧到 62°C。

触发条件（任一满足）：
  ① **外部急停文件** `sim2real\\ESTOP` 存在 —— 双击 `急停.bat` 或任何程序写这个文件都算；
  ② **任何通道温度 ≥ 52°C**；
  ③ **任何通道 |电流| ≥ 320 连续 3 次采样**（约 0.6 秒，排除瞬时冲击）。

触发动作（顺序固定）：
  read_pos() → send(该位置)   ← 目标=当前，误差 0，舵机卸力
  → 打印原因 → `os._exit(0)` 硬退出（保证动作循环立刻停）

用法（每个动作脚本连上串口后调用一次）：
    from estop import install_estop
    install_estop(send=send, read_pos=lambda: q(GET_POS),
                  read_curr=lambda: q(GET_CURR), read_temp=lambda: q(GET_TEMP))

    # 也可以手动触发：trigger_estop("手动急停")
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLAG = ROOT / "sim2real" / "ESTOP"
TEMP_LIMIT = 52
CURR_LIMIT = 320
CURR_STRIKES = 3
POLL = 0.2

_state = {"send": None, "read_pos": None, "read_curr": None, "read_temp": None,
          "thread": None, "fired": False}


def trigger_estop(reason: str = "手动急停") -> None:
    """外部写标记文件，让所有正在跑的脚本立刻卸力。"""
    try:
        FLAG.parent.mkdir(parents=True, exist_ok=True)
        FLAG.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {reason}\n", encoding="utf-8")
        print(f"[急停] 已写标记文件 {FLAG}", flush=True)
    except Exception as e:
        print(f"[急停] 写标记失败：{e}", file=sys.stderr)


def clear_estop() -> None:
    try:
        FLAG.unlink(missing_ok=True)
        print("[急停] 标记已清除", flush=True)
    except Exception:
        pass


def _fire(reason: str) -> None:
    if _state["fired"]:
        return
    _state["fired"] = True
    print(f"\n🛑 [急停] {reason}", flush=True)
    try:
        pos = _state["read_pos"]() if _state["read_pos"] else None
        if pos and _state["send"]:
            _state["send"](list(pos))          # 目标=当前位置 → 卸力
            print(f"[急停] 已卸力（目标=当前位置 {pos}）", flush=True)
    except Exception as e:
        print(f"[急停] 卸力失败：{e}（请手动拔 6V）", file=sys.stderr)
    time.sleep(0.2)
    os._exit(0)                                 # 硬退出，保证动作循环停住


def _loop() -> None:
    strikes = 0
    while True:
        time.sleep(POLL)
        # ① 外部标记
        if FLAG.exists():
            try:
                msg = FLAG.read_text(encoding="utf-8").strip()
            except Exception:
                msg = ""
            _fire(f"收到急停标记（{msg or '无说明'}）")
        # ② 温度 / ③ 电流
        try:
            t = _state["read_temp"]() if _state["read_temp"] else None
            if t and max(t) >= TEMP_LIMIT:
                _fire(f"温度 {max(t)}°C ≥ {TEMP_LIMIT}°C")
            c = _state["read_curr"]() if _state["read_curr"] else None
            if c:
                cc = [x - 65536 if x >= 32768 else x for x in c]
                if max(abs(x) for x in cc) >= CURR_LIMIT:
                    strikes += 1
                    if strikes >= CURR_STRIKES:
                        _fire(f"电流 {max(cc, key=abs)} ≥ {CURR_LIMIT} 连续 {strikes} 次")
                else:
                    strikes = 0
        except Exception:
            pass


def install_estop(send, read_pos, read_curr=None, read_temp=None) -> None:
    """在主脚本里调用一次；返回后可继续正常工作。"""
    if _state["thread"] is not None:
        return
    _state.update(send=send, read_pos=read_pos, read_curr=read_curr, read_temp=read_temp)
    if FLAG.exists():                       # 启动时就带着标记 → 说明上次没清，先清掉
        try:
            FLAG.unlink()
        except Exception:
            pass
    th = threading.Thread(target=_loop, daemon=True, name="estop")
    th.start()
    _state["thread"] = th
    print(f"[急停] 已启用：温度 ≥{TEMP_LIMIT}°C / 电流 ≥{CURR_LIMIT}×{CURR_STRIKES} / 标记文件 {FLAG.name}", flush=True)
