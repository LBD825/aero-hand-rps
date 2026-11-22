"""摄像头手势识别 · **石头剪刀布专用版**（27b_vision_rps.py）

为什么单独做一版（2026-11-21 用户要求）：
    全量版（27_vision_gesture.py）要分 12 种手势，判据之间容易互相抢、容错小。
    猜拳只需要分 **3 类**，可以把"中间态"全部丢掉——只认最有把握的三态，
    不确定就说"看不清"，**宁可不出拳，也不要出错拳**。M4 现场对局用这一版。

三类判据（比全量版宽松，专为猜拳）：
    rock     四指全屈（拇指随意）
    scissors 食指+中指伸，无名+小指屈（拇指随意）
    paper    四指都伸（拇指随意）

输出接口（给 M4 / RPS 主程序用）：
    Python:  from 27b_vision_rps import RpsRecognizer
             r = RpsRecognizer(); r.gesture(frame_bgr) -> 'rock'|'scissors'|'paper'|None
    命令行:  py scripts\\27b_vision_rps.py            # 开窗口看识别
             py scripts\\27b_vision_rps.py --api      # 识别到就通过本地 API 驱动真手
             py scripts\\27b_vision_rps.py --json     # 每行打印 JSON（给别的程序读）

⚠ mediapipe 不认中文路径 → 模型走 `C:\\AeroHandProj\\models\\hand_landmarker.task`
⚠ 必须用 `.venv312`（Python 3.12）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

MODEL = r"C:\AeroHandProj\models\hand_landmarker.task"
ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
API = "http://127.0.0.1:8765"

WRIST = 0
PINKY_MCP = 17
INDEX_MCP = 5
TIPS_PIPS = ((8, 6), (12, 10), (16, 14), (20, 18))   # 食/中/无/小 (tip, pip)

# ── 判据容差（猜拳版故意放宽，靠"只看三态"来保稳）──
EXTEND_RATIO = 1.10      # 指尖到腕 > PIP 到腕 × 1.10 → 伸直（全量版是 1.12）
MIN_VOTES = 4            # 连续稳定几帧才认（25 fps 下约 0.16 秒）
LABEL_TO_POSE = {"rock": "rock", "scissors": "scissors", "paper": "open"}


def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def fingers_state(lms):
    """四指是否伸直：[食指, 中指, 无名指, 小指]。纯几何，不依赖手朝向。"""
    return [_dist(lms[tip], lms[WRIST]) > _dist(lms[pip], lms[WRIST]) * EXTEND_RATIO
            for tip, pip in TIPS_PIPS]


def classify_rps(lms):
    """只返回 'rock' / 'scissors' / 'paper' / None（看不清就 None）。"""
    f = fingers_state(lms)
    n = sum(f)
    if n == 0:
        return "rock"                                  # 全屈
    if f[0] and f[1] and not f[2] and not f[3]:
        return "scissors"                              # 只有食+中
    if n == 4:
        return "paper"                                 # 四指全伸
    return None                                        # 中间态：不认


class FrameGrabber:
    """独立线程持续读帧，**只保留最新一帧**（2026-11-21 提速关键）。

    为什么必须这样：把 `cap.read()` 和 `detect_for_video()` 放在同一个循环里交替执行，
    实测整链只有 **1~2 fps**（读帧会等下一帧，推理又占着 CPU，两者互相卡）；
    拆成"采集线程 + 推理循环"后：**推理 46.8 fps、采集 28.6 fps**。
    另外 `CAP_MSMF` 比 `CAP_DSHOW` 快一倍（29.7 vs 14.9 fps），所以优先 MSMF。
    """

    def __init__(self, index: int = 0, size=(640, 480), backend=None):
        import cv2

        self._cv2 = cv2
        backends = [backend] if backend is not None else [cv2.CAP_MSMF, cv2.CAP_DSHOW]
        self.cap = None
        for b in backends:
            cap = cv2.VideoCapture(index, b)
            if cap.isOpened():
                self.cap = cap
                self.backend = b
                break
            cap.release()
        if self.cap is None:
            raise RuntimeError(f"打不开摄像头 {index}（被其它程序占用？）")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._lock = threading.Lock()
        self._frame = None
        self._stop = threading.Event()
        self.count = 0
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        while not self._stop.is_set():
            ok, fr = self.cap.read()
            if ok:
                with self._lock:
                    self._frame = fr
                    self.count += 1
            else:
                time.sleep(0.005)

    def latest(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def close(self):
        self._stop.set()
        self._th.join(timeout=1.0)
        try:
            self.cap.release()
        except Exception:
            pass


class RpsRecognizer:
    """把 mediapipe 包一层，对外只给 `gesture(frame_bgr)` 一个方法。"""

    def __init__(self, model_path: str = MODEL, min_conf: float = 0.5):
        os.environ.setdefault("GLOG_minloglevel", "2")
        import mediapipe as mp
        from mediapipe.tasks import python as mp_py
        from mediapipe.tasks.python import vision

        base = mp_py.BaseOptions(model_asset_path=model_path)
        opts = vision.HandLandmarkerOptions(
            base_options=base, num_hands=1,
            running_mode=vision.RunningMode.VIDEO,
            min_hand_detection_confidence=min_conf,
            min_hand_presence_confidence=min_conf,
            min_tracking_confidence=min_conf)
        self._lm = vision.HandLandmarker.create_from_options(opts)
        self._mp = mp
        self._t0 = time.time()
        self._votes = []
        self._last = None

    def raw(self, frame_bgr):
        """返回 (标签 or None, 21 个关键点 or None)。"""
        import cv2
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        res = self._lm.detect_for_video(img, int((time.time() - self._t0) * 1000))
        if not res.hand_landmarks:
            return None, None
        lms = res.hand_landmarks[0]
        return classify_rps(lms), lms

    def gesture(self, frame_bgr, votes: int = MIN_VOTES):
        """带稳定投票的识别：连续 votes 帧同一类才返回它，否则 None。"""
        label, _ = self.raw(frame_bgr)
        if label is None:
            self._votes = []
            return None
        self._votes.append(label)
        if len(self._votes) > votes:
            self._votes.pop(0)
        if len(self._votes) >= votes and len(set(self._votes)) == 1:
            if label != self._last:                    # 变化时才"重新报一次"
                self._last = label
            return label
        return None

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass


def _token() -> str:
    p = SIM / ".api_token"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _fire_pose(label: str) -> bool:
    pose = LABEL_TO_POSE.get(label)
    if not pose:
        return False
    req = urllib.request.Request(
        API + "/pose", data=json.dumps({"name": pose, "secs": 0}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Token": _token()})
    try:
        urllib.request.urlopen(req, timeout=4).read()
        return True
    except Exception as e:
        print(f"[api] 驱动失败：{e}", flush=True)
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", action="store_true", help="识别到就通过本地 API 驱动真手")
    ap.add_argument("--json", action="store_true", help="每行输出 JSON（给别的程序读，不开窗口）")
    ap.add_argument("--camera", type=int, default=0)
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import cv2

    rec = RpsRecognizer()
    grab = FrameGrabber(index=args.camera)      # 采集线程独立跑（MSMF 优先，约 29fps）
    print("摄像头后端:", "MSMF" if grab.backend == cv2.CAP_MSMF else "DSHOW", flush=True)

    print("猜拳识别已启动：rock / scissors / paper；q 退出", flush=True)
    last = None
    fired_at = 0.0
    try:
        while True:
            frame = grab.latest()               # 永远拿最新帧（不积压 → 不延迟）
            if frame is None:
                time.sleep(0.005)
                continue
            frame = cv2.flip(frame, 1)
            label, lms = rec.raw(frame)
            stable = rec.gesture(frame)

            if args.json:
                print(json.dumps({"label": label, "stable": stable,
                                  "t": round(time.time(), 3)}), flush=True)
            if stable and stable != last and time.time() - fired_at > 0.8:
                if args.api:
                    _fire_pose(stable)
                last, fired_at = stable, time.time()
            elif not label:
                last = None

            if not args.json:
                text = f"RPS: {label or '--'}"
                color = (0, 255, 0) if label else (120, 120, 120)
                cv2.putText(frame, text, (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
                if stable:
                    cv2.putText(frame, f"STABLE: {stable}", (12, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
                cv2.imshow("Aero Hand RPS (q quit)", frame)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    break
    finally:
        grab.close()
        if not args.json:
            cv2.destroyAllWindows()
        rec.close()


if __name__ == "__main__":
    main()
