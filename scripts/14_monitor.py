"""实时温度/电流/位置监视器（给现场调试用）

用途：插上/拔下电源、换弹簧、按住手指时，**一眼看到 7 个通道的温度和电流怎么变**。
判据（经验值）：
  · 待机温度 30~35°C 正常；**>45°C 要留意；>55°C 立刻断电**
  · 待机电流应在个位/十几；**某通道持续几百 = 在对抗负载（弹簧太硬/顶住）**

用法：
  py scripts\\14_monitor.py                 # 0.6 秒刷新一次
  py scripts\\14_monitor.py --hz 2          # 每 0.5 秒刷新
  py scripts\\14_monitor.py --csv log.csv   # 同时把数据存 CSV（方便后面画曲线）

没插板子/没供电时会显示"等待设备…"，插上会自动接上。Ctrl-C 退出。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

NAMES = ["拇指外展", "拇指屈", "拇指腱", "食指腱", "中指腱", "无名指腱", "小指腱"]
GET_POS, GET_CURR, GET_TEMP = 0x22, 0x24, 0x25

TEMP_WARN, TEMP_DANGER = 45, 55
CURR_WARN = 100


def find_port():
    from serial.tools import list_ports

    for p in list_ports.comports():
        if (p.vid or 0) == 0x303A:
            return p.device
    return None


def s16(v: int) -> int:
    return v - 65536 if v >= 32768 else v


def read_all(s, op: int, tries: int = 3):
    for _ in range(tries):
        try:
            s.reset_input_buffer()
            s.write(bytes([op, 0] + [0] * 14))
            s.flush()
            time.sleep(0.12)
            r = s.read(16)
        except Exception:
            return None
        if len(r) == 16 and r[0] == op:
            return [(r[2 + 2 * i + 1] << 8) | (r[2 + 2 * i]) for i in range(7)]
    return None


def render(pos, curr, temp) -> str:
    hdr = f"{'通道':<4}{'执行器':<10}{'位置':>7}{'电流':>8}{'温度':>6}  状态"
    lines = [hdr, "-" * 52]
    worst = ""
    for i in range(7):
        t = temp[i] if temp else 0
        c = s16(curr[i]) if curr else 0
        p = pos[i] if pos else 0
        flag = ""
        if t >= TEMP_DANGER:
            flag = "🔥 烫！断电"
            worst = "DANGER"
        elif t >= TEMP_WARN:
            flag = "⚠ 偏热"
            worst = worst or "WARN"
        elif abs(c) >= CURR_WARN:
            flag = "⚠ 大电流(对抗负载)"
            worst = worst or "WARN"
        lines.append(f"{i:<4}{NAMES[i]:<10}{p:>7}{c:>8}{t:>6}  {flag}")
    if all((temp or [0] * 7)[i] == 0 for i in range(7)):
        lines.append("")
        lines.append("💡 温度全 0 —— 舵机没供电（或总线没通）。这是判断'有没有电'最快的办法。")
    return "\n".join(lines) + ("\n" if worst != "DANGER" else "\n")


def _api_snapshot():
    """串口被占（通常是 API 在跑）时，改读本地 API 的 /status。

    2026-11-21 修：启动台「全部启动」会先起 API 再起监控，监控抢不到 COM5 就
    每 0.6 秒刷一行 PermissionError，既刷屏又白占 CPU。现在自动降级到 API 模式。
    """
    import json
    import urllib.request
    token_file = Path(__file__).resolve().parent.parent / "sim2real" / ".api_token"
    token = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else ""
    req = urllib.request.Request("http://127.0.0.1:8765/status", headers={"X-Token": token})
    with urllib.request.urlopen(req, timeout=2.0) as r:
        d = json.loads(r.read())
    return d.get("pos"), d.get("curr"), d.get("temp")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=float, default=1.6, help="每秒刷新次数（默认 1.6）")
    ap.add_argument("--csv", type=Path, default=None, help="把每次采样写进 CSV")
    ap.add_argument("--once", action="store_true", help="只打印一帧就退出（快速检查用）")
    args = ap.parse_args()

    import serial

    writer = None
    fh = None
    if args.csv:
        fh = args.csv.open("w", encoding="utf-8-sig", newline="")
        writer = csv.writer(fh)
        writer.writerow(["time"] + [f"pos{i}" for i in range(7)]
                        + [f"curr{i}" for i in range(7)] + [f"temp{i}" for i in range(7)])

    period = 1.0 / max(0.2, args.hz)
    s = None
    wait_ticks = 0                 # --once 模式下最多等几次，避免无设备时死等
    print("监视器启动（Ctrl-C 退出）")
    try:
        while True:
            if s is None:
                port = find_port()
                if not port:
                    if args.once:
                        wait_ticks += 1
                        if wait_ticks >= 3:
                            print("找不到 VID 0x303A 的设备（板子没插？），--once 直接退出")
                            return
                    sys.stdout.write(f"\r等待设备…（没找到 VID 0x303A）{datetime.now():%H:%M:%S}   ")
                    sys.stdout.flush()
                    time.sleep(1.0)
                    continue
                try:
                    s = serial.Serial(port, 921600, timeout=0.4)
                    time.sleep(1.0)
                    s.reset_input_buffer()
                    s.read(400)
                    print(f"\n已连接 {port}")
                except Exception as e:
                    busy = isinstance(e, PermissionError) or "PermissionError" in str(e) \
                           or "拒绝访问" in str(e)
                    if busy:
                        # 串口被别的程序占着 → 改用 API 读（只读，不碰串口）
                        try:
                            pos, curr, temp = _api_snapshot()
                            print(f"\n[降级] 串口被占，改读 API（127.0.0.1:8765）")
                            print(render(pos, curr, temp), flush=True)
                            time.sleep(period)
                            continue
                        except Exception as e2:
                            print(f"\n[降级失败] 串口被占且 API 不可用：{e2}")
                            return
                    sys.stdout.write(f"\r连接 {port} 失败：{e}   ")
                    sys.stdout.flush()
                    s = None
                    time.sleep(1.0)
                    continue

            pos = read_all(s, GET_POS)
            curr = read_all(s, GET_CURR)
            temp = read_all(s, GET_TEMP)

            if pos is None and curr is None and temp is None:
                # 板子可能被拔了 / 串口断了
                try:
                    s.close()
                except Exception:
                    pass
                s = None
                print("\n⚠️ 读不到数据（设备被拔掉或断电），回到等待状态…")
                continue

            print("\033[2J\033[H", end="")          # 清屏（Windows 终端支持）
            print(f"Aero Hand 实时监视   {datetime.now():%H:%M:%S}   刷新 {args.hz:.1f} Hz")
            print(render(pos, curr, temp))
            print("判据：待机 30~35°C 正常；>45 留意；>55 断电 | 某通道电流持续几百 = 对抗负载")
            if args.once:
                return

            if writer:
                writer.writerow([datetime.now().isoformat(timespec="milliseconds")]
                                + (pos or [0] * 7) + [s16(x) for x in (curr or [0] * 7)]
                                + (temp or [0] * 7))
                fh.flush()

            time.sleep(period)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass
        if fh:
            fh.close()


if __name__ == "__main__":
    main()
