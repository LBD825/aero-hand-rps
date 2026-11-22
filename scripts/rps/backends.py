"""RPS 决策后端（4 个规则策略）—— 从 `dexterous-hand-dist` 移植，**已修 clone bug**。

★ 为什么单开这个文件
    对局要"5 个策略供人挑"，其中 4 个不联网的规则策略来自外部仓库（MIT）。
    但那个仓库的 `clone()` 是坏的：`HedgePlayer` 的 7 个专家用**闭包**持有统计表，
    `copy.deepcopy` 只复制函数对象、不复制闭包 → 克隆体和母体共享同一张表。
    实测：复用同一实例跑多场，对 outcome1 的胜率从 **86.6% 掉到 50.7%**。

    本文件的修法：**专家写成类**（`Expert` 子类，状态在 `self` 上），于是 deepcopy 正常；
    并且每个后端都实现 `clone()`，明确重建内部状态。评测时**每场都 clone**，互不污染。

★ 统一接口（对局与评测共用）
    b = make_backend("hedge")
    move = b.decide(history, my_moves)       # history=对手历次出拳, my_moves=我方历次出拳
    b.observe(human_move, my_move, result)   # result 是**我方视角** +1 赢 / 0 平 / -1 输
    c = b.clone()                            # 状态独立的新副本

★ 四个后端
    random  纯随机（不看你）
    stats   出「你近 10 手主频」的克制拳
    fusion  6 路预测器 + 准确率 EMA 加权 + softmax 采样
    hedge   7 个专家 + Hedge 乘法权重（综合最强，实测 78%）
"""
from __future__ import annotations

import copy
import math
import random
from collections import Counter

MOVES = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}   # BEATS[x] = x 克制的拳
COUNTERS = {v: k for k, v in BEATS.items()}                          # COUNTERS[x] = 克制 x 的拳


def outcome(a: str, b: str) -> int:
    """a 对 b 的结果（a 视角）：+1 赢 / 0 平 / -1 输。"""
    if a == b:
        return 0
    return 1 if BEATS[a] == b else -1


def _opp_probs_ev(p):
    """给定对手出各拳的概率，算我出各拳的期望收益（赢+1 平0 输-1）。"""
    return {
        "rock": p["scissors"] - p["paper"],
        "paper": p["rock"] - p["scissors"],
        "scissors": p["paper"] - p["rock"],
    }


def _softmax_pick(ev, beta, rng=random):
    mx = max(ev.values())
    w = {m: math.exp(beta * (ev[m] - mx)) for m in MOVES}
    total = sum(w.values())
    r = rng.random() * total
    acc = 0.0
    for m in MOVES:
        acc += w[m]
        if r <= acc:
            return m
    return max(w, key=w.get)


class Backend:
    """所有后端的基类：只要求 decide / observe，clone 默认深拷贝（状态都在 self 上）。"""

    name = "?"

    def decide(self, history, my_moves=None) -> str:
        raise NotImplementedError

    def observe(self, human: str, mine: str, result: int) -> None:
        pass

    def clone(self) -> "Backend":
        return copy.deepcopy(self)


# ─────────────────────────── 1. 随机 ───────────────────────────

class RandomBackend(Backend):
    name = "随机"

    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def decide(self, history, my_moves=None):
        return self.rng.choice(MOVES)


# ─────────────────────────── 2. 统计（克主频） ───────────────────────────

class StatsBackend(Backend):
    name = "统计"

    def __init__(self, seed=None, window=10):
        self.rng = random.Random(seed)
        self.window = window

    def decide(self, history, my_moves=None):
        hist = (history or [])[-self.window:]
        if not hist:
            return self.rng.choice(MOVES)
        top = Counter(hist).most_common(1)[0][0]
        return COUNTERS[top]


# ─────────────────────────── 3. 融合（多预测器 + 采样） ───────────────────────────

class _AccuracyTracker:
    """在线追踪各路预测器的命中率（EMA），权重随准确率走。"""

    def __init__(self, decay=0.85):
        self.decay = decay
        self.ema = {}
        self.pending = None

    def update(self, history, preds):
        if self.pending is not None and history:
            actual = history[-1]
            for name, p in self.pending.items():
                hit = 1.0 if max(p, key=p.get) == actual else 0.0
                prev = self.ema.get(name, 0.34)
                self.ema[name] = self.decay * prev + (1 - self.decay) * hit
        self.pending = preds

    def weight(self, name, prior):
        e = max(self.ema.get(name, prior), 0.02)
        return (e ** 3) * prior


def _normalize(counter):
    total = sum(counter.values())
    return None if total <= 0 else {m: counter.get(m, 0) / total for m in MOVES}


def _pred_freq(hist, mine):
    return _normalize(Counter(hist[-20:]))


def _pred_recent(hist, mine):
    return None if len(hist) < 3 else _normalize(Counter(hist[-5:]))


def _pred_transition(hist, mine):
    if len(hist) < 4:
        return None
    last, c = hist[-1], Counter()
    for i in range(len(hist) - 1):
        if hist[i] == last:
            c[hist[i + 1]] += 1
    return _normalize(c)


def _pred_repeat(hist, mine):
    return None if not mine else {m: (1.0 if m == mine[-1] else 0.0) for m in MOVES}


def _pred_counter_me(hist, mine):
    if not mine:
        return None
    target = COUNTERS[mine[-1]]
    return {m: (1.0 if m == target else 0.0) for m in MOVES}


class FusionBackend(Backend):
    name = "融合"

    PRIORS = {"freq": 0.10, "recent": 0.25, "transition": 0.25,
              "repeat": 0.20, "counter_me": 0.20}
    PREDICTORS = {"freq": _pred_freq, "recent": _pred_recent,
                  "transition": _pred_transition, "repeat": _pred_repeat,
                  "counter_me": _pred_counter_me}

    def __init__(self, seed=None, beta=1.5):
        self.rng = random.Random(seed)
        self.beta = beta
        self.tracker = _AccuracyTracker()

    def decide(self, history, my_moves=None):
        hist = list(history or [])
        mine = list(my_moves or [])
        preds = {}
        for name, fn in self.PREDICTORS.items():
            try:
                p = fn(hist, mine)
            except Exception:
                p = None
            if p:
                preds[name] = p
        if not preds:
            return self.rng.choice(MOVES)

        self.tracker.update(hist, preds)          # 先更新准确率（用上轮预测 vs 本轮实际）
        fused, wsum = {m: 0.0 for m in MOVES}, 0.0
        for name, p in preds.items():
            w = self.tracker.weight(name, self.PRIORS[name])
            wsum += w
            for m in MOVES:
                fused[m] += w * p.get(m, 0.0)
        if wsum <= 0:
            return self.rng.choice(MOVES)
        fused = {m: v / wsum for m, v in fused.items()}
        return _softmax_pick(_opp_probs_ev(fused), self.beta, self.rng)


# ─────────────────────────── 4. Hedge7（7 专家 + 乘法权重） ───────────────────────────

class Expert:
    """专家基类：predict(hist) 给出「对手下一手」的 3 元分布；update(hist) 记账。

    hist 元素＝{'player': 对手出的, 'ai': 我出的, 'result': **对手视角**的结果}
    用类而不是闭包，是为了 deepcopy 能真正复制状态（这正是外部仓库的 bug 所在）。
    """
    name = "?"
    ALPHA = 0.6

    def predict(self, hist):
        return [1 / 3] * 3

    def update(self, hist):
        pass

    @staticmethod
    def _norm(counts, alpha=0.0):
        c = [x + alpha for x in counts]
        s = sum(c)
        return [1 / 3] * 3 if s <= 0 else [x / s for x in c]


class UniformExpert(Expert):
    name = "uniform"


class FavouriteExpert(Expert):
    """对手整体偏好（带 Laplace 平滑）。"""
    name = "favourite"

    def __init__(self):
        self.counts = [0, 0, 0]

    def predict(self, hist):
        return self._norm(self.counts, self.ALPHA)

    def update(self, hist):
        if hist:
            self.counts[MOVES.index(hist[-1]["player"])] += 1


class MarkovExpert(Expert):
    """k 阶马尔可夫：用最近 k 手的组合预测下一手。"""
    name = "markov"

    def __init__(self, k=1):
        self.k = k
        self.table = {}

    def _key(self, hist):
        return ">".join(r["player"] for r in hist[-self.k:])

    def predict(self, hist):
        if len(hist) < self.k:
            return [1 / 3] * 3
        return self._norm(self.table.get(self._key(hist), [0, 0, 0]), self.ALPHA)

    def update(self, hist):
        if len(hist) < self.k + 1:
            return
        ctx = ">".join(r["player"] for r in hist[-(self.k + 1):-1])
        self.table.setdefault(ctx, [0, 0, 0])[MOVES.index(hist[-1]["player"])] += 1


class OutcomeExpert(Expert):
    """win-stay：按「上一轮（结果, 对手出拳）」预测这一轮。"""
    name = "outcome"

    def __init__(self):
        self.table = {}

    @staticmethod
    def _key(r):
        return f"{r['result']}:{r['player']}"

    def predict(self, hist):
        if not hist:
            return [1 / 3] * 3
        return self._norm(self.table.get(self._key(hist[-1]), [0, 0, 0]), self.ALPHA)

    def update(self, hist):
        if len(hist) < 2:
            return
        self.table.setdefault(self._key(hist[-2]), [0, 0, 0])[MOVES.index(hist[-1]["player"])] += 1


class SecondGuessExpert(Expert):
    """二层博弈：对手在想「它会预期我出什么」——重复 / 克制自己 / 克制我。"""
    name = "second_guess"

    def __init__(self):
        self.counts = [0, 0, 0]

    def predict(self, hist):
        if not hist:
            return [1 / 3] * 3
        last = hist[-1]
        guesses = [last["player"], COUNTERS[last["player"]], COUNTERS[last["ai"]]]
        out = [0, 0, 0]
        for g, c in zip(guesses, self.counts):
            out[MOVES.index(g)] += c + self.ALPHA
        return self._norm(out)

    def update(self, hist):
        if len(hist) < 2:
            return
        prev, now = hist[-2], hist[-1]
        if now["player"] == prev["player"]:
            self.counts[0] += 1
        if now["player"] == COUNTERS[prev["player"]]:
            self.counts[1] += 1
        if now["player"] == COUNTERS[prev["ai"]]:
            self.counts[2] += 1


class HedgeBackend(Backend):
    """7 专家 + Hedge 乘法权重；出拳用 softmax 采样（不用纯 argmax，防被看穿）。"""
    name = "Hedge7"

    def __init__(self, seed=None, eta=0.55, beta=1.5):
        self.rng = random.Random(seed)
        self.eta, self.beta = eta, beta
        self.hist = []
        self.weights = [1.0] * 7
        self.experts = [UniformExpert(), FavouriteExpert(),
                        MarkovExpert(1), MarkovExpert(2), MarkovExpert(3),
                        OutcomeExpert(), SecondGuessExpert()]

    def _belief(self):
        w = Expert._norm(self.weights)
        mix = [0.0, 0.0, 0.0]
        for e, wi in zip(self.experts, w):
            p = e.predict(self.hist)
            for i in range(3):
                mix[i] += wi * p[i]
        return Expert._norm(mix)

    def decide(self, history, my_moves=None):
        belief = self._belief()
        spread = max(belief) - min(belief)
        if spread < 0.02:                      # 无信号不猜
            return MOVES[int(self.rng.random() * 3) % 3]
        ev = {m: sum(belief[i] * outcome(m, MOVES[i]) for i in range(3)) for m in MOVES}
        return _softmax_pick(ev, self.beta, self.rng)

    def observe(self, human, mine, result):
        losses = []
        for e in self.experts:
            p = e.predict(self.hist)           # 用观察前的预测算损失
            losses.append(-math.log(max(p[MOVES.index(human)], 1e-9)))
        self.hist.append({"player": human, "ai": mine, "result": -result})   # 存对手视角
        for e in self.experts:
            e.update(self.hist)
        for i, loss in enumerate(losses):
            self.weights[i] *= math.exp(-self.eta * loss)
        self.weights = Expert._norm(self.weights)


# ─────────────────────────── 注册表 ───────────────────────────

REGISTRY = {
    "random": (RandomBackend, "随机"),
    "stats": (StatsBackend, "统计（克主频）"),
    "fusion": (FusionBackend, "融合（多预测器+采样）"),
    "hedge": (HedgeBackend, "Hedge7（7专家+采样）"),
}


def make_backend(name: str, seed=None) -> Backend:
    if name not in REGISTRY:
        raise KeyError(f"未知后端 {name}，可选：{', '.join(REGISTRY)}")
    return REGISTRY[name][0](seed=seed)


def choices():
    """GUI 下拉用：[(id, 显示名), ...]"""
    return [(k, v[1]) for k, v in REGISTRY.items()]
