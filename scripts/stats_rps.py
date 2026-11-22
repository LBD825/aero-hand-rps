"""真人对局胜率统计（stats_rps.py）——从已落盘的 rps_history.json 与日志重建

**为什么单独写脚本**：用户要"真人对局胜率统计"，数据已经在 `sim2real\rps_history.json`
（每轮都有结果、通道、降级、四时间戳）里，不需要他再打一局。按他的习惯：
**先翻已有数据，能重建就先重建**。

用法：
    py scripts\\stats_rps.py              # 汇总全部
    py scripts\\stats_rps.py --detail     # 附带逐轮明细
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "sim2real" / "rps_history.json"


def load() -> dict:
    return json.loads(HIST.read_text(encoding="utf-8-sig"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true", help="打印逐轮明细")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not HIST.exists():
        print("没有 rps_history.json")
        return 1
    doc = load()
    rounds = doc.get("rounds") or []
    if not rounds:
        print("rps_history.json 里没有 rounds 记录")
        return 1

    print(f"数据：{HIST.name}　更新于 {doc.get('updated', '?')}")
    print(f"局次：{len(rounds)} 轮\n")

    # 只统计真人对局（有 human 字段且不是模拟人标记）
    real = [r for r in rounds if r.get("human") in ("rock", "scissors", "paper")]
    print(f"真人对局轮数：{len(real)}")
    if not real:
        return 0

    res = Counter()
    for r in real:
        h, b = r.get("human"), r.get("robot")
        if h == b:
            res["draw"] += 1
        elif (h, b) in (("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")):
            res["human"] += 1
        else:
            res["robot"] += 1
    n = len(real)
    print(f"\n【胜负】人赢 {res['human']}（{res['human'] / n * 100:.1f}%）"
          f"　机赢 {res['robot']}（{res['robot'] / n * 100:.1f}%）"
          f"　平 {res['draw']}（{res['draw'] / n * 100:.1f}%）")
    print("  注：平局单独算；若只看非平局，人赢占 "
          f"{res['human'] / max(1, res['human'] + res['robot']) * 100:.1f}%，"
          f"机赢占 {res['robot'] / max(1, res['human'] + res['robot']) * 100:.1f}%")

    # 出拳分布
    for who, key in (("人", "human"), ("机", "robot")):
        c = Counter(r.get(key) for r in real)
        top = ", ".join(f"{k} {v}（{v / n * 100:.0f}%）" for k, v in c.most_common())
        print(f"\n【{who}出拳分布】{top}")

    # 通道 / 降级 / 时间戳
    ch = Counter(r.get("channel") or "?" for r in real)
    print(f"\n【决策通道】" + ", ".join(f"{k} {v}" for k, v in ch.most_common()))
    degraded = sum(1 for r in real if r.get("degraded"))
    print(f"【降级】{degraded} 次（{degraded / n * 100:.1f}%）")
    ok = sum(1 for r in real if r.get("order_ok"))
    print(f"【四时间戳有序】{ok}/{n}（{ok / n * 100:.1f}%）")

    # 延迟
    lat = [r.get("latency") for r in real if isinstance(r.get("latency"), (int, float))]
    if lat:
        print(f"【决策延迟】中位 {statistics.median(lat):.2f}s　最长 {max(lat):.2f}s")

    # 按局次（bout）分组
    bouts = {}
    for r in real:
        bouts.setdefault(r.get("bout") or 0, []).append(r)
    print(f"\n【按局次】共 {len(bouts)} 局：")
    for b, rs in sorted(bouts.items()):
        hw = sum(1 for r in rs if r.get("human") != r.get("robot")
                 and (r.get("human"), r.get("robot")) in
                 (("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")))
        rw = sum(1 for r in rs if r.get("human") != r.get("robot")) - hw
        dr = len(rs) - hw - rw
        print(f"    第 {b} 局：{len(rs)} 轮　人 {hw} / 机 {rw} / 平 {dr}")

    if args.detail:
        print("\n【逐轮】")
        for i, r in enumerate(real, 1):
            print(f"  {i:>2}. 人={r.get('human'):<9} 机={r.get('robot'):<9} "
                  f"通道={r.get('channel') or '?':<6} 降级={bool(r.get('degraded'))} "
                  f"at={r.get('at', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
