"""温度守护：动作脚本里定时读温度，超阈值先卸力再退出

为什么要它：舵机连续动作 30 秒就会到 46~47°C，而"顶住"的通道升温更快；
真烧了只能换舵机，所以让脚本自己盯着比自己盯屏更可靠。

三层判据（Aero Hand 实测值）：
  · >45°C  → 打印警告，继续跑
  · ≥50°C  → 立刻"卸力"（把目标设成当前位置，不再出力）+ 退出脚本
  · 读数全 0 → 舵机没供电（不是过热，直接报错退出）

用法（在动作循环里每 1 秒调一次）：
    from thermal import ThermalGuard
    guard = ThermalGuard(read_temp_fn)      # read_temp_fn 返回 7 个温度的 list
    ...
    state = guard.check()                   # 'ok' / 'warn' / 'stop'
    if state == 'stop':
        send(current_positions)             # 卸力
        raise SystemExit(guard.message)

设计要点：**不做任何"降温动作"**——最有效的降温就是停止出力，让它自己凉。
"""
from __future__ import annotations

import time


class ThermalGuard:
    def __init__(self, read_temp, warn=45, stop=50, interval=1.0, verbose=True):
        self.read_temp = read_temp       # 可调用：返回 7 个温度的 list（或 None）
        self.warn = warn
        self.stop = stop
        self.interval = interval
        self.verbose = verbose
        self.t_last = 0.0
        self.max_seen = 0
        self.message = ""

    def check(self, force: bool = False) -> str:
        now = time.time()
        if not force and (now - self.t_last) < self.interval:
            return "ok"
        self.t_last = now

        temps = self.read_temp()
        if not temps:
            return "ok"
        tmax = max(temps)
        self.max_seen = max(self.max_seen, tmax)

        if tmax == 0:                       # 全 0 = 没供电
            self.message = "温度读数为 0：舵机没供电（检查 6V）"
            return "stop"

        if tmax >= self.stop:
            hot = [i for i, t in enumerate(temps) if t >= self.stop]
            self.message = (f"温度 {tmax}°C 达到停机阈值 {self.stop}°C（通道 {hot}）——"
                            f"已卸力并退出，等它凉到 40°C 以下再跑")
            return "stop"

        if tmax >= self.warn and self.verbose:
            print(f"  ⚠ 温度 {tmax}°C 偏高（阈值 {self.warn}），注意不要连续跑太久")
        return "ok"
