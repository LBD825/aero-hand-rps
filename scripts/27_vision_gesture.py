"""摄像头手势识别 → 驱动 Aero Hand（2026-11-21）

**怎么做的**：MediaPipe 的 HandLandmarker 从摄像头画面里取 21 个手部关键点，
按"哪几根手指伸直"判断手势，再通过本地 HTTP API 让真手做出对应姿势。

**运行方式**（必须用 .venv312 —— mediapipe 不支持 Python 3.13）：
    双击项目根 `手势识别.bat`
或  .venv312\\Scripts\\python.exe scripts\\27_vision_gesture.py

**要识别的动作**（2026-11-21：去掉"四指"和"捏"，新增 中指/6/牛）：
    0 指伸 → rock（石头）        1 指（食指）→ one（数字1）
    仅中指 → middle_finger（比中指）  仅小指 → pinky_only（收小指）
    仅拇指伸 → thumbs_up（点赞）  食+中 → scissors（剪刀）
    食+中+无名 → three（数字3）   五指 → open（张开）
    拇指+小指 → six（手语 6）     拇指+食指+小指 → horn（牛角）
    拇指+食指（其余屈）→ gun（枪）   四指伸（拇指不伸）→ four_extend（伸四指）

**触发规则**：同一手势连续稳定 6 帧才执行一次；手势变了才会再触发（防乱触发）。
**键盘**：空格 = 开关自动执行；r = 重新读姿势列表；q = 退出。

**依赖**：.venv312（mediapipe 1.2.3 + opencv 5.1），模型在 models\\hand_landmarker.task。
⚠️ mediapipe **不认中文路径**，所以模型路径走 `C:\\AeroHandProj\\models\\...`（英文链接）。
⚠️ API 必须先在跑（`启动API.bat`），否则只会显示识别结果、不会动手。
"""
from __future__ import annotations

import json
import os
import threading
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = r"C:\AeroHandProj\models\hand_landmarker.task"      # 必须英文路径（mediapipe 限制）
ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
API = "http://127.0.0.1:8765"
STABLE_FRAMES = 24         # 稳定多少帧才触发（48fps 下≈0.5 秒；6 帧=0.125 秒会自激抖动）
COOLDOWN = 4.0             # 触发后的冷却秒数（要大于一次动作时长，否则连环触发→电流尖峰）

# 关键点索引（MediaPipe Hands 标准）
WRIST = 0
THUMB_TIP = 4
INDEX_TIP, INDEX_PIP, INDEX_MCP = 8, 6, 5
MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP = 12, 10, 9
RING_TIP, RING_PIP = 16, 14
PINKY_TIP, PINKY_PIP, PINKY_MCP = 20, 18, 17


def dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def load_token() -> str:
    p = SIM / ".api_token"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def api_get(path: str, token: str, timeout: float = 3.0):
    req = urllib.request.Request(API + path, headers={"X-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_post(path: str, obj: dict, token: str, timeout: float = 5.0):
    req = urllib.request.Request(
        API + path, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class PoseSender:
    """把下发姿势丢到后台线程，别堵住摄像头循环。

    为什么需要（2026-11-22 实测）：POST /pose {"secs":1.2} 是同步的——
    服务端要走完 1.2 秒才返回，实测一次 1.99 秒。原来在主循环里直接调，
    于是每识别到一个手势画面就冻两秒，用户感觉"识别时特别卡"。
    现在主循环只把最新手势塞进槽位，真正的 HTTP 请求在 daemon 线程里跑；
    槽位只留最新一个（旧请求没发完就被换掉），不会积压。
    """

    def __init__(self, token: str):
        self.token = token
        self._lock = threading.Lock()
        self._pending = None          # (name, secs) 或 None
        self._stop = False
        self.sent = 0
        self.errors = 0
        self.dead = False             # API 连不上时置 True
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def submit(self, name: str, secs: float) -> None:
        with self._lock:
            self._pending = (name, secs)

    def _run(self) -> None:
        while not self._stop:
            with self._lock:
                job, self._pending = self._pending, None
            if job is None:
                time.sleep(0.02)
                continue
            name, secs = job
            try:
                api_post("/pose", {"name": name, "secs": secs}, self.token, timeout=15.0)
                self.sent += 1
                print(f"[下发] {name}（{secs}s）", flush=True)
            except Exception as e:
                self.errors += 1
                print(f"[下发失败] {name}: {e}", flush=True)
                if "10061" in str(e) or "拒绝" in str(e) or "refused" in str(e).lower():
                    self.dead = True          # API 没了，主循环会把 auto 关掉
                    print("[提示] API 连不上（可能被急停杀掉了）→ 已自动关闭 auto", flush=True)

    def close(self) -> None:
        self._stop = True


def fingers_up(lms) -> tuple[list[bool], bool]:
    """返回 (四指是否伸直[食,中,无名,小指], 拇指是否外伸)。

    判据：指尖到手腕的距离 明显大于 该指 PIP 到手腕的距离（伸直时指尖更远）。
    """
    tips = [(INDEX_TIP, INDEX_PIP), (MIDDLE_TIP, MIDDLE_PIP),
            (RING_TIP, RING_PIP), (PINKY_TIP, PINKY_PIP)]
    out = []
    for tip, pip in tips:
        out.append(dist(lms[tip], lms[WRIST]) > dist(lms[pip], lms[WRIST]) * 1.12)
    # 拇指：指尖离小指根越远，说明越外展/伸直
    thumb_out = dist(lms[THUMB_TIP], lms[PINKY_MCP]) > dist(lms[THUMB_TIP], lms[INDEX_MCP]) * 1.05
    return out, thumb_out


def classify(lms) -> str | None:
    f, thumb = fingers_up(lms)
    n = sum(f)
    # —— 先判"特定组合"（这些长得像，必须比通用计数更早判）——
    if (not f[0]) and f[1] and (not f[2]) and (not f[3]) and not thumb:
        return "middle_finger"                       # 只有中指伸
    if f[3] and (not f[0]) and (not f[1]) and (not f[2]) and not thumb:
        return "pinky_only"                          # 只有小指伸
    if thumb and f[3] and (not f[0]) and (not f[1]) and (not f[2]):
        return "six"                                 # 拇指 + 小指 = 6
    if thumb and f[0] and f[3] and (not f[1]) and (not f[2]):
        return "horn"                                # 拇指 + 食指 + 小指 = 牛角
    if thumb and f[0] and (not f[1]) and (not f[2]) and (not f[3]):
        return "gun"                                 # 拇指 + 食指 = 枪
    if thumb and n == 0:
        return "thumbs_up"
    if n == 0:
        return "rock"
    if n == 1 and f[0] and not thumb:
        return "one"
    if n == 2 and f[0] and f[1]:
        return "scissors"
    if n == 3 and f[0] and f[1] and not f[3]:
        return "three"
    if n == 4 and not thumb:
        return "four_extend"                         # 四指伸、拇指收
    if n == 4 and thumb:                     # 五指都开
        return "open"
    return None


def main() -> None:
    import os

    os.environ.setdefault("GLOG_minloglevel", "2")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_py
    from mediapipe.tasks.python import vision

    token = load_token()
    # 姿势列表（用于校验要触发的名字是否存在）
    try:
        poses = api_get("/poses", token)["poses"]
        print(f"✅ 已连上 API，{len(poses)} 个姿势可用", flush=True)
    except Exception as e:
        poses = {}
        print(f"⚠ 连不上 API（{e}）—— 只显示识别结果，不会动手", flush=True)

    base = mp_py.BaseOptions(model_asset_path=MODEL)
    opts = vision.HandLandmarkerOptions(base_options=base, num_hands=1,
                                       running_mode=vision.RunningMode.VIDEO,
                                       min_hand_detection_confidence=0.5,
                                       min_hand_presence_confidence=0.5,
                                       min_tracking_confidence=0.5)
    landmarker = vision.HandLandmarker.create_from_options(opts)

    # 2026-11-21：全量版也改用"采集线程 + 最新帧"（同猜拳版），否则 12 类识别会慢得没法用
    # —— 实测同线程交替 read+推理只有 1~2fps，拆开后推理 45fps+。
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "vision27b_grab", str(Path(__file__).resolve().parent / "27b_vision_rps.py"))
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    grab = _mod.FrameGrabber(index=0, size=(960, 540))
    sender = PoseSender(token)      # 后台下发，避免阻塞画面
    print("摄像头后端:", "MSMF" if grab.backend == cv2.CAP_MSMF else "DSHOW", flush=True)

    # 2026-11-21 事故后改：**默认不自动执行**。原因：默认 True 时，启动台一开视觉，
    # 摄像头看到任何像手势的东西就会连续 POST /pose 驱动真机 → 某通道电流冲到 −562，
    # 触发 estop 把整个 API 进程杀掉。现在必须按空格显式开启。
    auto = False
    last_gesture = None
    stable = 0
    _last_logged = None      # 上一次打印过的识别结果（防重复刷屏）
    _last_log_at = 0.0
    _n_frames, _fps_t0 = 0, time.time()
    last_fire = 0.0
    fired_for = None
    t0 = time.time()

    print("窗口已开：**自动执行默认关闭**（按空格开启才会动手）；r=重读姿势，q=退出", flush=True)
    while True:
        frame = grab.latest()          # 永远取最新帧（不积压 → 不延迟）
        if frame is None:
            time.sleep(0.005)
            continue
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = landmarker.detect_for_video(mp_img, int((time.time() - t0) * 1000))

        gesture = None
        if res.hand_landmarks:
            lms = res.hand_landmarks[0]
            gesture = classify(lms)
            # 画关键点
            h, w = frame.shape[:2]
            for lm in lms:
                cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 3, (0, 255, 0), -1)
            # 画骨架（简单连线）
            for a, b in ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                         (0, 9), (9, 10), (10, 11), (11, 12), (0, 13), (13, 14), (14, 15),
                         (15, 16), (0, 17), (17, 18), (18, 19), (19, 20), (5, 9), (9, 13), (13, 17)):
                cv2.line(frame,
                         (int(lms[a].x * w), int(lms[a].y * h)),
                         (int(lms[b].x * w), int(lms[b].y * h)), (0, 200, 255), 1)

        # 稳定计数
        if gesture and gesture == last_gesture:
            stable += 1
        else:
            stable = 1 if gesture else 0
            fired_for = None
        last_gesture = gesture

        # 2026-11-22：不管 auto 开没开，**识别结果一变就打一行**（方便看日志/远程测准不准）
        # 稳定到 STABLE_FRAMES 算"识别成功"一次，冷却 GLOG_COOLDOWN 秒避免刷屏
        now = time.time()
        if gesture and stable >= STABLE_FRAMES and gesture != _last_logged and now - _last_log_at > 1.0:
            print(f"[识别] {gesture}（稳 {stable}/{STABLE_FRAMES}）", flush=True)
            _last_logged, _last_log_at = gesture, now
        elif not gesture and _last_logged and now - _last_log_at > 2.0:
            _last_logged = None

        # API 挂了就别再触发（否则一直刷失败日志）
        if getattr(sender, "dead", False) and auto:
            auto = False
            print("[安全] 已自动关闭 auto（API 不可用）", flush=True)

        # 触发
        now = time.time()
        if (auto and gesture and stable >= STABLE_FRAMES
                and fired_for != gesture and now - last_fire > COOLDOWN):
            if poses and gesture in poses:
                try:
                    # 2026-11-22 事故修复：原来 secs=0（瞬时下发），手势切换是大步跳变，
                    # 实测把电流顶到 -399 触发 estop 杀掉 API。改平滑过渡（默认 1.2 秒）。
                    secs = float(os.environ.get('VISION_POSE_SECS', '2.0'))
                    sender.submit(gesture, secs)
                    print(f"[触发] {gesture}", flush=True)
                    last_fire = now
                    fired_for = gesture
                except Exception as e:
                    print(f"[触发失败] {gesture}: {e}", flush=True)
            elif not poses:
                pass
            else:
                print(f"[跳过] 手势 {gesture} 没有对应姿势", flush=True)
                fired_for = gesture

        # 画文字
        h, w = frame.shape[:2]
        label = gesture or "（没识别到手）"
        color = (0, 255, 0) if gesture else (128, 128, 128)
        cv2.putText(frame, f"gesture: {label}", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
        cv2.putText(frame, f"stable {stable}/{STABLE_FRAMES}   auto={'ON' if auto else 'OFF'}",
                    (12, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        if fired_for:
            cv2.putText(frame, f"FIRED: {fired_for}", (12, 98),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2)
        # 每 2 秒打一行帧率，卡不卡一眼能看出来（2026-11-22 加）
        _n_frames += 1
        if time.time() - _fps_t0 >= 2.0:
            print(f"[fps] {_n_frames / (time.time() - _fps_t0):.1f}", flush=True)
            _n_frames, _fps_t0 = 0, time.time()
        cv2.imshow("Aero Hand gesture control (space=auto, r=reload, q=quit)", frame)

        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord(" "):
            auto = not auto
            print(f"自动执行：{'ON' if auto else 'OFF'}", flush=True)
        if k == ord("r"):
            try:
                poses = api_get("/poses", token)["poses"]
                print(f"已重读 {len(poses)} 个姿势", flush=True)
            except Exception as e:
                print(f"重读失败：{e}", flush=True)

    grab.close()
    cv2.destroyAllWindows()
    landmarker.close()


if __name__ == "__main__":
    main()
