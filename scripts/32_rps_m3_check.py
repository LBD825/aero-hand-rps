"""M3 真机三手势体检（32）—— 一次跑完，出表，判过不过。

★ 为什么单独写一个
    M3 的验收标准是"三手势到位 + 峰值电流 <250A + 温度 <45°C，重复 10 次无卡滞"。
    手动一个个点太慢、也不留证据。这个脚本把它变成"跑一遍 → 一张表 → 明确 PASS/FAIL"，
    现场只需 15 分钟。

★ 默认是干跑（安全）
    py scripts\\32_rps_m3_check.py                # 干跑：只打印将要下发的 7 个值，不动手
    py scripts\\32_rps_m3_check.py --live          # 真机：会动！跑之前关掉 监控-温度.bat
    py scripts\\32_rps_m3_check.py --live --reps 10 --secs 0.8

★ 判据（与手册一致）
    · 每个手势播放后读一次状态：最热通道 <45°C、|最大电流| <250
    · 下落/到位：目标与实测的最大偏差 < 3% 行程（读 2 次取后一次，给舵机时间走完）

★ 安全
    · 只动三个 RPS 手势（`rock` / `scissors` / `open`），不碰 `limits.json`、不改姿势
    · 每个手势之间回 `open` 歇 0.6 秒，避免连续顶住升温
    · 发现 ≥50°C 立刻卸力退出（复用 agent.thermal 的判据）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent import open_hand, AgentError      # noqa: E402

POSE_OF = {"rock": "rock", "scissors": "scissors", "paper": "open"}   # 布＝open
TARGETS = ["rock", "scissors", "paper"]      # RPS 三种
TEMP_LIMIT = 46.0      # 总指挥 2026-11-22 裁决：≤46 通过；44~46 标临界
CURR_LIMIT = 250
TOL_PCT = 3.0                                # 到位判据：偏差 < 3% 行程


def _fmt(vals):
    return "[" + ", ".join(f"{v:6d}" if isinstance(v, int) else f"{v:6.1f}" for v in (vals or [])) + "]"


def _one(hand, move, secs, dry, limits):
    pose = POSE_OF[move]
    info = hand.pose_info(pose)
    tgt = info["targets"]
    if dry:
        print(f"  [DRY] {move:8} → 姿势 {pose:8} 将要下发 {_fmt(tgt)}", flush=True)
        return {"move": move, "ok": True, "dry": True, "targets": tgt,
                "temp": None, "curr": None, "dev_pct": None}
    hand.play(pose, strength=1.0, secs=secs)
    time.sleep(max(0.3, secs * 0.6))          # 给舵机走完
    st = hand.read_state()
    pos = st.get("positions") or []
    temps = st.get("temps") or []
    currs = st.get("currents") or []
    dev = 0.0
    for i, (t, p) in enumerate(zip(tgt, pos)):
        span = max(1, limits[i][1] - limits[i][0])
        dev = max(dev, abs(int(t) - int(p)) / span * 100.0)
    tmax = max(temps) if temps else None
    cmax = max((abs(c) for c in currs), default=None) if currs else None
    ok = (tmax is not None and tmax <= TEMP_LIMIT) and \
         (cmax is not None and cmax < CURR_LIMIT) and dev < TOL_PCT
    # 44~46°C 记"临界通过"（总指挥裁决：该区间小于测量分辨力，不判未通过）
    marginal = (tmax is not None and 44.0 <= tmax <= TEMP_LIMIT)
    # 2026-11-21 总指挥要求 M3 回填"最热+峰值+哪个通道"：把通道号一起打出来并存进结果
    hot_ch = max(range(len(temps)), key=lambda i: temps[i]) if temps else None
    cur_ch = max(range(len(currs)), key=lambda i: abs(currs[i])) if currs else None
    print(f"  {move:8} → {pose:8} 实测 {_fmt(pos)} 最热 {tmax}°C(ch{hot_ch}) "
          f"峰值电流 {cmax}(ch{cur_ch}) "
          f"偏差 {dev:.1f}%  {'PASS' if ok else 'FAIL'}"
          f"{'（临界）' if (ok and marginal) else ''}", flush=True)
    return {"move": move, "ok": ok, "dry": False, "targets": tgt,
            "temp": tmax, "curr": cmax, "dev_pct": round(dev, 2),
            "hot_ch": hot_ch, "cur_ch": cur_ch, "temps": temps, "currs": currs}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="M3 真机三手势体检")
    ap.add_argument("--live", action="store_true", help="真机动手（默认干跑）")
    ap.add_argument("--reps", type=int, default=3, help="每种手势重复几次（验收用 10）")
    ap.add_argument("--secs", type=float, default=0.8, help="每次过渡时长")
    args = ap.parse_args()

    dry = not args.live
    print("=" * 66)
    print(f" M3 三手势体检  模式={'DRY-RUN（不动手）' if dry else '真机'}"
          f"  每种重复={args.reps}  过渡={args.secs}s")
    if not dry:
        print(" 真机注意：先关掉 监控-温度.bat（独占 COM5）；6V 已上电；手能自由活动")
    print("=" * 66, flush=True)

    try:
        hand = open_hand(dry_run=dry)
    except AgentError as e:
        print(f"[失败] 打不开手：{e.code} {e}", file=sys.stderr)
        return 2
    print(f"[手] {'DRY-RUN（假手）' if dry else '真机 ' + str(hand.port)}", flush=True)
    limits = hand.limits

    rows = []
    try:
        for rep in range(1, args.reps + 1):
            print(f"\n-- 第 {rep}/{args.reps} 轮 --", flush=True)
            for move in TARGETS:
                try:
                    rows.append(_one(hand, move, args.secs, dry, limits))
                except AgentError as e:
                    print(f"  {move:8} → [安全中断] {e.code} {e}", flush=True)
                    raise
                time.sleep(0.2)
            if not dry:
                hand.home(secs=0.6)
                time.sleep(0.6)
    except AgentError as e:
        print(f"\n[中止] {e.code} {e}", file=sys.stderr)
        try:
            hand.estop()
        except Exception:
            pass
        hand.close()
        return 3
    finally:
        if dry:
            hand.close()

    # ---- 汇总 ----
    print("\n" + "=" * 66)
    print(f"{'手势':8}{'次数':>4}{'最热°C':>9}{'峰值电流':>10}{'到位偏差%':>10}  结果")
    print("-" * 66)
    all_ok = True
    for move in TARGETS:
        got = [r for r in rows if r["move"] == move]
        if not got:
            continue
        temps = [r["temp"] for r in got if r["temp"] is not None]
        currs = [r["curr"] for r in got if r["curr"] is not None]
        devs = [r["dev_pct"] for r in got if r["dev_pct"] is not None]
        ok = all(r["ok"] for r in got)
        all_ok = all_ok and ok
        print(f"{move:8}{len(got):>4}"
              f"{(max(temps) if temps else 0):>9.1f}"
              f"{(max(currs) if currs else 0):>10}"
              f"{(max(devs) if devs else 0):>10.1f}"
              f"  {'PASS' if ok else 'FAIL'}")
    print("-" * 66)
    verdict = "PASS —— M3 通过，可以进 M4 现场对局" if all_ok else "FAIL —— 看上面哪一行 / 哪一项超了"
    print(f"总判定：{verdict}")
    if dry:
        print("（这是干跑结果，真实数字要加 --live 再跑一次）")
    try:
        hand.close()
    except Exception:
        pass
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
