"""姿势库自检：17 个姿势 × 7 通道是否都落在 sim2real/limits.json 内

为什么单独抽出来当 CI 步骤：`poses.json` 与 `limits.json` 是**安全相关文件**，
改姿势/改上限之后最容易出的错就是"某个通道越界"（真机会顶死、升温）。
这个检查不需要硬件、不需要网络，跑一次 0.1 秒。

用法：python tests/check_poses.py
退出码：0 = 全在范围内；1 = 有越界（CI 会红）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSES = ROOT / "sim2real" / "poses.json"
LIMITS = ROOT / "sim2real" / "limits.json"
N_CH = 7


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    poses = load(POSES)["poses"]
    limits = load(LIMITS)

    bad: list[str] = []
    for i in range(N_CH):
        ch = limits.get(str(i))
        if not ch or "lo" not in ch or "hi" not in ch:
            bad.append(f"limits 缺 ch{i} 的 lo/hi")

    for name, p in sorted(poses.items()):
        targets = p["targets"] if isinstance(p, dict) else p
        if len(targets) != N_CH:
            bad.append(f"{name}: 通道数 {len(targets)} ≠ {N_CH}")
            continue
        for i, v in enumerate(targets):
            lo, hi = limits[str(i)]["lo"], limits[str(i)]["hi"]
            if not (lo <= v <= hi):
                bad.append(f"{name}.ch{i}={v} 越界 [{lo}, {hi}]")

    print(f"姿势 {len(poses)} 个 × {N_CH} 通道，越界 {len(bad)} 项")
    for b in bad:
        print("  -", b)
    if not bad:
        # 顺带把最热/最冷通道数出来，方便人工扫一眼
        print("  全部落在 sim2real/limits.json 内 ✅")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
