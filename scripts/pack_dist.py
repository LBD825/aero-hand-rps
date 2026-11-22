"""打包给朋友（pack_dist.py）—— 按 A/B 两档把 RPS 对局拷成可分发包

**为什么需要**（总指挥 2026-11-22 派工单）：对方要能自己跑起来，但不该拿走我们的
虚拟环境（1.6 GB、路径写死）、日志、密钥。所以做成"按清单拷 + 缺一个就报错"。

两档：
  A 档：只玩对局（不接真手），约 12 MB
  B 档：A + 接真手所需（agent\\ + sim2real\\ + 真机 bat）

铁律：
  · 源文件**缺一个就报名字并退出**（不许静默跳过）；
  · **绝不打包** `.venv` / `.venv312` / `logs\\` / `*.log` / `monitor-live.csv`
    / `rps_history.json` / `.api_token` / `rps_lock.json` / `ESTOP`；
  · 打完**打印清单 + 总大小**；
  · 生成的 bat 按档位重写参数（A 档不带 `--live`，B 档带）。

用法：
    py scripts\\pack_dist.py --tier A
    py scripts\\pack_dist.py --tier B
输出：`产物\\RPS分发-<YYYYMMDD>-<A|B>\\`
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "产物"

# A 档清单（相对项目根）
TIER_A = [
    "scripts/30_rps_game.py",
    "models/hand_landmarker.task",
    "requirements.txt",
    "requirements-vision.txt",
    "安装依赖.bat",
    "启动对局.bat",
    "手册-操作-v1.md",
]
TIER_A_DIRS = ["scripts/rps", "scripts/27_vision_gesture.py"]

# B 档在 A 之上追加
TIER_B_EXTRA = [
    "启动对局-真机.bat",
    "急停.bat",
    "上电自检.bat",
]
TIER_B_DIRS = ["agent"]
TIER_B_SIM = ["poses.json", "poses_user.json", "limits.json"]

# 绝不进包的（目录名/文件名/后缀）
BANNED_DIRS = {".venv", ".venv312", "logs", "__pycache__", ".git", "产物", "cad", "firmware"}
BANNED_NAMES = {"monitor-live.csv", "rps_history.json", ".api_token", "rps_lock.json", "ESTOP"}
BANNED_SUFFIX = {".log", ".pyc", ".bak"}


def bad(path: Path) -> bool:
    if any(part in BANNED_DIRS for part in path.parts):
        return True
    if path.name in BANNED_NAMES:
        return True
    if path.suffix.lower() in BANNED_SUFFIX:
        return True
    if path.name.startswith("rps_history.bak-"):
        return True
    return False


def collect(tier: str) -> list[Path]:
    items: list[Path] = []
    for rel in TIER_A + (TIER_B_EXTRA if tier == "B" else []):
        items.append(ROOT / rel)
    for rel in TIER_A_DIRS + (TIER_B_DIRS if tier == "B" else []):
        p = ROOT / rel
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    items.append(f)
        else:
            items.append(p)
    if tier == "B":
        for name in TIER_B_SIM:
            items.append(ROOT / "sim2real" / name)

    out, missing = [], []
    for p in items:
        if not p.exists():
            missing.append(p.relative_to(ROOT).as_posix())
        elif not bad(p.relative_to(ROOT)):
            out.append(p)
    if missing:
        print("❌ 缺少源文件，已中止（不静默跳过）：", flush=True)
        for m in missing:
            print(f"   - {m}", flush=True)
        sys.exit(2)
    return out


CAM_BAT_A = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
title RPS - camera (no hand, dry-run)

if not exist ".venv312\\Scripts\\python.exe" (
  echo [ERROR] .venv312 not found. Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   RPS with CAMERA  -^>  dry-run (no real hand on this PC)
echo   click NEXT ROUND, show rock / scissors / paper
echo   keyboard fallback: press 1 / 2 / 3 in the game window
echo ============================================================
".venv312\\Scripts\\python.exe" scripts\\30_rps_game.py --cam --persona feiyu --rounds-per-bout 10 %*
pause
"""

CAM_BAT_B = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
title RPS - camera + LIVE HAND

if not exist ".venv312\\Scripts\\python.exe" (
  echo [ERROR] .venv312 not found. Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   RPS with CAMERA + REAL HAND  (THE HAND WILL MOVE)
echo   before you start: close the temperature monitor
echo   Esc / the stop bat = emergency release
echo ============================================================
".venv312\\Scripts\\python.exe" scripts\\30_rps_game.py --cam --live --persona feiyu --rounds-per-bout 10 %*
pause
"""

README = """# RPS 分发包（{tier} 档）

> 生成时间：{when}　｜　档位：**{tier}**（{tier_desc}）

## 这个包里有什么

{manifest}

## 对方第一步（四步）

1. 装 **`uv`**（`https://docs.astral.sh/uv/`，只要一次）
2. 双击 **`安装依赖.bat`** —— 自动建 `.venv`（3.13）+ `.venv312`（**必须 3.12**，mediapipe 没有 3.13 轮子），约 5~10 分钟、需联网
3. **手动确认 `models\\hand_landmarker.task` 在**（7.5 MB，它不在 requirements 里，最容易漏）
4. 双击 **`启动对局-摄像头.bat`**（无摄像头就双击 `启动对局.bat`，用键盘 1/2/3 出拳）

## 两档区别

| 档 | 能干什么 | 需要的硬件 |
|---|---|---|
| **A** | 摄像头认人 + 电脑里那只"假手"出拳（纯软件，**不动真机**） | 摄像头（没有就用键盘） |
| **B** | A + **接真手**出拳（COM 口、6V 电源、急停） | 摄像头 + Aero Hand 真机 |

**没有打包**：`.venv` / `.venv312`（1.6 GB、路径写死）、日志、`rps_history.json`、`.api_token`。
"""

FRIEND = """这几样按顺序来就行，卡住随时喊我。

1) 先装 uv（一个 Python 环境管理工具，装一次以后都能用），装完能用就行。
2) 双击包里的"安装依赖"那个 bat，它会自动建两个环境装依赖，大概五到十分钟，要联网。
3) 装完确认 models 文件夹里的 hand_landmarker.task 还在，这个文件不在依赖里，最容易漏。
4) 双击"启动对局-摄像头"那个 bat 就能玩，摄像头认你的手势，电脑那只假手会回应。
   没摄像头就双击"启动对局"那个，键盘按 1 石头 2 剪刀 3 布，一样能玩。
5) 对外说成绩的时候请带上这句：对随机人类最多五成胜率，别讲成百分百。

有问题把报错截图发我，我这边能复现就能修。
"""


def write_text(p: Path, text: str, bom: bool) -> None:
    """统一 UTF-8（可选 BOM）+ CRLF。"""
    body = text.replace("\r\n", "\n").replace("\n", "\r\n")
    p.write_text(body, encoding="utf-8-sig" if bom else "utf-8", newline="")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["A", "B"], required=True)
    ap.add_argument("--date", default=None, help="覆盖日期（默认今天）")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    tier = args.tier
    date = args.date or time.strftime("%Y%m%d")
    # 目录带档位：A/B 同一天各打一次不会互相覆盖（实测：只带日期时 B 把 A 冲掉了）
    out = OUT_ROOT / f"RPS分发-{date}-{tier}"
    if out.exists():
        shutil.rmtree(out)                      # 同日期重打就覆盖，避免两份混一起
    out.mkdir(parents=True, exist_ok=True)

    files = collect(tier)
    total = 0
    manifest_lines = []
    for src in files:
        rel = src.relative_to(ROOT)
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        size = dst.stat().st_size
        total += size
        manifest_lines.append(f"- `{rel.as_posix()}`　{size / 1024:.0f} KB")

    # 按档位重写摄像头 bat（A 档不带 --live）
    write_text(out / "启动对局-摄像头.bat",
               CAM_BAT_A if tier == "A" else CAM_BAT_B, bom=False)
    # 手动改过的 bat 也要 CRLF（上面 write_text 已处理）

    write_text(out / "发给朋友的话.txt", FRIEND, bom=True)
    write_text(out / "README-分发.md", README.format(
        tier=tier,
        when=time.strftime("%Y-%m-%d %H:%M"),
        tier_desc="只玩对局，不动真机" if tier == "A" else "含接真手所需文件",
        manifest="\n".join(sorted(manifest_lines))), bom=False)

    # 打印清单
    print(f"✅ 打包完成：{out}")
    print(f"   档位 {tier}　文件 {len(files)} 个　总计 {total / 1024 / 1024:.1f} MB")
    print("   清单：")
    for line in sorted(manifest_lines):
        print("     " + line)

    # 生成 zip（同名，放在产物目录下，方便直接微信发）
    zpath = out.with_suffix(".zip")
    if zpath.exists():
        zpath.unlink()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(out).as_posix())
    zmb = zpath.stat().st_size / 1024 / 1024
    print(f"   📦 zip: {zpath.name}　{zmb:.1f} MB（可直接发微信）")

    # 自检：不该出现的东西
    leaked = [p for p in out.rglob("*")
              if p.is_file() and (p.name in BANNED_NAMES or p.suffix.lower() in BANNED_SUFFIX)]
    print("   泄漏检查：", "✅ 无日志/密钥" if not leaked else f"❌ 有 {len(leaked)} 个可疑文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
