"""运动插值核心：归一化进度 + minimum-jerk 速度曲线

为什么要有这一层（2026-11-21 用户提的问题："四指伸展时速度略微不同"）：
旧播放器是**按 u16 数值线性插值**——四根手指行程不一样（食指要 51850、中指 55408…），
同样 1.5 秒走完，走得远的看起来就快。改法有两条，业界都这么做：

  ① **归一化进度**（synchronized / normalized progress）
     每根手指定义 0=张开、1=目标姿势，用同一个 f 驱动：
         目标 = open + f × (目标 − open)
     这样所有关节**同时出发、同时到达**，速度按各自行程比例自动分配。
     这正是 ROS2 `joint_trajectory_controller` 做多关节同步的方式。

  ② **minimum-jerk 速度曲线**（机器人界标准的"最像人手"的过渡）
     f(τ) = 10τ³ − 15τ⁴ + 6τ⁵   （τ 从 0 到 1）
     两端的加速度为 0，所以起步/停住都不会"顿一下"；匀速（线性）会两头猛。

本模块只做数学，不碰串口——串口在调用方。
"""
from __future__ import annotations


def minjerk(tau: float) -> float:
    """minimum-jerk 进度曲线：τ∈[0,1] → f∈[0,1]，两端速度和加速度都为 0。"""
    tau = max(0.0, min(1.0, tau))
    return 10 * tau**3 - 15 * tau**4 + 6 * tau**5


def smoothstep(tau: float) -> float:
    """更便宜的平滑曲线（3τ²−2τ³），加速稍猛但计算简单，备用。"""
    tau = max(0.0, min(1.0, tau))
    return tau * tau * (3 - 2 * tau)


def blend(start, end, f: float):
    """按归一化进度 f 在 start→end 之间取值（f 已包含速度曲线）。"""
    return [int(round(s + (e - s) * f)) for s, e in zip(start, end)]


def plan_move(start, end, secs: float, hz: float = 25.0, curve=minjerk):
    """生成一次移动的帧序列：返回 [(f, targets), ...]，调用方负责发送与限速。"""
    steps = max(1, int(round(secs * hz)))
    out = []
    for k in range(1, steps + 1):
        f = curve(k / steps)
        out.append((f, blend(start, end, f)))
    return out


def plan_move_velocity(start, end, secs: float, hz: float = 25.0, curve=minjerk):
    """等"速度"展开：所有通道按同样的 u16/秒 移动，行程长的走得更久。

    为什么要有这个模式（2026-11-21 用户反馈"展开速度不一致"）：
      · 归一化进度（plan_move）让所有通道**同时到达**，但行程长的通道**线速度更快**，
        视觉上像"某几根在赶"；
      · 等速度模式让每根的 u16 变化率相同，视觉上"匀匀地一起散开"，
        代价是行程长的（如拇指 40000 计数）会比行程短的晚到。
    用法：展开时用等速度、握紧时用归一化（同时到达）。
    """
    steps = max(1, int(round(secs * hz)))
    dists = [abs(e - s) for s, e in zip(start, end)]
    dmax = max(dists) or 1
    out = []
    for k in range(1, steps + 1):
        f = curve(k / steps)
        # 每个通道按自身行程/最大行程缩放进度：行程短的先到，长的继续走
        targets = []
        for s0, e0, d in zip(start, end, dists):
            if d == 0:
                targets.append(int(e0))
                continue
            share = d / dmax
            fk = min(1.0, f / share) if share > 0 else 1.0
            targets.append(int(round(s0 + (e0 - s0) * fk)))
        out.append((f, targets))
    return out
