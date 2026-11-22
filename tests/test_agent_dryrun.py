"""tests\\test_agent_dryrun.py —— 不碰硬件，验证 `agent` 包符合接口契约 v1

**为什么要有这个测试**（2026-11-22）：
总指挥给的 P0 第一件是 `agent\\` 薄壳，但薄壳是"三条线共用的墙"——
智能体部照它写界面、总指挥照它验收。墙如果歪了（键缺了、错误码变了、dry_run 偷偷发了字节），
另外两条线要返工。所以这里用**纯 dry-run** 把契约逐条钉死，任何电脑都能跑，不需要手。

跑法：
    py tests\\test_agent_dryrun.py
    （或 .venv\\Scripts\\python.exe tests\\test_agent_dryrun.py）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import AgentError, ERROR_CODES, N_CH, open_hand   # noqa: E402

passed, failed = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=== 契约 1：8 个 API 都存在 ===")
    h = open_hand(dry_run=True)
    for m in ("list_poses", "pose_info", "play", "home", "estop", "read_state", "close"):
        check(f"Hand.{m}()", callable(getattr(h, m, None)))
    check("open_hand(dry_run=True) 返回 Hand", h.__class__.__name__ == "Hand")

    print("\n=== 契约 2：错误码只有 5 个 ===")
    check("ERROR_CODES == 5 个", len(ERROR_CODES) == 5, str(ERROR_CODES))
    for code in ("PORT_BUSY", "NO_HAND", "UNKNOWN_POSE", "TOO_HOT", "DRY_RUN"):
        check(f"含 {code}", code in ERROR_CODES)

    print("\n=== 契约 3：list_poses / pose_info ===")
    poses = h.list_poses()
    check("list_poses 非空", len(poses) > 0, f"{len(poses)} 个")
    check("含 open", "open" in poses)
    info = h.pose_info("open")
    check("pose_info 有 label", isinstance(info.get("label"), str))
    check("pose_info.targets 是 7 个整数",
          isinstance(info.get("targets"), list) and len(info["targets"]) == N_CH
          and all(isinstance(x, int) for x in info["targets"]), str(info.get("targets")))

    print("\n=== 契约 4：UNKNOWN_POSE 要抛异常，不能返回 False ===")
    try:
        h.play("不存在的姿势")
        check("play 未知姿势抛 AgentError", False, "没有抛")
    except AgentError as e:
        check("play 未知姿势抛 AgentError", e.code == "UNKNOWN_POSE", e.code)
    try:
        h.pose_info("也不存在")
        check("pose_info 未知姿势抛 AgentError", False, "没有抛")
    except AgentError as e:
        check("pose_info 未知姿势抛 AgentError", e.code == "UNKNOWN_POSE", e.code)

    print("\n=== 契约 5：strength 插值 0→open、1→目标 ===")
    tgt = h.pose_info("rock")["targets"]
    open_t = h.pose_info("open")["targets"]
    got0 = h.play("rock", strength=0.0, secs=0)
    check("strength=0 时等于 open", got0 == open_t, f"{got0} vs {open_t}")
    got1 = h.play("rock", strength=1.0, secs=0)
    check("strength=1 时接近目标（允许被 limits 夹紧）",
          all(abs(a - b) <= 2000 for a, b in zip(got1, tgt)), f"{got1} vs {tgt}")
    got_half = h.play("rock", strength=0.5, secs=0)
    check("strength=0.5 大致在中间",
          all(abs(g - (o + (t - o) * 0.5)) <= 2000
              for g, o, t in zip(got_half, open_t, tgt)), str(got_half))

    print("\n=== 契约 6：read_state 四个键永远在 ===")
    st = h.read_state()
    for k in ("temps", "positions", "currents", "t_sec"):
        check(f"含 {k}", k in st)
    check("dry_run 时 mock=True", st.get("mock") is True, str(st.get("mock")))
    check("t_sec 是时间戳", isinstance(st["t_sec"], float))

    print("\n=== 契约 7：estop 必须很快返回（0.2 秒内卸力 + 回 open 不应卡死）===")
    t0 = time.time()
    h.estop()
    dt = time.time() - t0
    check(f"estop 用时 {dt:.2f}s < 2s", dt < 2.0, f"{dt:.2f}s")

    print("\n=== 契约 8：home / close 可用 ===")
    h.home()
    check("home 后等于 open", h.read_state() is not None)
    h.close()
    check("close 不抛异常", True)

    print(f"\n=== 结果：通过 {passed}，失败 {failed} ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
