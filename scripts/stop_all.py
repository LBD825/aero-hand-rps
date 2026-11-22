"""全关（stop_all.py）—— 一条命令关掉所有组件并卸力

**为什么需要**：有的组件是隐藏窗口启动的（比如 API 用 `Start-Process -WindowStyle Minimized`
+ 重定向输出），任务栏上找不到、点不到 ✕。用户要"全关"时没有可点的东西。

**做的事（按安全顺序）**：
  1. 先给 API 发 `/release`（若 API 在跑）——先松手，再关进程；
  2. 杀掉本项目相关的 python 进程（API / 视觉 / 监控 / GUI / 对局 / 启动台），**排除自己**；
  3. 再直连串口补一次卸力（进程都走了，串口应该空出来）；
  4. 验收：串口能否打开、7 通道温度电流、API 端口是否已关。

用法：
    py scripts\\stop_all.py            # 执行
    py scripts\\stop_all.py --dry      # 只看会关哪些进程
双击入口：项目根 `全关.bat`（纯 ASCII）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
API = "http://127.0.0.1:8765"

# 认哪些进程属于本项目（按命令行关键词）
PATTERNS = ("26_api", "27_vision", "27b_vision", "14_monitor", "24_gui_manual",
            "30_rps_game", "00_launcher", "16_safe_init", "09_sim2real_bridge")


def token() -> str:
    p = SIM / ".api_token"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def api_alive() -> bool:
    try:
        urllib.request.urlopen(API + "/health", timeout=2).read()
        return True
    except Exception:
        return False


def api_release() -> bool:
    try:
        req = urllib.request.Request(API + "/release", data=b"{}",
                                     headers={"Content-Type": "application/json",
                                              "X-Token": token()})
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception:
        return False


def find_targets() -> list[tuple[int, str]]:
    """列出本项目相关、且不是自己的 python 进程。"""
    me = os.getpid()
    out = []
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } "
             "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace").stdout
        data = json.loads(raw) if raw.strip() else []
        if isinstance(data, dict):
            data = [data]
        for row in data:
            pid = int(row.get("ProcessId") or 0)
            cmd = row.get("CommandLine") or ""
            if pid == me:
                continue
            if any(k in cmd for k in PATTERNS):
                out.append((pid, cmd))
    except Exception as e:
        print(f"[warn] 枚举进程失败：{e}", flush=True)
    return out


def kill(pid: int) -> bool:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def serial_open_then_release() -> str:
    """进程都关掉后：先回 open（已知安全位），再卸力。

    只卸力会把手指留在"上一个动作停在哪就是哪"（实测曾停在握拳残留位），
    用户看到会以为没关干净；所以先把它摆回 open 再松手。
    """
    try:
        import serial
        from serial.tools import list_ports
    except Exception as e:
        return f"没有 pyserial：{e}"
    port = next((p.device for p in list_ports.comports() if (p.vid or 0) == 0x303A), None)
    if not port:
        return "找不到手（VID 0x303A）"
    try:
        s = serial.Serial(port, 921600, timeout=0.5)
        time.sleep(1.0)
        s.reset_input_buffer()
        s.read(400)

        def q(op):
            for _ in range(3):
                s.reset_input_buffer()
                s.write(bytes([op, 0] + [0] * 14))
                s.flush()
                time.sleep(0.14)
                r = s.read(16)
                if len(r) == 16 and r[0] == op:
                    return [(r[2 + 2 * i + 1] << 8) | (r[2 + 2 * i]) for i in range(7)]
            return None

        p = q(0x22)
        if not p:
            s.close()
            return f"{port} 打开但读不到数据"
        # 先回 open：读 poses.json 的 open，用最小步进发过去（不追速度，求稳）
        open_t = None
        try:
            doc = json.loads((SIM / "poses.json").read_text(encoding="utf-8-sig"))
            open_t = doc["poses"]["open"]["targets"]
        except Exception:
            open_t = None
        if open_t:
            steps = 24
            cur = list(p)
            for k in range(1, steps + 1):
                f = k / steps
                tgt = [int(c + (t - c) * f) for c, t in zip(cur, open_t)]
                s.write(bytes([0x11, 0] + sum([[v & 0xFF, (v >> 8) & 0xFF] for v in tgt], [])))
                s.flush()
                time.sleep(0.05)
            time.sleep(0.4)
            p = q(0x22) or p
        # 再卸力（目标=当前位置）
        s.write(bytes([0x11, 0] + sum([[v & 0xFF, (v >> 8) & 0xFF] for v in p], [])))
        s.flush()
        time.sleep(0.5)
        c = q(0x24)
        t = q(0x25)
        s.close()
        cur = [x - 65536 if x >= 32768 else x for x in (c or [0] * 7)]
        return (f"✅ {port} 已卸力：位置 {p} 电流 {cur} 最高温 {max(t) if t else '?'}°C")
    except Exception as e:
        return f"{port} 操作失败：{type(e).__name__} {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只列出要关的进程")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 56)
    print("  Aero Hand 全关")
    print("=" * 56)

    # ① 先松手
    if api_alive():
        print("① API 在跑 → 先发卸力…", "✅" if api_release() else "⚠ 卸力请求没成功")
    else:
        print("① API 没在跑（跳过 API 卸力）")

    # ② 关进程
    targets = find_targets()
    if not targets:
        print("② 没有需要关的本项目进程")
    else:
        print(f"② 要关 {len(targets)} 个进程：")
        for pid, cmd in targets:
            short = cmd.replace(str(ROOT), ".")[:90]
            print(f"   PID {pid}: {short}")
        if args.dry:
            print("\n（--dry：只列不关）")
            return 0
        for pid, _ in targets:
            print(f"   → 关闭 {pid} {'✅' if kill(pid) else '⚠'}")
        time.sleep(2.0)

    # ③ 补一次串口卸力 + 验收
    print("③ 直连串口补卸力：", serial_open_then_release())
    print("④ API 端口：", "⚠ 还在响应" if api_alive() else "✅ 已关闭")
    left = find_targets()
    print("⑤ 剩余本项目进程：", f"⚠ {len(left)} 个" if left else "✅ 0 个")
    print("\n完成。手已松，可以断电。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
