"""重新录一遍动作（28_rerecord_poses.py）—— 把每个姿势"真跑到位、量下来"当新值

为什么这么录（2026-11-21 用户要求「重新录一遍动作」）：
  之前 poses.json 里的值是**当初手调的期望值**，其间换过弹簧（0.4）、改过零点（calibrate-mid）、
  抬高过上限（ch1→45000、ch4→54650），所以"目标 58000"和"手指实际停在 57717"已经对不上。
  这次的做法：**逐个姿势下发 → 等它停稳 → 读回真实位置/电流/温度 → 把量到的值写成新目标**。
  这样以后换动作、算归一化、做对比，用的都是"这双手真能到的位置"。

安全：
  · 每个姿势从一个固定起点（open）出发，用 minimum-jerk 平滑过渡；
  · 每次动作后检查温度与电流：温度 ≥46°C 或 |电流| ≥250 立刻停、卸力、退出；
  · 全程接 `estop`（温度 52 / 电流 320 / 标记文件三路）；
  · 结束时回 open 并卸力。

输出：
  · `sim2real\\poses_user.json` 里新增 `重录-<名字>` 条目（含 targets/pos/curr/temp/时间）
  · 屏幕上的对照表（目标 vs 实际 vs 偏差 vs 电流 vs 温度）
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from motion import minjerk                      # noqa: E402
from porthelp import open_hand                 # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
CTRL_POS, GET_POS, GET_CURR, GET_TEMP = 0x11, 0x22, 0x24, 0x25
SECS = 1.6            # 每个姿势过渡时长
SETTLE = 1.0          # 停下来再读的时间
TEMP_STOP = 46.0      # 到 46°C 就停
CURR_STOP = 250       # 单通道电流超过这个就停


def load_json(p: Path, default=None):
    if not p.exists():
        return default if default is not None else {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def s16(v):
    return v - 65536 if v >= 32768 else v


class Hand:
    def __init__(self, ser):
        self.s = ser
        self.lock = threading.Lock()      # 急停线程也会用串口，必须加锁

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


def move(h: Hand, cur, tgt, secs=SECS, hz=25) -> list:
    steps = max(1, int(secs * hz))
    c = list(cur)
    for k in range(1, steps + 1):
        f = minjerk(k / steps)
        h.send([int(a + (b - a) * f) for a, b in zip(c, tgt)])
        time.sleep(1 / hz)
    return list(tgt)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    doc = load_json(SIM / "poses.json")
    poses = {k: v["targets"] for k, v in doc["poses"].items()}
    limits = {int(k): (v["lo"], v["hi"]) for k, v in load_json(SIM / "limits.json").items()}

    ser = open_hand()
    h = Hand(ser)
    from estop import install_estop
    install_estop(h.send, h.pos, h.curr, h.temp)

    # 起手先卸力（记住：刷写/触限之后第一条命令必须是卸力）
    p0 = h.pos()
    h.send(list(p0))
    time.sleep(0.5)
    print(f"已卸力，起点 {p0}  最高 {max(h.temp())}°C", flush=True)

    open_t = poses.get("open") or p0
    cur = move(h, p0, open_t, 1.4)
    time.sleep(0.6)

    # 要重录的姿势（跳过 open 自身：它是基准）
    order = [n for n in ("rock", "one", "scissors", "three", "thumbs_up",
                         "pinch", "paper", "fist_thumb_out", "fist_thumb_over",
                         "oppo_index", "oppo_middle", "oppo_ring") if n in poses]

    user = load_json(SIM / "poses_user.json", {"poses": {}})
    user.setdefault("poses", {})
    results = []
    print(f"\n{'姿势':<16}{'目标→实际（偏差）':<44}{'电流峰值':>8}{'温度':>7}", flush=True)
    print("-" * 82, flush=True)

    for name in order:
        tgt = poses[name]
        # 夹进 limits（防止历史值越界）
        tgt = [max(limits[i][0], min(limits[i][1], tgt[i])) for i in range(7)]
        cur = move(h, cur, tgt)
        time.sleep(SETTLE)
        p, c, t = h.pos(), h.curr(), h.temp()
        cmax = max(abs(x) for x in c)
        tmax = max(t)
        dev = [p[i] - tgt[i] for i in range(7)]
        results.append((name, tgt, p, c, t, dev))
        dev_s = " ".join(f"{d:+d}" for d in dev)
        print(f"{name:<16}{str(p):<34} {dev_s:<20}{cmax:>8}{tmax:>6}°C", flush=True)
        user["poses"][f"重录-{name}"] = {
            "targets": p,                       # ← 用实测值当新目标
            "nominal": tgt,
            "pos": p, "curr": c, "temp": t,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (SIM / "poses_user.json").write_text(
            json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")
        # 安全闸
        if tmax >= TEMP_STOP or cmax >= CURR_STOP:
            print(f"⚠ {name}: 温度 {tmax}°C / 电流 {cmax} 超限，立刻卸力结束", flush=True)
            pp = h.pos()
            h.send(list(pp))
            ser.close()
            return
        # 每个姿势后回 open，保证下一个姿势从固定起点出发
        cur = move(h, p, open_t, 1.2)
        time.sleep(0.5)

    # open 自己：量一次实际值
    time.sleep(SETTLE)
    po, co, to = h.pos(), h.curr(), h.temp()
    user["poses"]["重录-open"] = {"targets": po, "nominal": open_t, "pos": po,
                                  "curr": co, "temp": to,
                                  "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    (SIM / "poses_user.json").write_text(json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{'open':<16}{str(po):<34} {'—':<20}{max(abs(x) for x in co):>8}{max(to):>6}°C", flush=True)

    # 收尾：回 open + 卸力
    cur = move(h, h.pos(), open_t, 1.2)
    time.sleep(0.5)
    pp = h.pos()
    h.send(list(pp))
    print(f"\n已回 open 并卸力：{pp}  最高 {max(h.temp())}°C", flush=True)
    print(f"共重录 {len(results)} 个姿势 + open，已存 sim2real\\poses_user.json（前缀 重录-）", flush=True)
    ser.close()


if __name__ == "__main__":
    main()
