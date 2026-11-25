"""A（狄利克雷多专家）超参搜优

**为什么要搜**：A 的三个旋钮直接决定"信谁、信多少、忘多快"——
- `scale`：分数差放大倍数（越大越"赢者通吃"）
- `min_alpha`：每个专家的保底权重（越小越激进，越容易被噪声带跑）
- `decay`：老战绩的遗忘速度（越小越健忘，越大越固执）

**两阶段跑**（避免 36 组全跑）：
① 固定 `decay=0.995`，扫 `scale × min_alpha`（4×3=12 组）；
② 取①最优，再扫 `decay`（0.99 / 0.995 / 0.999）；
③ 用最优组合跑一次"大样本确认"（15000 轮/对手），和历史默认值对比。

每个组合打两个对手：**B（两阶段自适应）** 与 **Hedge7（项目现有）**；
每格 1000 轮 × 3 种子（探索/统计更快的初筛），指标＝A 的胜率（后 500 轮）。

用法：
    py scripts\\eval_kaggle_sweep.py            # 全流程（约 3 分钟）
    py scripts\\eval_kaggle_sweep.py --quick    # 只做阶段①
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import eval_kaggle as E                                     # noqa: E402
from rps.backends_kaggle import AdaptiveLearner, DirichletEnsemble  # noqa: E402


def make_b(seed):
    return AdaptiveLearner(seed=seed, explore_rounds=500)


def make_hedge(seed):
    return E.HedgeAdapter(seed=seed)


def eval_combo(params: dict, rounds: int, seeds: int, matches: int):
    """返回 (vs B 的胜率, vs Hedge 的胜率)，均为"后 500 轮"平均胜率。"""
    out = {}
    for name, mk in (("B", make_b), ("Hedge7", make_hedge)):
        rates = []
        for s in range(seeds):
            for m in range(matches):
                st = E.play_match(lambda sd, p=params: DirichletEnsemble(seed=sd, **p),
                                  mk, rounds, s * 100 + m)
                rates.append(E.rate(st["A"]["second"]))
        out[name] = statistics.mean(rates)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--rounds", type=int, default=1000)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--matches", type=int, default=1)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    t0 = time.time()
    print("阶段① 扫 scale × min_alpha（decay=0.995）")
    print(f"{'scale':>7}{'min_alpha':>11}{'vs B':>9}{'vs Hedge7':>11}{'均值':>8}")
    print("-" * 48)
    grid1 = []
    for scale in (0.5, 1.0, 2.0, 4.0):
        for ma in (0.01, 0.1, 1.0):
            p = {"scale": scale, "min_alpha": ma, "decay": 0.995}
            r = eval_combo(p, args.rounds, args.seeds, args.matches)
            avg = (r["B"] + r["Hedge7"]) / 2
            grid1.append((avg, scale, ma, r))
            print(f"{scale:>7.1f}{ma:>11.2f}{r['B']:>8.1f}%{r['Hedge7']:>10.1f}%{avg:>7.1f}%")
    best1 = max(grid1, key=lambda x: x[0])
    print(f"\n  → 阶段① 最优：scale={best1[1]}, min_alpha={best1[2]}（均值 {best1[0]:.1f}%）")

    if args.quick:
        print(f"\n耗时 {time.time() - t0:.1f}s（--quick 结束）")
        return 0

    print("\n阶段② 固定上面两个，扫 decay")
    print(f"{'decay':>8}{'vs B':>9}{'vs Hedge7':>11}{'均值':>8}")
    print("-" * 40)
    grid2 = []
    for decay in (0.99, 0.995, 0.999):
        p = {"scale": best1[1], "min_alpha": best1[2], "decay": decay}
        r = eval_combo(p, args.rounds, args.seeds, args.matches)
        avg = (r["B"] + r["Hedge7"]) / 2
        grid2.append((avg, decay, r))
        print(f"{decay:>8.3f}{r['B']:>8.1f}%{r['Hedge7']:>10.1f}%{avg:>7.1f}%")
    best2 = max(grid2, key=lambda x: x[0])
    best = {"scale": best1[1], "min_alpha": best1[2], "decay": best2[1]}
    print(f"\n  → 阶段② 最优：decay={best['decay']}（均值 {best2[0]:.1f}%）")

    print("\n阶段③ 大样本确认（15000 轮/对手：3 局 × 1000 轮 × 5 种子）")
    print(f"{'组合':<44}{'vs B':>9}{'vs Hedge7':>11}")
    print("-" * 66)
    rows = []
    for tag, p in (("默认 scale=1.0 min_alpha=0.1 decay=0.995",
                    {"scale": 1.0, "min_alpha": 0.1, "decay": 0.995}),
                   (f"搜优 scale={best['scale']} min_alpha={best['min_alpha']} decay={best['decay']}",
                    best)):
        r = eval_combo(p, 1000, 5, 3)
        rows.append((tag, r))
        print(f"{tag:<44}{r['B']:>8.1f}%{r['Hedge7']:>10.1f}%")

    out = ROOT / "产物" / ("RPS超参搜优-" + time.strftime("%Y%m%d-%H%M%S") + ".md")
    lines = [f"# A（狄利克雷多专家）超参搜优（{time.strftime('%Y-%m-%d %H:%M')}）", "",
             "初筛：1000 轮 × 3 种子 × 1 局；确认：3 局 × 1000 轮 × 5 种子。指标＝A 的后 500 轮胜率。", "",
             "## 阶段① scale × min_alpha（decay=0.995）", "",
             "| scale | min_alpha | vs B | vs Hedge7 | 均值 |", "|---|---|---|---|---|"]
    for avg, scale, ma, r in grid1:
        lines.append(f"| {scale} | {ma} | {r['B']:.1f}% | {r['Hedge7']:.1f}% | {avg:.1f}% |")
    if not args.quick:
        lines += ["", "## 阶段② decay", "", "| decay | vs B | vs Hedge7 | 均值 |", "|---|---|---|---|"]
        for avg, decay, r in grid2:
            lines.append(f"| {decay} | {r['B']:.1f}% | {r['Hedge7']:.1f}% | {avg:.1f}% |")
        lines += ["", "## 阶段③ 大样本确认", "", "| 组合 | vs B | vs Hedge7 |", "|---|---|---|"]
        for tag, r in rows:
            lines.append(f"| {tag} | {r['B']:.1f}% | {r['Hedge7']:.1f}% |")
    lines += ["", f"耗时 {time.time() - t0:.1f} 秒。"]
    out_dir = ROOT / "产物"
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
