"""姿势库归一化（**生成物工具**，2026-11-21 总指挥 Q5 裁决后重写）

★ 第 0 节 · 归属（照 Q5 裁决写的，别再用旧用法）
    · `poses.json` 是**唯一可编辑的真相源**；
    · `poses_norm.json` 是**生成物**：头部带 `"_generated": true`，**禁止手改**；
    · 任何脚本读姿势只读 `poses.json`；norm 只给"强度层"做内部换算；
    · `--check` 现在会**真的校验**：重算一遍与现有 norm 不一致就报错退出（exit 1）。

★ 第 1 节 · 归一化是什么
    换弹簧 / 改件 / 重绕线之后每个通道的 lo/hi 都会变，裸 u16 全部失效。
    归一化把它存成 0~1 的比例，真实值由 `limits.json` 现算：
        归一化 0 = 伸端（limits.lo）      归一化 1 = 屈端（limits.hi）
        u16 = lo + f × (hi − lo)
    以后只要更新 `limits.json`，所有姿势自动跟着缩放。

★ 第 2 节 · 三种用法
    py scripts\\23_normalize_poses.py --to-norm   # 由 poses.json 生成 poses_norm.json（覆盖前先备份）
    py scripts\\23_normalize_poses.py --check     # 校验：不一致就报错（CI/收工前跑这个）
    py scripts\\23_normalize_poses.py --table     # 只看对照表（旧 --check 的行为）
    py scripts\\23_normalize_poses.py --to-u16    # ⚠ 反向还原（会把 norm 写回 poses.json，一般不用）

★ 第 3 节 · 为什么 --check 要动文件以外的东西
    手改 norm 是"两份真相"的源头（总指挥质量红线第 4 条）。所以 --check 不只看误差，
    还比 `_generated` 标记：没有标记的文件直接判"这是手改的，重跑 --to-norm"。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
POSES = SIM / "poses.json"
NORMS = SIM / "poses_norm.json"
LIMITS = SIM / "limits.json"

N_CH = 7


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def load_limits():
    lim = load_json(LIMITS)
    lo = {ch: int(lim[str(ch)]["lo"]) for ch in range(N_CH)}
    hi = {ch: int(lim[str(ch)]["hi"]) for ch in range(N_CH)}
    return lo, hi


def build_norm(poses_doc, lo, hi):
    """由 poses.json 内容算出 norm 文档（纯函数，--to-norm 与 --check 共用）。"""
    def to_norm(v, ch):
        span = hi[ch] - lo[ch]
        return 0.0 if span == 0 else (v - lo[ch]) / span

    out = {}
    for name, pose in poses_doc["poses"].items():
        targets = pose.get("targets") or []
        if len(targets) != N_CH:
            raise SystemExit(f"姿势 {name} 的 targets 不是 {N_CH} 个：{targets}")
        out[name] = {
            "label": pose.get("label") or pose.get("name_zh") or name,
            "norm": [round(to_norm(int(v), ch), 4) for ch, v in enumerate(targets)],
            "note": pose.get("note", ""),
            "source_mtime": None,          # 由调用方补（避免纯函数掺 IO）
        }
    return out


def stamp(poses_doc, norm_poses, lo, hi):
    """补时间戳与来源统计，写成最终文档结构。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "_generated": True,                 # ★ 总指挥 Q5 要求：标了它就是生成物
        "_source": "poses.json",
        "_generated_at": now,
        "_note": "生成物，禁止手改。改姿势请改 poses.json，然后重跑 23 --to-norm",
        "_counts": {"poses": len(norm_poses),
                    "limits_sha": _limits_fingerprint(lo, hi),
                    "poses_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                                 time.localtime(POSES.stat().st_mtime))},
        "channel_order": "ch0..ch6",
        "poses": norm_poses,
    }


def _limits_fingerprint(lo, hi) -> str:
    return "|".join(f"{lo[i]}-{hi[i]}" for i in range(N_CH))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--to-norm", action="store_true", help="由 poses.json 生成 poses_norm.json")
    g.add_argument("--check", action="store_true", help="校验 norm 是否与 poses.json+limits 一致（不一致退出码 1）")
    g.add_argument("--table", action="store_true", help="只打印对照表（旧的 --check 行为）")
    g.add_argument("--to-u16", action="store_true", help="把 norm 还原写回 poses.json（⚠ 少用）")
    args = ap.parse_args()

    lo, hi = load_limits()
    poses_doc = load_json(POSES)

    if args.to_norm:
        norm_poses = build_norm(poses_doc, lo, hi)
        doc = stamp(poses_doc, norm_poses, lo, hi)
        if NORMS.exists():                                   # 覆盖前备份，便于对照
            bak = NORMS.with_suffix(".json.bak")
            bak.write_bytes(NORMS.read_bytes())
            print(f"（旧文件已备份到 {bak.name}）")
        NORMS.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 已生成 {NORMS.name}：{len(norm_poses)} 个姿势，_generated=true，"
              f"来源 poses.json（mtime {doc['_counts']['poses_mtime']}）")
        return

    if args.table:
        print(f"{'姿势':<16}{'u16（当前）':>34}{'归一化':>30}  往返误差")
        for name, pose in poses_doc["poses"].items():
            norm = [round((v - lo[ch]) / (hi[ch] - lo[ch] or 1), 3) for ch, v in enumerate(pose["targets"])]
            back = [int(round(lo[ch] + f * (hi[ch] - lo[ch]))) for ch, f in enumerate(norm)]
            diff = max(abs(a - b) for a, b in zip(pose["targets"], back))
            print(f"{name:<16}{str(pose['targets']):>34}{str(norm):>30}  {diff}")
        return

    if args.to_u16:
        n = load_json(NORMS)
        for name, item in n["poses"].items():
            if name in poses_doc["poses"]:
                poses_doc["poses"][name]["targets"] = [
                    int(round(lo[ch] + max(0.0, min(1.0, f)) * (hi[ch] - lo[ch])))
                    for ch, f in enumerate(item["norm"])]
                poses_doc["poses"][name]["note"] = item.get("note", "") + "｜由归一化按 limits 还原"
        POSES.write_text(json.dumps(poses_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"⚠ 已按当前 limits 还原写入 {POSES.name}（姿势数值已变，建议再跑 --to-norm 同步）")
        return

    # ---------- --check：真的校验 ----------
    if not NORMS.exists():
        print(f"❌ 缺少 {NORMS.name} —— 先跑 --to-norm", file=sys.stderr)
        raise SystemExit(1)
    cur = load_json(NORMS)
    problems = []
    if not cur.get("_generated"):
        problems.append(f"{NORMS.name} 没有 \"_generated\": true → 疑似手改过")
    fresh = build_norm(poses_doc, lo, hi)
    old = cur.get("poses", {})
    only_old = sorted(set(old) - set(fresh))
    only_new = sorted(set(fresh) - set(old))
    if only_old:
        problems.append(f"norm 里多出姿势（poses.json 已没有）：{only_old}")
    if only_new:
        problems.append(f"norm 里缺姿势（poses.json 有）：{only_new}")
    drift = []
    for name, item in fresh.items():
        if name not in old:
            continue
        a = old[name].get("norm") or []
        b = item["norm"]
        if len(a) != len(b):
            drift.append((name, f"长度 {len(a)} vs {len(b)}"))
            continue
        d = max((abs(x - y) for x, y in zip(a, b)), default=0.0)
        if d > 1e-4:
            drift.append((name, f"最大差 {d:.4f}"))
    if drift:
        problems.append("归一化值已漂移：" + "；".join(f"{n}（{why}）" for n, why in drift[:6]))
    old_lim = (cur.get("_counts") or {}).get("limits_sha")
    if old_lim and old_lim != _limits_fingerprint(lo, hi):
        problems.append(f"limits.json 已变（norm 记的是 {old_lim}）→ 需重跑 --to-norm")

    if problems:
        print("❌ 校验未通过：", file=sys.stderr)
        for p in problems:
            print("   · " + p, file=sys.stderr)
        print("   修法：py scripts\\23_normalize_poses.py --to-norm", file=sys.stderr)
        raise SystemExit(1)

    print(f"✅ 校验通过：{len(fresh)} 个姿势与 poses.json + limits.json 一致，"
          f"生成于 {cur.get('_generated_at')}")


if __name__ == "__main__":
    main()
