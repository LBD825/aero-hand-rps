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
COOLDOWN = 4.0             # 默认冷却（实际用"自适应时长 + 2 秒"，见主循环）
MIN_SECS = 1.5             # 一次动作最短时长
MAX_SECS = 6.0             # 一次动作最长时长（跨半个行程时用这个）
UNITS_PER_SEC = 12000.0    # 每秒钟最多走多少个 u16 单位（跨度 ÷ 这个 = 时长）

# 关键点索引（MediaPipe Hands 标准）
WRIST = 0
THUMB_MCP, THUMB_IP, THUMB_TIP = 2, 3, 4
INDEX_TIP, INDEX_PIP, INDEX_MCP = 8, 6, 5
MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP = 12, 10, 9
RING_MCP = 13
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


def _angle_deg(a, b, c) -> float:
    """以 b 为顶点的夹角（度）。用 x,y,z 三维向量，对"手在画面里怎么转"不敏感。

    手指伸直 ≈ 170~180°，屈曲 ≈ 30~100°。比"指尖到手腕的距离"稳得多：
    后者在手倾斜或指尖朝向镜头时会失效（2026-11-22 实测：做 3 被判成全屈）。
    """
    v1 = (a.x - b.x, a.y - b.y, a.z - b.z)
    v2 = (c.x - b.x, c.y - b.y, c.z - b.z)
    n1 = math.sqrt(v1[0] ** 2 + v1[1] ** 2 + v1[2] ** 2)
    n2 = math.sqrt(v2[0] ** 2 + v2[1] ** 2 + v2[2] ** 2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cosv = (v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]) / (n1 * n2)
    cosv = max(-1.0, min(1.0, cosv))
    return math.degrees(math.acos(cosv))


def fingers_up(lms) -> tuple[list[bool], bool]:
    """返回 (四指是否伸直[食,中,无名,小指], 拇指是否外伸)。

    **判据（2026-11-22 改）**：手指伸直与否看**指节夹角** `angle(MCP, PIP, TIP)`：
      伸直 ≈ 160~180°，屈曲 ≈ 30~110°。阈值取 145° 作分界（留一点余量给半伸）。
    拇指：既要"伸直"（`angle(MCP,IP,TIP)` 够大），又要"离掌心远"
      （指尖到小指根 > 指尖到食指根），两个条件都满足才算外伸。
    旧判据（指尖到手腕距离 > PIP 到手腕距离 ×1.12）在手倾斜/指尖朝镜头时会整排判成屈曲，
    实测把"数字3"判成 T1 0000、把"枪"的食指和小指搞反。
    """
    straight_min = 145.0
    out = []
    for mcp, pip, tip in ((INDEX_MCP, INDEX_PIP, INDEX_TIP),
                          (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP),
                          (RING_MCP, RING_PIP, RING_TIP),
                          (PINKY_MCP, PINKY_PIP, PINKY_TIP)):
        out.append(_angle_deg(lms[mcp], lms[pip], lms[tip]) >= straight_min)
    # 拇指：伸直 + 外展（离小指根比离食指根更远）
    thumb_straight = _angle_deg(lms[THUMB_MCP], lms[THUMB_IP], lms[THUMB_TIP]) >= 140.0
    thumb_abduct = dist(lms[THUMB_TIP], lms[PINKY_MCP]) > dist(lms[THUMB_TIP], lms[INDEX_MCP]) * 1.02
    thumb_out = thumb_straight and thumb_abduct
    return out, thumb_out


def diag_fingers(lms) -> str:
    """一行指型摘要，用于诊断"为什么判成这个手势"。

    格式：T=拇指外伸，然后 食 中 无名 小 = 1 伸直 / 0 屈曲。
    例：T1 1 0 0 0 → 拇指伸 + 食指伸 = 应该是 gun（枪）。
    """
    try:
        four, thumb = fingers_up(lms)
        return ("T1 " if thumb else "T0 ") + " ".join("1" if f else "0" for f in four)
    except Exception:
        return "指型读取失败"


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
    cooldown = COOLDOWN
    # 姿势目标值表 + 当前目标（用来算"这次要跨多远"，从而决定动作时长）
    pose_targets = {}
    try:
        import json as _json
        _doc = _json.loads((Path(__file__).resolve().parent.parent
                           / "sim2real" / "poses.json").read_text(encoding="utf-8-sig"))
        for _n, _p in (_doc.get("poses") or {}).items():
            _t = _p.get("targets") if isinstance(_p, dict) else _p
            if _t and len(_t) == 7:
                pose_targets[_n] = [int(v) for v in _t]
    except Exception as _e:
        print(f"⚠ 读不到 poses.json 的目标值（自适应速度会退化为固定时长）：{_e}", flush=True)
    cur_targets = pose_targets.get("open", [0] * 7)
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
            print(f"[识别] {gesture}（稳 {stable}/{STABLE_FRAMES}）指型={diag_fingers(lms)}",
                  flush=True)
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
                and fired_for != gesture and now - last_fire > cooldown):
            if poses and gesture in poses:
                try:
                    # 2026-11-22 二次事故修复：固定 2 秒不够 —— 各姿势之间 **ch0（拇指外展）
                    # 跨度最大到 51000**（行程只有 0~53000），2 秒走完半个行程会把舵机顶到
                    # 电流 -477，触发 estop 把 API 杀掉。
                    # 现在按"最大通道跨度"自适应时长：跨度越大越慢，冷却跟着时长走。
                    tgt = pose_targets.get(gesture)
                    if tgt:
                        span = max(abs(int(tgt[i]) - int(cur_targets[i])) for i in range(7))
                    else:
                        span = 0
                    secs = min(MAX_SECS, max(MIN_SECS, span / UNITS_PER_SEC))
                    # 允许环境变量强制覆盖（用户要"瞬时"时设 VISION_POSE_SECS=0）：
                    # 0 = 瞬时下发。⚠ 跨 5 万的姿势瞬时切会顶到电流上限触发急停，
                    # 这是用户明确要求后才这么跑的。
                    _ovr = os.environ.get("VISION_POSE_SECS")
                    if _ovr is not None:
                        secs = max(0.0, float(_ovr))
                    sender.submit(gesture, secs)
                    if tgt:
                        cur_targets = [int(v) for v in tgt]
                    cooldown = secs + 2.0          # 冷却＝动作时长 + 余量，避免连环触发
                    print(f"[触发] {gesture} 指型={diag_fingers(lms)} 跨度={span} 时长={secs:.1f}s",
                          flush=True)
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
