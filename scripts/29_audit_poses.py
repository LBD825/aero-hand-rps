"""把动作库逐个"瞬时"演一遍，让用户看清哪个要删（29_audit_poses.py）

用途：动作库攒到十几个后，肉眼看不过来也不知道该删哪些。
做法：从 open 出发，**瞬时**跳到每个姿势 → 停 2.5 秒（够看清）→ 回 open → 下一个。
      每个姿势先打印名字和编号，屏幕上能对上"现在演的是哪个"。

安全：接 estop；每个姿势后检查温度 ≥46°C 或 |电流| ≥250 就停；结束回 open 并卸力。
用法：py scripts\\29_audit_poses.py            # 全部姿势
      py scripts\\29_audit_poses.py --only rock,one   # 只演指定几个
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from porthelp import open_hand                 # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
CTRL_POS, GET_POS, GET_CURR, GET_TEMP = 0x11, 0x22, 0x24, 0x25
HOLD = 2.5          # 每个姿势停留秒数（看情况改）
GAP = 0.8           # 回 open 后停顿


def s16(v):
    return v - 65536 if v >= 32768 else v


class Hand:
    def __init__(self, ser):
        self.s = ser
        self.lock = threading.Lock()

    def _read(self, op):
        with self.lock:
            for _ in range(4):
                self.s.reset_input_buffer()
                self.s.write(bytes([op, 0] + [0] * 14))
                self.s.flush()
                time.sleep(0.15)
                r = self.s.read(16)
                if len(r) == 16 and r[0] == op:
                    return [(r[2 + 2 * i + 1] << 8) | (r[2 + 2 * i]) for i in range(7)]
            return None

    def pos(self):
        return self._read(GET_POS) or [0] * 7

    def curr(self):
        v = self._read(GET_CURR)
        return [s16(x) for x in v] if v else [0] * 7

    def temp(self):
        return self._read(GET_TEMP) or [0] * 7

    def send(self, targets):
        pl = [0]
        for v in targets:
            vi = int(v)
            pl += [vi & 0xFF, (vi >> 8) & 0xFF]
        with self.lock:
            self.s.write(bytes([CTRL_POS] + pl))
            self.s.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的姿势名，只演这些")
    ap.add_argument("--hold", type=float, default=HOLD)
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    doc = json.loads((SIM / "poses.json").read_text(encoding="utf-8-sig"))
    poses = {k: v["targets"] for k, v in doc["poses"].items()}
    names = ([n.strip() for n in args.only.split(",") if n.strip()]
             if args.only else list(poses.keys()))
    names = [n for n in names if n in poses]

    ser = open_hand()
    h = Hand(ser)
    from estop import install_estop
    install_estop(h.send, h.pos, h.curr, h.temp)

    p = h.pos()
    h.send(list(p))                     # 先卸力
    time.sleep(0.4)
    print(f"已卸力，起点 {p}  {max(h.temp())}°C", flush=True)
    h.send(list(poses["open"]))         # 摆到 open 起点
    time.sleep(1.0)
    print(f"\n准备演 {len(names)} 个姿势，每个停 {args.hold} 秒。看清了记下要删的名字。\n", flush=True)

    try:
        for i, n in enumerate(names, 1):
            h.send(list(poses[n]))      # 瞬时到位
            time.sleep(0.45)            # 等舵机走完（瞬时下发，机构还要 0.3~0.5s）
            p, c, t = h.pos(), h.curr(), h.temp()
            cmax = max(abs(x) for x in c)
            tmax = max(t)
            print(f"[{i:>2}/{len(names)}] {n:<16} pos={p}  电流峰值={cmax:>3}  {tmax}°C",
                  flush=True)
            time.sleep(args.hold)
            if tmax >= 46 or cmax >= 250:
                print(f"⚠ {n} 超限（{tmax}°C / {cmax}），停止", flush=True)
                break
            h.send(list(poses["open"]))
            time.sleep(GAP)
        print("\n演完了，回 open 并卸力", flush=True)
        h.send(list(poses["open"]))
        time.sleep(0.8)
        h.send(list(h.pos()))
        print(f"最终 {h.pos()}  {max(h.temp())}°C", flush=True)
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
