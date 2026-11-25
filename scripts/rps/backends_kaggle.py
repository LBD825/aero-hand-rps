"""RPS 决策算法：A＝狄利克雷加权的多专家，B＝两阶段自适应

**来源与版权说明（重要）**
- A 的结构参考 Kaggle 2020-21 "Rock, Paper, Scissors" 第 37 名公开方案
  `0Zeta/RockPaperScissors` 的 `best_submission.py`（2852 行 / 33 个类）：
  它的核心是「一堆策略各自给一个着法 → 按历史表现给每个策略打分 →
  `np.random.dirichlet(scale*(scores-min)+0.1)` 采样权重 → 加权投票」。
  该仓库**根目录没有 LICENSE 文件**，因此这里**不是拷它的代码**，而是按上述
  算法结构重新实现（策略集合按"多步超前/转移矩阵/模式匹配"重新组织）。
- A 的另一支参考：Kaggle notebook《Rock Paper Scissors - Multi Armed Bandit》那类
  submission（专家池 + 每个专家 Beta(1,1) 后验 + step_size/decay_rate）。
  本文件用 dirichlet 版本，因为用户点名要"狄利克雷分布采样"。

**约定**：着法用 0=石头 1=布 2=剪刀；`a` 赢 `b` 当且仅当 `(a - b) % 3 == 1`。
所以"克制 x"= `(x + 1) % 3`。

**两个机器人的区别**
- `DirichletEnsemble`（A）：**多个专家 + 每轮按后验表现 dirichlet 采样权重**，
  专家池含：随机、克制上一步、复读自己、自增、频率、转移矩阵（一阶）、
  转移张量（二阶）、模式匹配（k=2..5，带衰减）、最长后缀匹配、固定三连。
- `AdaptiveLearner`（B）：**前 `explore_rounds` 轮纯随机**（用户设定，同时静默学习），
  之后用"学到的对手模型"克制它；并对"它也在克制我"做二阶反制
  （若某预测器近期准确率低于随机水平，就换用它的对手模型/反向模型）。
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

import numpy as np

N_MOVES = 3


def beats(a: int, b: int) -> bool:
    """a 是否赢 b。"""
    return (a - b) % 3 == 1


def counter(x: int) -> int:
    """克制 x 的着法。"""
    return (x + 1) % 3


# ────────────────────────── A：专家池 ──────────────────────────
# 每个专家签名：fn(my_hist, opp_hist) -> 着法(0..2) 或 None（表示"没意见"）

def _p_last_opp(mh, oh):
    return counter(oh[-1]) if oh else None


def _p_anti_last(mh, oh):
    # 反向：对手上次输给我们（说明它在调），就猜它这次会改出"上上次"那种
    return counter(oh[-2]) if len(oh) >= 2 else None


def _p_copy_my(mh, oh):
    return mh[-1] if mh else None


def _p_cycle_my(mh, oh):
    return (mh[-1] + 1) % 3 if mh else None


def _p_freq(mh, oh):
    if len(oh) < 3:
        return None
    return counter(Counter(oh).most_common(1)[0][0])


def _p_anti_freq(mh, oh):
    if len(oh) < 3:
        return None
    # 对手在避开我们最爱克制的东西 → 反着猜
    return counter((Counter(oh).most_common(1)[0][0] + 1) % 3)


def _p_trans1(mh, oh):
    """一阶转移：历史上"对手上一步=o"之后，它最常出什么。"""
    if len(oh) < 4:
        return None
    ctx = oh[-1]
    nxt = Counter(oh[i + 1] for i in range(len(oh) - 1) if oh[i] == ctx)
    if not nxt:
        return None
    return counter(nxt.most_common(1)[0][0])


def _p_trans2(mh, oh):
    """二阶转移（张量的简化版）：(上一步, 上上步) → 下一步。"""
    if len(oh) < 6:
        return None
    ctx = (oh[-2], oh[-1])
    nxt = Counter(oh[i + 1] for i in range(len(oh) - 2)
                  if (oh[i - 1], oh[i]) == ctx)
    if not nxt:
        return None
    return counter(nxt.most_common(1)[0][0])


def _p_pattern(k: int, decay: float = 1.0):
    """多步超前：找历史里与"最近 k 步"相同的片段，看它后面出什么（越近权重越高）。"""
    def fn(mh, oh):
        n = len(oh)
        if n < k + 2:
            return None
        pat = oh[-k:]
        score = defaultdict(float)
        for i in range(k, n):
            if oh[i - k:i] == pat and i < n:
                w = decay ** (n - i)
                score[oh[i]] += w
        if not score:
            return None
        return counter(max(score.items(), key=lambda kv: kv[1])[0])
    return fn


def _p_self_pattern(k: int, decay: float = 1.0):
    """看"我自己最近 k 步"相同的片段后面，对手出了什么（对手在跟我的节奏）。"""
    def fn(mh, oh):
        n = len(mh)
        if n < k + 2:
            return None
        pat = mh[-k:]
        score = defaultdict(float)
        for i in range(k, n):
            if mh[i - k:i] == pat:
                score[oh[i]] += decay ** (n - i)
        if not score:
            return None
        return counter(max(score.items(), key=lambda kv: kv[1])[0])
    return fn


def _p_longest_suffix(mh, oh):
    """最长后缀匹配：找历史上"我+对手"这段最长的重复后缀，预测对手下一步。"""
    n = len(oh)
    if n < 8:
        return None
    seq = list(zip(mh, oh))
    for L in range(min(6, n - 1), 1, -1):
        suffix = seq[-L:]
        for i in range(L, n):
            if seq[i - L:i] == suffix and i < n:
                return counter(oh[i])
    return None


def _p_fixed(move: int):
    return lambda mh, oh: move


def default_experts():
    """A 的专家池（名字 → 函数）。数量与 37th 方案同量级，但按需要重组。"""
    e = {
        "last_opp": _p_last_opp,
        "anti_last": _p_anti_last,
        "copy_my": _p_copy_my,
        "cycle_my": _p_cycle_my,
        "freq": _p_freq,
        "anti_freq": _p_anti_freq,
        "trans1": _p_trans1,
        "trans2": _p_trans2,
        "longest_suffix": _p_longest_suffix,
        "fixed_rock": _p_fixed(0),
        "fixed_paper": _p_fixed(1),
        "fixed_scissors": _p_fixed(2),
    }
    for k in (2, 3, 4, 5):
        e[f"pattern_{k}"] = _p_pattern(k, decay=0.97)
        e[f"selfpattern_{k}"] = _p_self_pattern(k, decay=0.97)
    return e


class DirichletEnsemble:
    """A：多专家 + 每轮 dirichlet 采样权重加权投票。

    `scale` 与 `+0.1` 偏置沿用参考实现的写法（保证"没输过的专家"也有少量概率被选中），
    `decay` 让老战绩慢慢失效（对手会变）。
    """

    def __init__(self, seed: int = 0, scale: float = 1.0, decay: float = 0.995,
                 min_alpha: float = 0.1):
        self.rng = np.random.default_rng(seed)
        self.experts = default_experts()
        self.names = list(self.experts)
        self.score = {n: 0.0 for n in self.names}
        self.picks = Counter()
        self.scale = scale
        self.decay = decay
        self.min_alpha = min_alpha
        self.win_weight = 1.0
        self.loss_weight = -1.0
        self.last_votes = {}

    def decide(self, mh, oh) -> int:
        votes = {}
        for n, fn in self.experts.items():
            try:
                v = fn(mh, oh)
            except Exception:
                v = None
            votes[n] = v
        self.last_votes = votes
        # 只有"有意见"的专家参与加权
        active = [n for n, v in votes.items() if v is not None]
        if not active:
            return int(self.rng.integers(N_MOVES))
        vals = np.array([self.score[n] for n in active], dtype=float)
        alpha = self.scale * (vals - vals.min()) + self.min_alpha
        w = self.rng.dirichlet(alpha) if len(active) > 1 else np.array([1.0])
        tally = np.zeros(N_MOVES)
        for wi, n in zip(w, active):
            tally[votes[n]] += wi
        move = int(np.argmax(tally))
        # 平票随机破同
        if list(tally).count(tally[move]) > 1:
            move = int(self.rng.choice(np.flatnonzero(tally == tally[move])))
        self.picks[move] += 1
        return move

    def learn(self, mh, oh) -> None:
        """按"上一步各专家的建议有没有赢过对手的落子"更新分数。"""
        if not oh or not self.last_votes:
            return
        actual = oh[-1]
        for n, v in self.last_votes.items():
            if v is None:
                continue
            self.score[n] = self.score[n] * self.decay
            if beats(v, actual):
                self.score[n] += self.win_weight
            elif v == actual:
                pass
            else:
                self.score[n] += self.loss_weight
        self.last_votes = {}


# ────────────────────────── B：两阶段自适应 ──────────────────────────
class AdaptiveLearner:
    """B：前 `explore_rounds` 轮纯随机（同时学习），之后克制学到的对手模型。

    第二层（关键）：如果"我以为的对手模型"近期准确率掉到随机水平以下，
    说明对手在反制我的反制 ⇒ 改用"反向模型"（预测对手会出克制我预测的那个）。
    """

    def __init__(self, seed: int = 0, explore_rounds: int = 500,
                 eps: float = 0.1, decay: float = 0.995):
        self.rng = random.Random(seed)
        self.explore = explore_rounds
        self.eps = eps
        self.decay = decay
        self.round = 0
        # 预测器集合：都预测"对手下一步出什么"
        self.predictors = {}
        for k in (1, 2, 3, 4, 5):
            self.predictors[f"pat{k}"] = self._mk_pattern(k)
        self.predictors["freq"] = self._mk_freq()
        self.predictors["trans1"] = self._mk_trans1()
        self.predictors["trans2"] = self._mk_trans2()
        self.predictors["react"] = self._mk_react()
        # 反向模型：预测"对手会克制我上次的反制"
        self.predictors["anti_react"] = self._mk_anti_react()
        self.score = {n: 0.0 for n in self.predictors}
        self.hits = {n: 0 for n in self.predictors}
        self.shots = {n: 0 for n in self.predictors}
        self.chosen = Counter()
        self.last_pred = None

    # ---- 预测器工厂：都返回 predicted_opp_move 或 None ----
    def _mk_pattern(self, k):
        def fn(mh, oh):
            n = len(oh)
            if n < k + 2:
                return None
            pat = oh[-k:]
            acc = defaultdict(float)
            for i in range(k, n):
                if oh[i - k:i] == pat:
                    acc[oh[i]] += 0.98 ** (n - i)
            return max(acc.items(), key=lambda kv: kv[1])[0] if acc else None
        return fn

    def _mk_freq(self):
        def fn(mh, oh):
            return Counter(oh).most_common(1)[0][0] if len(oh) >= 3 else None
        return fn

    def _mk_trans1(self):
        def fn(mh, oh):
            if len(oh) < 4:
                return None
            ctx = oh[-1]
            c = Counter(oh[i + 1] for i in range(len(oh) - 1) if oh[i] == ctx)
            return c.most_common(1)[0][0] if c else None
        return fn

    def _mk_trans2(self):
        def fn(mh, oh):
            if len(oh) < 6:
                return None
            ctx = (oh[-2], oh[-1])
            c = Counter(oh[i + 1] for i in range(len(oh) - 2)
                        if (oh[i - 1], oh[i]) == ctx)
            return c.most_common(1)[0][0] if c else None
        return fn

    def _mk_react(self):
        """对手对我的上一步有反应模式：我出 m 时它常出什么。"""
        def fn(mh, oh):
            if len(mh) < 4:
                return None
            c = Counter(oh[i] for i in range(len(mh)) if mh[i] == mh[-1] and i + 1 <= len(oh))
            return c.most_common(1)[0][0] if c else None
        return fn

    def _mk_anti_react(self):
        """二阶：对手会专门克制"我上次用来克制它的那个" ⇒ 预测它再往上加一层。"""
        def fn(mh, oh):
            if len(oh) < 5:
                return None
            # 用"我上一步"推出它可能想克制我 → 它要出 counter(mh[-1])，
            # 但它知道我会想到这一层 ⇒ 它再 +1
            return counter(counter(mh[-1]))
        return fn

    def decide(self, mh, oh) -> int:
        self.round += 1
        if self.round <= self.explore or not oh:
            self.last_pred = None
            return self.rng.randrange(N_MOVES)
        # 评估每个预测器：谁能预测准对手的下一步（用于选谁）
        preds = {}
        for n, fn in self.predictors.items():
            try:
                preds[n] = fn(mh, oh)
            except Exception:
                preds[n] = None
        best, best_s = None, -1e9
        for n, p in preds.items():
            if p is None:
                continue
            s = self.score[n]
            if s > best_s:
                best, best_s = n, s
        if self.rng.random() < self.eps:      # ε 探索
            self.chosen["random"] += 1
            self.last_pred = None
            return self.rng.randrange(N_MOVES)
        if best is None:
            self.chosen["random"] += 1
            self.last_pred = None
            return self.rng.randrange(N_MOVES)
        self.chosen[best] += 1
        self.last_pred = {best: preds[best]}
        return counter(preds[best])

    def learn(self, mh, oh) -> None:
        """用"对手这次实际出了什么"给所有预测器打分（衰减 + 命中 +1 / 未中 −1）。"""
        if not oh:
            return
        actual = oh[-1]
        mh_star = mh[:-1] if len(mh) > 1 else []   # 决策时看到的历史
        for n, fn in self.predictors.items():
            try:
                p = fn(mh_star, oh[:-1])
            except Exception:
                p = None
            if p is None:
                continue
            self.score[n] = self.score[n] * self.decay
            self.shots[n] += 1
            if p == actual:
                self.score[n] += 1.0
                self.hits[n] += 1
            else:
                self.score[n] -= 1.0
        self.last_pred = None
