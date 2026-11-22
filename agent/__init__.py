"""`agent` —— Aero Hand 的公开接口（接口契约 v1，总指挥部 2026-11-21 定稿）。

    ⚠ 这个文件**只做导出与薄封装，不放底层逻辑**。
      字节在 `link.py`，串口在 `hal.py`，插值在 `motion.py`，温度在 `thermal.py`，假手在 `mock.py`。

★ 智能体部只需要看这里（8 个 API）
    from agent import open_hand, AgentError
    h = open_hand(dry_run=True)          # dry_run=True 完全不碰串口
    h.list_poses()                        # ['open', 'rock', ...]
    h.pose_info('rock')                   # {'label': ..., 'targets': [7 个整数]}
    h.play('rock', strength=1.0, secs=1.6)
    h.home()
    h.estop()
    h.read_state()                        # {'temps','positions','currents','t_sec','mock'}
    h.close()

★ 用法示例（dry-run，任何电脑都能跑，不需要手）
    py -c "from agent import open_hand; h=open_hand(dry_run=True); print(h.list_poses()); print(h.read_state())"

★ 错误码（智能体部据此提示用户，不要自己解析文字）
    PORT_BUSY    串口被占（监控窗口开着）
    NO_HAND      找不到 VID 0x303A
    UNKNOWN_POSE 姿势名不存在
    TOO_HOT      温度 ≥50°C，已停止下发
    DRY_RUN      dry_run 下调用了必须真机的操作（一般可忽略）

★ 数据约定
    · `targets` 恒为 7 个整数，顺序固定 [ch0..ch6]
    · `read_state()` 的四个键永远存在；读不到时值是 None（**不删键、不造 0**）
    · `strength` 只对 `play` 生效：0 → 停在 open 位，1 → 完整姿势
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

__version__ = "1.0.0"

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
POSES_FILE = SIM / "poses.json"
LIMITS_FILE = SIM / "limits.json"
USER_POSES_FILE = SIM / "poses_user.json"

N_CH = 7
HZ = 25.0                       # 下发频率（与真机脚本一致）
TEMP_CHECK_EVERY = 25           # 每 25 帧（≈1 秒）查一次温度

ERROR_CODES = ("PORT_BUSY", "NO_HAND", "UNKNOWN_POSE", "TOO_HOT", "DRY_RUN")


class AgentError(Exception):
    """带错误码的异常：`code` 是上面 5 个之一，`str(e)` 是给人看的中文说明。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(self.message)

    def __repr__(self):
        return f"AgentError({self.code!r}, {self.message!r})"


# ─────────────────────────── 小工具 ───────────────────────────

def _load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ─────────────────────────── 公开类 ───────────────────────────

class Hand:
    """一只手。`dry_run=True` 时内部换成 `mock.MockHand`，一个字节都不会发出去。"""

    def __init__(self, driver, *, dry_run: bool = True, poses_file: Path = POSES_FILE,
                 limits_file: Path = LIMITS_FILE, user_poses_file: Path = USER_POSES_FILE):
        self._hal = driver
        self.dry_run = dry_run
        self._poses_file = poses_file
        self._limits_file = limits_file
        self._user_file = user_poses_file
        self._lock = threading.Lock()
        self._warned_temp = False
        self.last_targets = None          # 最近一次下发的 7 个值（供 /state、调试）
        self.reload()

    # ---------- 数据 ----------
    def reload(self):
        """重新读 poses.json / limits.json（外部改了文件后调一次即可见）。"""
        doc = _load_json(self._poses_file)
        self._pose_docs = doc.get("poses", {})
        self._sequences = doc.get("sequences", {}) or {}
        lim = _load_json(self._limits_file)
        self.limits = {}
        for i in range(N_CH):
            row = lim.get(str(i)) or {}
            lo, hi = row.get("lo", 0), row.get("hi", 65535)
            self.limits[i] = (int(lo), int(hi))
        self._poses = {k: [int(v) for v in (v.get("targets") or [])]
                       for k, v in self._pose_docs.items()}

    @property
    def port(self):
        return getattr(self._hal, "port", None)

    def clamp(self, targets):
        """按 limits.json 逐通道夹紧——**任何下发前都要走这里**。"""
        out = []
        for i, v in enumerate(targets):
            lo, hi = self.limits[i]
            out.append(max(lo, min(hi, int(v))))
        return out

    # ---------- 契约 8 个 API ----------
    def list_poses(self):
        """可用姿势名（顺序不保证，要展示请自己排序）。"""
        return list(self._poses.keys())

    def pose_info(self, name: str):
        if name not in self._pose_docs:
            raise AgentError("UNKNOWN_POSE", f"没有这个姿势：{name}")
        doc = self._pose_docs[name]
        return {"label": doc.get("label") or doc.get("name_zh") or name,
                "targets": list(self._poses[name])}

    def play(self, name: str, strength: float = 1.0, secs: float = 1.6):
        """执行姿势。strength∈[0,1] 在 open 与目标之间插值；secs 是过渡时长（0＝瞬时）。"""
        if name not in self._poses:
            raise AgentError("UNKNOWN_POSE", f"没有这个姿势：{name}")
        if "open" not in self._poses:
            raise AgentError("UNKNOWN_POSE", "poses.json 里没有 open 姿势，无法算强度")

        s = max(0.0, min(1.0, float(strength)))
        open_t = self._poses["open"]
        target = self.clamp([int(round(o + (t - o) * s))
                             for o, t in zip(open_t, self._poses[name])])
        self._check_temp()
        start = self._hal.pos() or list(open_t)
        self._run_frames(start, target, secs)
        return target

    def home(self, secs: float = 1.5):
        """回张开（open），走到位为止。"""
        return self.play("open", strength=1.0, secs=secs)

    def estop(self):
        """急停：先把目标设成当前位置（卸力，1 个字节帧就生效），再慢慢回 open。"""
        pos = self._hal.pos()
        if pos:
            self._hal.send(list(pos))
            self.last_targets = list(pos)
        try:
            if "open" in self._poses:
                self._run_frames(pos or self._poses["open"], self._poses["open"], 0.6)
        except Exception:
            pass                          # 急停路径上不允许再抛错

    def read_state(self):
        """{'temps','positions','currents','t_sec'} —— 缺数据用 None，不删键。"""
        t0 = time.time()
        temps = self._hal.temp()
        positions = self._hal.pos()
        currents = self._hal.curr()
        return {
            "temps": temps,
            "positions": positions,
            "currents": currents,
            "t_sec": time.time(),
            "read_cost_sec": round(time.time() - t0, 4),
            "mock": bool(self.dry_run),
        }

    def close(self):
        try:
            pos = self._hal.pos()
            if pos:
                self._hal.send(list(pos))     # 收尾卸力
        except Exception:
            pass
        try:
            self._hal.close()
        except Exception:
            pass

    # ---------- 执行部内部用（不在契约 8 个 API 内，智能体部不许调） ----------
    def raw_send(self, targets):
        """直接下发 7 个值（已夹紧）。给 26_api 的 /targets、/channel、/nudge 用。"""
        t = self.clamp(targets)
        with self._lock:
            self._hal.send(t)
        self.last_targets = t
        return t

    def raw_pos(self):
        return self._hal.pos()

    def raw_curr(self):
        return self._hal.curr()

    def raw_temp(self):
        return self._hal.temp()

    def sequences(self):
        return dict(self._sequences)

    def pose_docs(self):
        return json.loads(json.dumps(self._pose_docs, ensure_ascii=False))

    def user_poses(self):
        return _load_json(self._user_file, {"poses": {}}).get("poses", {})

    # ---------- 内部 ----------
    def _check_temp(self):
        state = _thermal().check(self._hal.temp())
        if state == "stop":
            ch, t = _thermal().hottest(self._hal.temp())
            raise AgentError("TOO_HOT", f"通道 {ch} 温度 {t}°C ≥ 50°C，已停止下发")
        if state == "warn" and not self._warned_temp:
            self._warned_temp = True
            print("[agent] ⚠ 温度已过 45°C，注意别连续跑", flush=True)
        return state

    def _run_frames(self, start, target, secs):
        """按 min-jerk + 归一化进度下发；瞬时（secs<=0）只发最后一帧。"""
        frames = _motion().plan(start, target, secs, HZ)
        instant = len(frames) == 1 and (secs is None or secs <= 0)
        for i, frame in enumerate(frames):
            if i and not instant:
                time.sleep(1.0 / HZ)
            with self._lock:
                self._hal.send(frame)
            self.last_targets = list(frame)
            if i and i % TEMP_CHECK_EVERY == 0:
                self._check_temp()


# ─────────────────────────── 入口 ───────────────────────────

def open_hand(dry_run: bool = True, port: str | None = None) -> Hand:
    """打开手。**默认 dry_run=True**（一个字节都不发，任何电脑可跑）。

    dry_run=False 时才碰串口：找不到手抛 NO_HAND，串口被占抛 PORT_BUSY。
    """
    if dry_run:
        from .mock import MockHand
        return Hand(MockHand(), dry_run=True)
    from .hal import RealHand
    return Hand(RealHand(port=port), dry_run=False)


def _motion():
    from . import motion as m
    return m


def _thermal():
    from . import thermal as t
    return t


__all__ = ["open_hand", "Hand", "AgentError", "ERROR_CODES", "N_CH", "__version__"]
