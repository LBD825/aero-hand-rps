"""建立 GitHub 公开发布用的干净仓库（stage_github.py）

**为什么单独做 staging**：项目里有 878 MB 杂物（`downloads/` 691 MB、日志、CAD、固件、第三方副本）。
直接 `git init` 会把它们一起提交，既超 GitHub 单文件 100 MB 上限，也会泄露内部信息。
所以只在 `产物\\github-repo\\` 里放**白名单文件**，在那儿 git init + commit + push。

用法：
    py scripts\\stage_github.py            # 生成/刷新 产物\\github-repo\\
    py scripts\\stage_github.py --check    # 只检查并打印清单
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "产物" / "github-repo"

# ── 白名单：要发布的（相对项目根）──
FILES = [
    # 真机底层
    "agent/__init__.py", "agent/hal.py", "agent/link.py",
    "agent/motion.py", "agent/thermal.py", "agent/mock.py",
    # 对局
    "scripts/30_rps_game.py",
    "scripts/rps/__init__.py", "scripts/rps/backends.py", "scripts/rps/flow.py",
    "scripts/rps/humans.py", "scripts/rps/llm.py", "scripts/rps/persona.py",
    "scripts/rps/README.md", "scripts/rps/策略库.md", "scripts/rps/策略库-精简.md",
    # 视觉识别（两版）
    "scripts/27_vision_gesture.py", "scripts/27b_vision_rps.py",
    # 控制入口与工具
    "scripts/24_gui_manual.py", "scripts/26_api.py", "scripts/00_launcher.py",
    "scripts/stop_all.py", "scripts/pack_dist.py", "scripts/precheck.py",
    "scripts/33_gesture_accuracy.py", "scripts/stats_rps.py",
    # 动作与安全
    "scripts/motion.py", "scripts/safe.py", "scripts/estop.py", "scripts/thermal.py",
    "scripts/porthelp.py", "scripts/14_monitor.py", "scripts/16_safe_init.py",
    "scripts/23_normalize_poses.py", "scripts/28_rerecord_poses.py",
    "scripts/29_audit_poses.py", "scripts/32_rps_m3_check.py",
    # 数据（不含 token / 历史 / lock）
    "sim2real/poses.json", "sim2real/poses_norm.json", "sim2real/limits.json",
    # 模型
    "models/hand_landmarker.task",
    # 双击入口
    "安装依赖.bat", "启动台.bat", "手动控制.bat", "启动API.bat",
    "启动对局.bat", "启动对局-摄像头.bat", "启动对局-真机.bat",
    "手势识别.bat", "手势识别-猜拳.bat",
    "急停.bat", "全关.bat", "上电自检.bat", "监控-温度.bat", "打包-给朋友.bat",
    "测试-手势准确率.bat", "手势识别-瞬时跟随.bat",
    # 依赖清单
    "requirements.txt", "requirements-vision.txt",
    # 文档
    "手册-操作-v1.md", "API说明.md", "M3-回填-20261121.md", "M4-验收记录-20261121.md",
]

# 目录整拷（含子文件）
DIRS = ["scripts/rps/persona"]

GITIGNORE = """# --- 不发布的：环境与产物 ---
.venv/
.venv312/
__pycache__/
*.pyc
产物/
_tmp/
logs/
downloads/

# 第三方副本（要的是引用，不是拷贝）
aero-open-sim/
mujoco_playground/
repo/

# 硬件资产（原项目许可范围，另行分发）
cad/
firmware/

# 运行期数据 / 密钥（绝不公开）
sim2real/.api_token
sim2real/rps_lock.json
sim2real/rps_history.json
sim2real/rps_history.bak-*.json
sim2real/ESTOP
sim2real/poses_user.json
*.log
monitor-live.csv
"""

README = """# Aero Hand RPS · 石头剪刀布人机对局

用一只**开源的腱驱动灵巧手**（Aero Hand Open，16 关节 / 7 舵机）跟人玩石头剪刀布：
摄像头认你的手势，机器人**先锁定出拳**（只根据历史，不看你这轮出什么），然后真手出拳、并带一个会说垃圾话的人设。

> 本项目是**二次开发**：硬件与固件来自 Aero Hand Open；这里的代码是"把它跑起来、能演示、能验收"的那一层。

## 它能做什么

- **猜拳对局**（核心）：摄像头认石头/剪刀/布 → 机器人按策略出拳 → 真机摆手势；一局 10 轮
- **手势识别**：两版 —— 猜拳专用（3 类，稳）/ 全量（12 类）
- **手动控制台**：7 通道滑块 + 17 个实测姿势 + 序列播放 + 保存/覆盖
- **本地 HTTP API**：24 个接口、token 鉴权、FIFO 队列，供别的程序驱动
- **安全**：急停（温度 52°C / 电流 320×3 / 标记文件三路）、退出即卸力、一键全关

## 快速开始（只玩对局，不接真手）

```bash
# 1) 装 uv（https://docs.astral.sh/uv/）
# 2) 建环境
uv venv --python 3.12 .venv312
uv pip install --python .venv312/Scripts/python.exe -r requirements-vision.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
# 3) 确认模型在 models/hand_landmarker.task
# 4) 双击 启动对局-摄像头.bat
```

> 视觉必须 **Python 3.12**（mediapipe 没有 3.13 的轮子）；模型文件不在 requirements 里，需随仓库一起拿。

## 接真手（可选）

需要 Aero Hand Open 实物 + 6V 电源 + `agent/` 与 `sim2real/`（本仓库已包含）。
先双击 `上电自检.bat` 看电流/温度，再 `启动对局-真机.bat`；出事就 `急停.bat` 或 `全关.bat`。

## 目录

| 路径 | 作用 |
|---|---|
| `scripts/rps/` | 对局内核：4 个规则策略、13 个模拟人、LLM 决策链、5 个人设、状态机 |
| `scripts/30_rps_game.py` | 对局 GUI（摄像头认人 + 真机出拳 + 四时间戳） |
| `scripts/27b_vision_rps.py` | 猜拳专用识别（3 类，中间态一律丢弃） |
| `scripts/27_vision_gesture.py` | 全量识别（12 类手势） |
| `agent/` | 唯一碰串口的薄壳（8 个公开 API + 5 个错误码） |
| `scripts/24_gui_manual.py` | 手动控制台 v3 |
| `scripts/26_api.py` | 本地 HTTP API（24 接口） |
| `sim2real/` | 姿势库（17 个真机实测值）+ 安全上下限 |
| `手册-操作-v1.md` | **给别人用的操作手册**（开箱 → 四个入口 → 故障表 → 换电脑四步） |

## 公平性怎么保证

机器人的决策函数签名是 `decide(history, my_moves)` —— **参数里没有"本轮人手"**，物理上拿不到；
出拳后立刻写入 `sim2real/rps_lock.json`（锁定时间戳），然后才轮到人出拳；
一轮内有四个时间戳 `decide ≤ lock ≤ human ≤ reveal`，代码里有 `assert` 硬校验。

## 对外口径（引用本项目时请带上）

- 姿势：**17 个系统姿势**（真机实测）
- 手势识别：**12 种**
- LLM：**云端 Flash + 规则兜底**（不走本地模型）
- 胜率：同条件 **72.4% vs Hedge 64.9%**；**对随机人类上限 50%**（不要宣传 100%）

## 致谢与许可

- 硬件/固件/仿真：**Aero Hand Open**（原项目，遵循其许可）
- 本仓库代码：**MIT**（见 `LICENSE`），欢迎照着手册搭一只
- 模型：MediaPipe HandLandmarker（Google，Apache-2.0）

## 免责

这是**演示级**装置：手指会动、会夹、有舵机，操作时请有人看着，别让小孩/宠物靠近；
上电前先读 `手册-操作-v1.md` 的四条红线。
"""

LICENSE = """MIT License

Copyright (c) 2026 Aero Hand RPS contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    missing = [f for f in FILES if not (ROOT / f).exists()]
    if missing:
        print("❌ 白名单里的文件缺失（不静默跳过）：")
        for m in missing:
            print("   -", m)
        return 2

    extra_dirs = []
    for d in DIRS:
        if (ROOT / d).is_dir():
            extra_dirs += [p for p in (ROOT / d).rglob("*") if p.is_file()]

    total = 0
    print(f"白名单 {len(FILES)} 个文件 + 目录 {len(DIRS)} 个（共 {len(extra_dirs)} 个附加文件）")
    for rel in FILES:
        total += (ROOT / rel).stat().st_size
    for p in extra_dirs:
        total += p.stat().st_size
    print(f"预计仓库体积：{total / 1024 / 1024:.1f} MB（含模型 7.5 MB）")

    if args.check:
        big = [(f, round((ROOT / f).stat().st_size / 1048576, 1))
               for f in FILES if (ROOT / f).stat().st_size > 50 * 1024 * 1024]
        print("超过 50 MB 的单文件：", big if big else "无")
        return 0

    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True, exist_ok=True)

    for rel in FILES:
        dst = STAGE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    for p in extra_dirs:
        rel = p.relative_to(ROOT)
        dst = STAGE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)

    (STAGE / "README.md").write_text(README, encoding="utf-8")
    (STAGE / "LICENSE").write_text(LICENSE, encoding="utf-8")
    (STAGE / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    n = sum(1 for _ in STAGE.rglob("*") if _.is_file())
    size = sum(_.stat().st_size for _ in STAGE.rglob("*") if _.is_file())
    print(f"✅ staging 完成：{STAGE}")
    print(f"   文件 {n} 个 / {size / 1024 / 1024:.1f} MB")
    print("   下一步：cd 进去 → git init → add → commit → 认证后 push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
