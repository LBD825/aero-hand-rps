"""启动前自检（precheck.py）—— 双击入口先跑它，避免"窗口开了但没反应"

**为什么需要**（2026-11-22 实测）：双击「启动对局-摄像头.bat」时如果已经有一个对局在跑，
第二个实例会去抢 COM5 → 手连不上 / 静默不动，界面上看着像"程序坏了"。
另外摄像头、模型文件、串口占用也都是常见"看起来正常其实没起来"的原因。

检查四项，任何一项不满足就打印原因并返回非 0：
  1. 有没有**已在运行的对局实例**（同脚本名）
  2. COM5（VID 0x303A）是否**空着**
  3. 摄像头能否打开（0 号，MSMF）
  4. models/hand_landmarker.task 是否在

用法：python scripts\\precheck.py            # 供 bat 调用
      python scripts\\precheck.py --skip-cam # 不测摄像头（无摄像头场景）
退出码：0 = 可以启动；1 = 有阻塞项
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "hand_landmarker.task"
GAME = "30_rps_game.py"


def running_games() -> list[str]:
    """列出正在跑的对局进程（排除自己）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } "
             "| Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace").stdout
        import json
        data = json.loads(out) if out.strip() else []
        if isinstance(data, dict):
            data = [data]
        me = str(Path(__file__).stat().st_ino)  # 仅用于去重，不完全可靠
        rows = [r for r in data if GAME in (r.get("CommandLine") or "")]
        # .venv\Scripts\python.exe 是 shim：它会再起一个真实 python，
        # 父子都含 30_rps_game.py ⇒ 父 PID 也在集合里时跳过，避免"一个对局算两个"
        pids = {int(r.get("ProcessId") or 0) for r in rows}
        hits = []
        for r in rows:
            pid = int(r.get("ProcessId") or 0)
            parent = int(r.get("ParentProcessId") or 0)
            if parent in pids:
                continue
            hits.append(f"PID {pid}")
        return hits or [f"PID {next(iter(pids))}"] if pids else []
    except Exception:
        return []


def check_serial() -> str:
    try:
        import serial
        from serial.tools import list_ports
    except Exception as e:
        return f"缺少 pyserial（{type(e).__name__}）"
    port = next((p.device for p in list_ports.comports() if (p.vid or 0) == 0x303A), None)
    if not port:
        return "没找到手（VID 0x303A）——板子插好、上电了吗？"
    try:
        s = serial.Serial(port, 921600, timeout=0.4)
        s.close()
        return f"OK {port}"
    except Exception as e:
        return f"{port} 被占用：{type(e).__name__}（先关掉温度监控/其它对局）"


def check_camera() -> str:
    try:
        import cv2
    except Exception as e:
        return f"缺少 opencv（{type(e).__name__}）"
    for backend, name in ((cv2.CAP_MSMF, "MSMF"), (cv2.CAP_DSHOW, "DSHOW")):
        try:
            cap = cv2.VideoCapture(0, backend)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None:
                return f"OK {name} {frame.shape[1]}x{frame.shape[0]}"
        except Exception:
            continue
    return "摄像头打不开（被别的程序占用？）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-cam", action="store_true")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    problems: list[str] = []

    games = running_games()
    print(f"[1/4] 已有对局实例：{'、'.join(games) if games else '无'}")
    if games:
        problems.append(f"已经有一个对局在跑（{'、'.join(games)}）——先关掉那个窗口，或双击「全关.bat」")

    print("[2/4] 串口：", end="", flush=True)
    ser = check_serial()
    print(ser)
    if not ser.startswith("OK"):
        problems.append(ser)

    if args.skip_cam:
        print("[3/4] 摄像头：跳过（--skip-cam）")
    else:
        print("[3/4] 摄像头：", end="", flush=True)
        cam = check_camera()
        print(cam)
        if not cam.startswith("OK"):
            problems.append(cam)

    print("[4/4] 模型文件：", end="", flush=True)
    if MODEL.exists():
        print(f"OK {MODEL.stat().st_size / 1048576:.1f} MB")
    else:
        print("缺失")
        problems.append(f"缺模型文件 {MODEL}（约 7.5 MB，不在 requirements 里，需手工拷）")

    if problems:
        print("\n❌ 不能启动，先解决这些：")
        for i, p in enumerate(problems, 1):
            print(f"   {i}. {p}")
        return 1

    print("\n✅ 自检通过，可以启动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
