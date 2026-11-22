"""运动插值（motion 层）—— 只是 `scripts\\motion.py` 的转发，**不复制实现**。

★ 为什么这么写
    总指挥 2026-11-21 的规矩："`agent\\motion.py` 复用 `scripts\\motion.py` 的归一化 + min-jerk"。
    所以真正的数学仍只有一份（在 `scripts\\motion.py`）；这里按文件路径把它加载进来，
    再补两个 `agent` 层要用的函数（`plan` / `blend`），避免上层自己算插值。

★ 归一化进度是什么（一句话）
    每根手指各自定义 0 = 张开、1 = 目标，用**同一个**进度 f 驱动，于是所有手指同时出发、
    同时到达；行程长的手指自然走得快。

★ minimum-jerk
    f(τ) = 10τ³ − 15τ⁴ + 6τ⁵，两端速度和加速度都是 0 → 起步不猛、停住不顿。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HZ_DEFAULT = 25.0          # 与真机脚本一致：25 帧/秒
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    """按路径加载 scripts\\<name>.py（不污染 sys.path，也不复制代码）。"""
    path = _SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_aerohand_scripts_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SRC = _load("motion")

minjerk = _SRC.minjerk
smoothstep = _SRC.smoothstep
blend = _SRC.blend
plan_move_velocity = _SRC.plan_move_velocity


def plan(start, end, secs: float, hz: float = _HZ_DEFAULT):
    """生成一次移动的全部帧：[[7 个目标], ...]，最后一帧一定等于 end。

    secs<=0（瞬时，用户 2026-11-21 定的默认）→ 只返回 [end]，由上层立刻下发。
    """
    start = [int(v) for v in start]
    end = [int(v) for v in end]
    if secs is None or secs <= 0:
        return [end]
    steps = max(1, int(round(secs * hz)))
    frames = []
    for k in range(1, steps + 1):
        f = minjerk(k / steps)
        frames.append(blend(start, end, f))
    frames[-1] = end               # 防浮点/取整让最后一帧差 1
    return frames


def hz() -> float:
    return _HZ_DEFAULT
