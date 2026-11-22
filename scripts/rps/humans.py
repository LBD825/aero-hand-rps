"""人类行为模拟器（13 个）—— 移植自 `dexterous-hand-dist/arena.py`，用于**离线评估**。

★ 为什么要它
    "让 LLM 出拳比规则强"这句话，必须有数才成立。人不可能陪着打 200 轮，
    所以用认知科学文献里的人类行为模型当陪练：
      · RandomHuman  真随机（理论上限：任何策略对它都只有 50%）
      · WSLS         赢则保持、输则换拳（人类最强经验规律）
      · 0-ToM        Dirichlet 信念追踪 + 指数遗忘 + softmax
      · CR           条件响应（Wang et al.，社会循环的来源）
      · Copycat      模仿你上一手
      · Counter      克制你上一手
      · Brockbank×7  Brockbank & Vul 2024 的 7 种"算法对手" + 10% 噪声

★ 接口（与 backends.Backend 对称）
    h.decide(opp_history, my_history)      # opp=机器历次出拳, my=自己历次出拳
    h.observe(my_move, opp_move, result)   # result 是**自己视角** +1/0/-1

★ 注意
    这些是"模拟人"，不是真人。评估结论只能说明"对某类有规律的对手更强"，
    不能说明"能赢真人"。汇报时必须同时标"对随机人类＝50%"。
"""
from __future__ import annotations

import math
import random

MOVES = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
COUNTERS = {v: k for k, v in BEATS.items()}


def outcome(a: str, b: str) -> int:
    if a == b:
        return 0
    return 1 if BEATS[a] == b else -1


def _shift(move: str, k: int) -> str:
    return MOVES[(MOVES.index(move) + k) % 3]


def _sample(probs, r):
    r = r * sum(probs)
    acc = 0.0
    for i, p in enumerate(probs):
        acc += p
        if r <= acc:
            return i
    return len(probs) - 1


class Human:
    name = "?"

    def decide(self, opp, my) -> str:
        raise NotImplementedError

    def observe(self, my_move, opp_move, result):
        pass

    def clone(self):
        import copy
        return copy.deepcopy(self)


class RandomHuman(Human):
    name = "随机人"

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def decide(self, opp, my):
        return self.rng.choice(MOVES)


class WSLS(Human):
    name = "WSLS"

    def __init__(self, seed=None, win=(0.80, 0.10, 0.10),
                 lose=(0.10, 0.70, 0.20), draw=(0.40, 0.30, 0.30)):
        self.rng = random.Random(seed)
        self.theta = {1: win, -1: lose, 0: draw}
        self.last_move = None
        self.last_result = None

    def decide(self, opp, my):
        if self.last_move is None:
            return self.rng.choice(MOVES)
        k = _sample(self.theta[self.last_result], self.rng.random()) - 1
        return _shift(self.last_move, k)

    def observe(self, my_move, opp_move, result):
        self.last_move, self.last_result = my_move, result


class ZeroToM(Human):
    name = "0-ToM"

    def __init__(self, seed=None, log_sigma=0.0, log_beta=0.0):
        self.rng = random.Random(seed)
        self.rho = math.exp(-math.exp(log_sigma))
        self.beta = math.exp(log_beta)
        self.alpha = [1.0, 1.0, 1.0]

    def decide(self, opp, my):
        if opp:
            self.alpha = [self.rho * a + (1 - self.rho) for a in self.alpha]
        total = sum(self.alpha)
        p = {m: self.alpha[i] / total for i, m in enumerate(MOVES)}
        ev = {m: sum(p[u] * outcome(m, u) for u in MOVES) for m in MOVES}
        mx = max(ev.values())
        w = [math.exp(self.beta * (ev[m] - mx)) for m in MOVES]
        return MOVES[_sample(w, self.rng.random())]

    def observe(self, my_move, opp_move, result):
        self.alpha[MOVES.index(opp_move)] += 1


class ConditionalResponse(Human):
    name = "CR"

    def __init__(self, seed=None, w=(0.55, 0.25, 0.20),
                 t=(0.35, 0.35, 0.30), l=(0.20, 0.55, 0.25)):
        self.rng = random.Random(seed)
        self.gamma = {1: w, 0: t, -1: l}
        self.last_move = None
        self.last_result = None

    def decide(self, opp, my):
        if self.last_move is None:
            return self.rng.choice(MOVES)
        k = _sample(self.gamma[self.last_result], self.rng.random()) - 1
        return _shift(self.last_move, k)

    def observe(self, my_move, opp_move, result):
        self.last_move, self.last_result = my_move, result


class Copycat(Human):
    name = "模仿"

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def decide(self, opp, my):
        return opp[-1] if opp else self.rng.choice(MOVES)


class CounterHuman(Human):
    name = "反制"

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def decide(self, opp, my):
        return COUNTERS[opp[-1]] if opp else self.rng.choice(MOVES)


class BrockbankBot(Human):
    """Brockbank & Vul 2024 的 7 种算法对手（+10% 噪声）。"""
    KINDS = ("self+", "self-", "opp+", "opp0",
             "outcome1", "outcome2", "outcome_trans")

    def __init__(self, kind="self+", seed=None, noise=0.10):
        if kind not in self.KINDS:
            raise ValueError(f"未知 bot {kind}")
        self.kind = kind
        self.rng = random.Random(seed)
        self.noise = noise
        self.my_last = self.opp_last = None
        self.last_result = self.last_shift = None
        self.name = f"Brockbank-{kind}"

    def _rule(self):
        k = self.kind
        if k == "self+":
            return COUNTERS[self.my_last] if self.my_last else None
        if k == "self-":
            return BEATS[self.my_last] if self.my_last else None
        if k == "opp+":
            return COUNTERS[self.opp_last] if self.opp_last else None
        if k == "opp0":
            return self.opp_last
        if k == "outcome1":
            if self.my_last is None:
                return None
            return (self.my_last if self.last_result == 1 else
                    COUNTERS[self.my_last] if self.last_result == -1 else
                    BEATS[self.my_last])
        if k == "outcome2":
            if self.my_last is None:
                return None
            return (COUNTERS[self.my_last] if self.last_result == 1 else
                    BEATS[self.my_last] if self.last_result == -1 else
                    self.my_last)
        if self.my_last is None:
            return None
        if self.last_result == 1:
            return _shift(self.my_last, 0 if self.last_shift in (0, 1) else 1)
        if self.last_result == -1:
            return _shift(self.my_last, 1 if self.last_shift in (0, 1) else -1)
        return _shift(self.my_last, -1)

    def decide(self, opp, my):
        if self.rng.random() < self.noise:
            return self.rng.choice(MOVES)
        target = self._rule()
        return target if target in MOVES else self.rng.choice(MOVES)

    def observe(self, my_move, opp_move, result):
        if self.my_last is not None:
            self.last_shift = (MOVES.index(my_move) - MOVES.index(self.my_last)) % 3
        self.my_last, self.opp_last, self.last_result = my_move, opp_move, result


def make_human(kind: str, seed=None) -> Human:
    simple = {
        "random": RandomHuman, "wsls": WSLS, "ztom": ZeroToM, "cr": ConditionalResponse,
        "copycat": Copycat, "counter": CounterHuman,
    }
    if kind in BrockbankBot.KINDS:
        return BrockbankBot(kind, seed)
    if kind not in simple:
        raise KeyError(f"未知人类模型 {kind}")
    return simple[kind](seed=seed)


SIMPLE_KINDS = ["random", "wsls", "ztom", "cr", "copycat", "counter"]
ALL_KINDS = SIMPLE_KINDS + list(BrockbankBot.KINDS)


def match(robot, human, rounds=200, seed=0, human_seed_offset=7919):
    """跑 rounds 轮，返回机器人视角战绩。两边 view 对称，谁也不知道本轮对方出什么。

    ⚠ **两边种子必须错开**：若 robot 与 human 用同一个 seed，两边 PRNG 同步 →
    每轮都出同一手 → 全是平局（实测踩过：胜率 w/(w+l) 当 w+l=0 时被算成 0.0%，
    看起来像"随机策略被打爆"，其实一场没赢过也一场没输过）。所以这里给人类侧加偏移。
    """
    if hasattr(human, "rng") and human_seed_offset:
        human.rng = random.Random(random.Random(human_seed_offset).randrange(1 << 30))
    rng = random.Random(seed)
    robot, human = robot.clone(), human.clone()
    opp_of_robot, opp_of_human = [], []      # 各自看到的「对手历次出拳」
    my_r, my_h = [], []
    w = d = l = 0
    for _ in range(rounds):
        rm = robot.decide(opp_of_robot, my_r)
        hm = human.decide(opp_of_human, my_h)
        if rm not in MOVES or hm not in MOVES:
            continue
        r = outcome(rm, hm)
        if r == 1:
            w += 1
        elif r == -1:
            l += 1
        else:
            d += 1
        robot.observe(hm, rm, r)
        human.observe(hm, rm, -r)
        opp_of_robot.append(hm)
        opp_of_human.append(rm)
        my_r.append(rm)
        my_h.append(hm)
    decisive = w + l
    return {"wins": w, "draws": d, "losses": l,
            "win_rate": round(w / decisive, 4) if decisive else None,
            "draw_rate": round(d / max(1, w + d + l), 4)}
