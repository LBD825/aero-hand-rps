"""RPS 算法强度对抗：A（狄利克雷多专家）vs B（两阶段自适应）

**为什么要分阶段统计**：用户设定 B 前 500 轮纯随机 ⇒ 对任何对手期望都是各 1/3，
前 500 轮净分≈0；**即使后 500 全胜，1000 轮总胜率也只有 ≈50%**。
所以主指标是**后 500 轮胜率**，前 500 单独报（应≈50%，用来确认"随机确实是随机"）。

用法：
    py scripts\\eval_kaggle.py                     # 默认 A vs B，3 局 × 1000 轮 × 5 种子
    py scripts\\eval_kaggle.py --rounds 1000 --matches 3 --seeds 5
    py scripts\\eval_kaggle.py --controls          # 另跑 A/B vs 纯随机、Hedge 作校准
输出：
    控制台表格 + 产物\\RPS算法对抗-<YYYYmmdd-HHMMSS>.md
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from rps.backends_kaggle import (AdaptiveLearner, DirichletEnsemble,  # noqa: E402
                                 beats, counter)

RPS_NAME = {0: "石头", 1: "布", 2: "剪刀"}


class RandomAgent:
    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def decide(self, mh, oh):
        return self.rng.randrange(3)

    def learn(self, mh, oh):
        pass


class HedgeAdapter:
    """把项目现有的 Hedge7（`scripts\\rps\\backends.py`）包成同一接口。

    Hedge7 是**有状态**后端：靠 `observe(human, mine, result)` 学权重，
    `decide()` 自己读内部历史（忽略传入的 history）。所以这里只做三件事：
    调 decide → 记下我出的拳 → 下一轮开始前用上一轮的真实结果喂 observe。
    """

    def __init__(self, seed=0):
        from rps.backends import MOVES, make_backend
        self._MOVES = MOVES
        self._idx = {m: i for i, m in enumerate(MOVES)}
        self.b = make_backend("hedge", seed=seed)
        self.last = None

    def decide(self, mh, oh):
        self.last = self.b.decide(None)
        return self._idx[self.last]

    def learn(self, mh, oh):
        if not oh or self.last is None:
            return
        human = self._MOVES[oh[-1]]
        mine = self.last
        mv_h, mv_m = oh[-1], self._idx[mine]
        result = 1 if beats(mv_m, mv_h) else (0 if mv_m == mv_h else -1)
        self.b.observe(human, mine, result)
        self.last = None


def play_match(maker_a, maker_b, rounds: int, seed: int):
    """跑一局，返回双方的分阶段战绩。

    ⚠ 两个智能体**必须用不同的随机种子**：第一版两边都传同一个 seed，
    结果 `RandomAgent` 与 `AdaptiveLearner` 的 random.Random 序列完全一样，
    "B vs 纯随机"那一格变成 500 平 0 胜 0 负（假数据）。这里给 B 加偏移。
    """
    a = maker_a(seed)
    b = maker_b(seed * 7919 + 104729)
    mh_a, oh_a = [], []      # A 视角：自己的历史 / 对手(B)的历史
    mh_b, oh_b = [], []
    stat = {
        "A": {"first": Counter(), "second": Counter(), "picks": Counter()},
        "B": {"first": Counter(), "second": Counter(), "picks": Counter()},
    }
    for r in range(1, rounds + 1):
        mv_a = a.decide(mh_a, oh_a)
        mv_b = b.decide(mh_b, oh_b)
        phase = "first" if r <= rounds // 2 else "second"
        if mv_a == mv_b:
            stat["A"][phase]["draw"] += 1
            stat["B"][phase]["draw"] += 1
        elif beats(mv_a, mv_b):
            stat["A"][phase]["win"] += 1
            stat["B"][phase]["loss"] += 1
        else:
            stat["A"][phase]["loss"] += 1
            stat["B"][phase]["win"] += 1
        stat["A"]["picks"][mv_a] += 1
        stat["B"]["picks"][mv_b] += 1
        # 各自追加历史（互为对手）
        mh_a.append(mv_a)
        oh_a.append(mv_b)
        mh_b.append(mv_b)
        oh_b.append(mv_a)
        a.learn(mh_a, oh_a)
        b.learn(mh_b, oh_b)
    return stat


def rate(c: Counter) -> float:
    n = sum(c.values())
    return (c["win"] / n * 100.0) if n else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1000)
    ap.add_argument("--matches", type=int, default=3, help="每个种子打几局")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--controls", action="store_true", help="额外跑 vs 纯随机 / Hedge 校准")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    maker_a = lambda s: DirichletEnsemble(seed=s)          # noqa: E731
    maker_b = lambda s: AdaptiveLearner(seed=s, explore_rounds=args.rounds // 2)  # noqa: E731
    maker_rand = lambda s: RandomAgent(seed=s)             # noqa: E731

    pairs = [("A(Dirichlet多专家)", maker_a, "B(两阶段自适应)", maker_b)]
    if args.controls:
        maker_hedge = lambda s: HedgeAdapter(seed=s)          # noqa: E731
        pairs += [("A(Dirichlet多专家)", maker_a, "纯随机", maker_rand),
                  ("B(两阶段自适应)", maker_b, "纯随机", maker_rand),
                  ("A(Dirichlet多专家)", maker_a, "Hedge7(项目现有)", maker_hedge),
                  ("B(两阶段自适应)", maker_b, "Hedge7(项目现有)", maker_hedge)]

    t0 = time.time()
    rows = []
    for name_x, mx, name_y, my in pairs:
        first_rates, second_rates, total = [], [], Counter()
        for seed in range(args.seeds):
            for _m in range(args.matches):
                st = play_match(mx, my, args.rounds, seed * 100 + _m)
                first_rates.append(rate(st["A"]["first"]))
                second_rates.append(rate(st["A"]["second"]))
                for k in ("win", "draw", "loss"):
                    total[k] += st["A"]["first"].get(k, 0) + st["A"]["second"].get(k, 0)
        n = args.seeds * args.matches
        tot = max(1, sum(total.values()))
        rows.append({
            "x": name_x, "y": name_y, "n": n,
            "first": statistics.mean(first_rates),
            "second": statistics.mean(second_rates),
            "second_sd": statistics.pstdev(second_rates) if n > 1 else 0.0,
            "total_rate": total["win"] / tot * 100.0,
            "draw_pct": total["draw"] / tot * 100.0,
            "loss_pct": total["loss"] / tot * 100.0,
            "win": total["win"], "draw": total["draw"], "loss": total["loss"],
        })

    print(f"每格 {args.matches} 局 × {args.rounds} 轮 × {args.seeds} 种子"
          f"（共 {args.seeds * args.matches * args.rounds} 轮/格），耗时 {time.time() - t0:.1f}s\n")
    print(f"{'对阵':<36}{'前500胜':>8}{'后500胜':>9}{'后500σ':>7}{'总胜':>7}{'平':>7}{'负':>7}")
    print("-" * 82)
    for r in rows:
        print(f"{r['x'] + ' vs ' + r['y']:<36}{r['first']:>7.1f}%{r['second']:>8.1f}%"
              f"{r['second_sd']:>7.1f}{r['total_rate']:>6.1f}%"
              f"{r['draw_pct']:>6.1f}%{r['loss_pct']:>6.1f}%")

    # 落盘
    out_dir = ROOT / "产物"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("RPS算法对抗-" + time.strftime("%Y%m%d-%H%M%S") + ".md")
    lines = [
        f"# RPS 算法强度对抗（{time.strftime('%Y-%m-%d %H:%M')}）", "",
        f"规模：每格 **{args.matches} 局 × {args.rounds} 轮 × {args.seeds} 种子**"
        f"（{args.seeds * args.matches * args.rounds} 轮）。",
        "",
        "> A＝狄利克雷加权的多专家（参考 Kaggle RPS 37th place 的算法结构，自行实现）；"
        "B＝前一半纯随机+静默学习、后一半克制学到的对手模型（含二阶反制）。",
        "> **主指标是「后 500 轮胜率」**：前 500 轮 B 纯随机，对任何对手期望各 1/3，"
        "所以总胜率上限≈50%，看总胜率说明不了算法强弱。",
        "",
        "| 对阵 | 前 500 胜率 | 后 500 胜率 | 后 500 标准差 | 总胜率 | 平 | 负 | 赢/平/负 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['x']} vs {r['y']} | {r['first']:.1f}% | **{r['second']:.1f}%** | "
                     f"{r['second_sd']:.1f} | {r['total_rate']:.1f}% | "
                     f"{r['draw_pct']:.1f}% | {r['loss_pct']:.1f}% | "
                     f"{r['win']}/{r['draw']}/{r['loss']} |")
    lines += ["", f"耗时 {time.time() - t0:.1f} 秒。"]
    out.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
