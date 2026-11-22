"""上电安全初始化：把所有通道先摆到"安全中点"，避免一上电就顶在死点上

为什么需要：这只手的 0 在多个通道上**不是零位**（ch0 的 0 甚至超出机械行程）。
固件上电默认目标可能让舵机一直顶着（实测 ch0 曾 −342/49°C）。所以每次上电后，
先跑这个脚本把 7 个通道摆到各自安全区间的中间，再开始别的动作。

安全值来源：`sim2real/endpoints.json`（实测端点），中点 = (lo_safe + hi_safe)/2。
用法：
  py scripts\\16_safe_init.py            # 只打印将要下发的值（dry-run）
  py scripts\\16_safe_init.py --live     # 真下发
  py scripts\\16_safe_init.py --live --read   # 下发后读回位置/电流/温度
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EP = ROOT / "sim2real" / "endpoints.json"
GET_POS, GET_CURR, GET_TEMP, CTRL_POS = 0x22, 0x24, 0x25, 0x11
NAMES = ["拇指外展", "拇指屈", "拇指腱", "食指腱", "中指腱", "无名指腱", "小指腱"]


def s16(v: int) -> int:
    return v - 65536 if v >= 32768 else v


def find_port() -> str:
    from serial.tools import list_ports

    for p in list_ports.comports():
        if (p.vid or 0) == 0x303A:
            return p.device
    raise SystemExit("找不到 VID 0x303A —— 板子没连")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--read", action="store_true", help="下发后读回状态")
    args = ap.parse_args()

    ep = json.loads(EP.read_text(encoding="utf-8-sig"))
    targets = []
    for ch in range(7):
        lo, hi = ep[str(ch)]["lo_pos"], ep[str(ch)]["hi_pos"]
        d = hi - lo
        lo_s, hi_s = lo + d * 0.08, hi - d * 0.08      # 与标定一致的 8% 余量
        mid = int((lo_s + hi_s) / 2)
        targets.append(mid)
        print(f"  ch{ch} {NAMES[ch]:<8} 安全区间[{int(lo_s)},{int(hi_s)}] → 中点 {mid}")

    if not args.live:
        print("\n[dry-run] 没下发。确认无误后加 --live。")
        return

    import serial

    s = serial.Serial(find_port(), 921600, timeout=0.5)
    time.sleep(1.0)
    s.reset_input_buffer()
    s.read(500)

    def send(t: list[int]):
        pl = [0]
        for v in t:
            pl += [v & 0xFF, (v >> 8) & 0xFF]
        s.write(bytes([CTRL_POS] + pl))
        s.flush()

    def q(op: int):
        for _ in range(4):
            s.reset_input_buffer()
            s.write(bytes([op, 0] + [0] * 14))
            s.flush()
            time.sleep(0.2)
            r = s.read(16)
            if len(r) == 16 and r[0] == op:
                return [(r[2 + 2 * i + 1] << 8) | (r[2 + 2 * i]) for i in range(7)]
        return None

    print("\n下发安全中点…")
    send(targets)
    time.sleep(1.5)

    if args.read:
        p, c, t = q(GET_POS), q(GET_CURR), q(GET_TEMP)
        print(f"{'通道':<4}{'执行器':<10}{'位置':>7}{'电流':>8}{'温度':>6}")
        for i in range(7):
            print(f"{i:<4}{NAMES[i]:<10}{p[i]:>7}{s16(c[i]):>8}{t[i]:>6}")
        hot = [i for i in range(7) if t[i] >= 50]
        if hot:
            print(f"\n⚠️ 这些通道温度 ≥50°C：{hot} —— 断电散热，别继续跑动作")
    s.close()
    print("完成")


if __name__ == "__main__":
    main()
