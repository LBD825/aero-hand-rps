"""mock 层（假手）—— **不碰串口、不碰硬件**，供智能体部离线开发。

★ 为什么先做它
    三线制里智能体部要写 GUI/语言层，但没有手就不能跑。总指挥 2026-11-21 批注：
    "建 agent\\mock.py（智能体部要用它离线开发；没有它智能体部就得等）"，所以这个文件
    是 P0 里**第一个**要落地的。

★ 它模拟了什么（够用就好）
    · `send(targets)` 记下目标，`pos()` 按"每次调用前进一点"的方式慢慢逼近目标
      （真实舵机也是 0.3~0.6 秒走完，所以刚发完读到半路位置是对的，不是 bug）；
    · 温度从 34°C 起、每次 `send` 涨一点点，能触发"变橙/变红"的界面分支；
    · 电流给小值（带符号），不会误触发 ≥320 的急停判据。

★ 怎么用它造"过热"场景
    `MockHand(heat=0.5)` 每次下发涨 0.5°C，几十次就到 50°C，用来测 TOO_HOT 分支。
"""
from __future__ import annotations

import time

N_CH = 7
MOCK_PORT = "MOCK"


class MockHand:
    """假手：接口与 `hal.RealHand` 一致（send / pos / curr / temp / port / close）。"""

    def __init__(self, start=None, heat: float = 0.05):
        from . import AgentError  # noqa: F401  （保持与 RealHand 相同的异常来源）

        self.port = MOCK_PORT
        self.heat = heat
        self.sent = []                      # 下发历史，便于测试断言
        self.closed = False
        self._pos = list(start or [25000, 300, 400, 150, 150, 150, 0])
        self._target = list(self._pos)
        self._temp = [34.0] * N_CH
        self._t0 = time.time()

    # ---------- 写 ----------
    def send(self, targets):
        if self.closed:
            raise RuntimeError("mock 已关闭")
        self._target = [int(v) & 0xFFFF for v in targets]
        self.sent.append(list(self._target))
        if self.heat:
            self._temp = [t + self.heat for t in self._temp]

    # ---------- 读 ----------
    def pos(self):
        """每次读都朝目标走 18%，最多 8 次就到（模拟 0.3~0.6 秒行程）。"""
        if self.closed:
            return None
        step = 0.18
        self._pos = [int(round(p + (t - p) * step)) for p, t in zip(self._pos, self._target)]
        return list(self._pos)

    def curr(self):
        if self.closed:
            return None
        gap = max(abs(p - t) for p, t in zip(self._pos, self._target))
        return [(-1) ** i * min(180, 20 + gap // 300) for i in range(N_CH)]

    def temp(self):
        return None if self.closed else [round(t, 1) for t in self._temp]

    def close(self):
        self.closed = True
