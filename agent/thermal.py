"""温度守护（thermal 层）—— 三层判据，与 `scripts\\thermal.py` 的阈值保持一致。

★ 判据（Aero Hand 实测，2026-11-21）
    < 45°C      ok
    ≥ 45°C      warn：界面变橙，继续跑
    ≥ 50°C      stop：立刻卸力，抛 AgentError("TOO_HOT")

★ 为什么不在 `agent` 里再定一套阈值
    阈值只能有一个真相源。这里与 `scripts\\thermal.py`（warn=45 / stop=50）和
    `scripts\\estop.py`（52°C 硬停）对齐：50 是"上层该收手"，52 是"后台线程直接断电"。

★ 读数全 0 的处理
    全 0 通常＝舵机没供电（不是冷），这时**不许当正常**：返回 'no_power'，由上层决定报错。
"""
from __future__ import annotations

TEMP_WARN = 45.0
TEMP_STOP = 50.0
TEMP_ESTOP = 52.0        # 与 scripts\estop.py 一致（后台线程用）


def check(temps):
    """返回 'ok' / 'warn' / 'stop' / 'no_power' / 'unknown'。"""
    if not temps:
        return "unknown"
    if max(temps) <= 0:
        return "no_power"
    if max(temps) >= TEMP_STOP:
        return "stop"
    if max(temps) >= TEMP_WARN:
        return "warn"
    return "ok"


def hottest(temps):
    """最热通道与温度：(通道号, 温度)；没有数据返回 (None, None)。"""
    if not temps:
        return None, None
    i = max(range(len(temps)), key=lambda k: temps[k])
    return i, temps[i]


def assert_ok(temps):
    """不达标就抛 AgentError('TOO_HOT')。温度正常返回 None。"""
    from . import AgentError

    state = check(temps)
    if state == "stop":
        ch, t = hottest(temps)
        raise AgentError("TOO_HOT", f"通道 {ch} 温度 {t}°C ≥ {TEMP_STOP}°C，已停止下发")
    if state == "no_power":
        raise AgentError("TOO_HOT", "温度读数全 0 —— 舵机可能没供电")
    return None
