"""12 种手势的准确率自测（scripts\\33_gesture_accuracy.py）

**为什么需要**：`27_vision_gesture.py` 的日志只能说明"识别到了什么"，
不知道你当时在做哪个手势，所以没法算准确率、也没法定位"哪个手势判错了"。

做法：按固定顺序逐个提示（屏幕大字 + 进度 k/12），每个手势给 3 秒窗口，
窗口内统计 classify 的众数，最后出一张「目标 → 识别 → 对/错」的表。

用法：
    py scripts\\33_gesture_accuracy.py            # 12 个全测
    py scripts\\33_gesture_accuracy.py --only rock,scissors,paper
    py scripts\\33_gesture_accuracy.py --window 3.5

注意：**不接串口、不下发动作**（纯识别），所以不需要 API、不会动手。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V27 = ROOT / "scripts" / "27_vision_gesture.py"
MODEL = ROOT / "models" / "hand_landmarker.task"

# 顺序＝提示顺序。(中文名, 期望标签, 窗口里的动作描述)
# ⚠ cv2.putText 不支持中文（只会显示方块），所以中文用 PIL 画（见 draw_cn），
#   PIL 不可用时自动退回英文 hint_en。
SEQUENCE = [
    ("石头（握拳）", "rock", "握拳：五指全收起来", "ROCK  (make a fist)"),
    ("剪刀（食+中）", "scissors", "食指＋中指笔直伸出，其余收", "SCISSORS  (index + middle)"),
    ("布（五指张开）", "open", "五指全部张开，手指伸直", "PAPER  (all 5 spread)"),
    ("数字1（只食指）", "one", "只伸食指，拇指贴掌心", "ONE  (index only, thumb in)"),
    ("点赞（只拇指）", "thumbs_up", "只伸拇指，四指握起来", "THUMBS UP  (thumb only)"),
    ("比中指", "middle_finger", "只伸中指，拇指收", "MIDDLE  (middle only)"),
    ("只伸小指", "pinky_only", "只伸小指，其余收", "PINKY  (pinky only)"),
    ("数字3（食+中+无名）", "three", "食指＋中指＋无名指笔直伸，小指收", "THREE  (index+middle+ring)"),
    ("6（拇指+小指）", "six", "拇指＋小指伸，中间三指收", "SIX  (thumb + pinky)"),
    ("牛角（拇指+食+小）", "horn", "拇指＋食指＋小指伸", "HORN  (thumb+index+pinky)"),
    ("枪（拇指+食指）", "gun", "拇指＋食指伸，其余收", "GUN  (thumb + index)"),
    ("四指伸（拇指收）", "four_extend", "四指并拢笔直伸，拇指收进掌心", "FOUR  (4 up, thumb in)"),
]

# 准备阶段常驻的一条提醒（2026-11-22 实测：这两句能避免 80% 的"认错"）
TIP_LINE = "伸出的手指要笔直；手背朝上、手指朝上；手放画面中间"


def load_v27():
    spec = importlib.util.spec_from_file_location("v27", V27)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = [sys.argv[0]]          # 防止被它的 argparse 干扰
    spec.loader.exec_module(mod)      # 有 __main__ 保护，不会跑主流程
    return mod


_CN_FONT = None
_CN_OK = False


def _init_cn_font():
    """准备中文字体（cv2.putText 画不了中文，用 PIL 画）。"""
    global _CN_FONT, _CN_OK
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                 r"C:\Windows\Fonts\simhei.ttf"):
        try:
            from PIL import ImageFont
            _CN_FONT = ImageFont.truetype(path, 40)
            _CN_OK = True
            return
        except Exception:
            continue
    _CN_OK = False


def draw_cn(bgr, text, xy, color=(0, 255, 255), size=40):
    """在画面左上角画一行中文（PIL 渲染），失败则返回原图（调用方退回英文）。"""
    if not _CN_OK:
        return bgr
    try:
        import numpy as np
        from PIL import Image, ImageDraw
        font = _CN_FONT if size == 40 else __import__("PIL.ImageFont", fromlist=["truetype"]).truetype(
            r"C:\Windows\Fonts\msyh.ttc", size)
        rgb = Image.fromarray(bgr[:, :, ::-1])
        d = ImageDraw.Draw(rgb)
        d.text(xy, text, font=font, fill=(color[2], color[1], color[0]))
        return np.array(rgb)[:, :, ::-1].copy()
    except Exception:
        return bgr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="只测这些（逗号分隔的英文标签）")
    ap.add_argument("--window", type=float, default=3.0, help="每个手势的识别窗口秒数")
    ap.add_argument("--prep", type=float, default=3.0,
                    help="每个手势先给几秒准备时间（只显示提示与倒计时，不统计）")
    ap.add_argument("--camera", type=int, default=0)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_py
    from mediapipe.tasks.python import vision

    _init_cn_font()
    print("中文提示（PIL）：" + ("✅ 可用" if _CN_OK else "❌ 不可用，窗口退回英文"), flush=True)

    v27 = load_v27()
    classify = v27.classify
    diag = v27.diag_fingers

    items = SEQUENCE
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        items = [it for it in SEQUENCE if it[1] in want]

    # FrameGrabber 在 27b 里（27 号自己也这么加载：采集线程 + 最新帧，
    # 单线程交替 read+推理只有 1~2fps）
    _spec = importlib.util.spec_from_file_location(
        "vision27b_grab", ROOT / "scripts" / "27b_vision_rps.py")
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    grab = _mod.FrameGrabber(index=args.camera, size=(960, 540))
    lm = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=mp_py.BaseOptions(model_asset_path=v27.MODEL),
        num_hands=1, running_mode=vision.RunningMode.VIDEO,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5))

    print(f"开始：共 {len(items)} 个手势，每个先准备 {args.prep:g} 秒、再识别 {args.window} 秒。q 退出。", flush=True)
    results = []
    points_dump = []          # [(期望, 中文名, [[21 个(x,y,z)], ...])] 事后可离线调阈值
    t_start = time.time()
    for k, (cn, want, hint_cn, hint_en) in enumerate(items, 1):
        print(f"\n[{k}/{len(items)}] 请做：{cn}（期望 {want}）—— 先摆好姿势", flush=True)
        # ---- 准备阶段：只显示提示 + 倒计时，不统计（用户要"慢一点"）----
        tp = time.time()
        while time.time() - tp < args.prep:
            frame = grab.latest()
            if frame is not None:
                prep_img = frame.copy()
                hh, ww = prep_img.shape[:2]
                cv2.rectangle(prep_img, (0, 0), (ww, 196), (24, 24, 24), -1)
                if _CN_OK:
                    prep_img = draw_cn(prep_img, f"第 {k}/{len(items)} 步 · {cn}", (16, 6), (0, 255, 255), 40)
                    prep_img = draw_cn(prep_img, hint_cn, (16, 58), (0, 255, 180), 34)
                    prep_img = draw_cn(prep_img, TIP_LINE, (16, 106), (150, 220, 150), 26)
                else:
                    cv2.putText(prep_img, f"STEP {k}/{len(items)}  {hint_en}", (16, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
                    cv2.putText(prep_img, "straight fingers, back of hand up, centered",
                                (16, 106), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 220, 150), 2)
                left_p = max(0.0, args.prep - (time.time() - tp))
                cv2.putText(prep_img, f"GET READY ... {left_p:0.1f}s",
                            (16, 178), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 200, 255), 2)
                cv2.imshow("gesture accuracy test  (q=quit)", prep_img)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    grab.close()
                    return 1
            else:
                time.sleep(0.02)
        # ---- 识别阶段 ----
        seen = Counter()
        diag_seen = Counter()
        points = []
        t0 = time.time()
        while time.time() - t0 < args.window:
            frame = grab.latest()
            if frame is None:
                time.sleep(0.01)
                continue
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            res = lm.detect_for_video(img, int((time.time() - t_start) * 1000))
            label = None
            if res.hand_landmarks:
                lms = res.hand_landmarks[0]
                points.append([(round(p.x, 5), round(p.y, 5), round(p.z, 5)) for p in lms])
                label = classify(lms)
                if label:
                    seen[label] += 1
                    diag_seen[diag(lms)] += 1
            # 提示画面：中文用 PIL 画（cv2.putText 不支持中文），英文作备份
            left = max(0.0, args.window - (time.time() - t0))
            big = frame.copy()
            hh, ww = big.shape[:2]
            cv2.rectangle(big, (0, 0), (ww, 158), (24, 24, 24), -1)
            if _CN_OK:
                big = draw_cn(big, f"第 {k}/{len(items)} 步 · {cn}", (16, 6), (0, 255, 255), 40)
                big = draw_cn(big, hint_cn, (16, 58), (0, 255, 180), 34)
            else:
                cv2.putText(big, f"STEP {k}/{len(items)}  {hint_en}", (16, 44),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
                cv2.putText(big, hint_en, (16, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 180), 2)
            cv2.putText(big, f"{left:0.1f}s   see: {label or '-'}", (16, 140),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (200, 200, 200), 2)
            # 底部一条进度条，一眼看出还剩几步
            bar_w = int(ww * k / len(items))
            cv2.rectangle(big, (0, hh - 10), (bar_w, hh), (0, 200, 120), -1)
            cv2.imshow("gesture accuracy test  (q=quit)", big)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                grab.close()
                return 1
        top = seen.most_common(1)[0][0] if seen else None
        got_ratio = (seen[top] / sum(seen.values()) * 100) if seen else 0.0
        ok = top == want
        results.append((cn, want, top, got_ratio, diag_seen.most_common(1)[0][0] if diag_seen else "-"))
        points_dump.append((want, cn, points))
        print(f"     → 识别 {top}（占 {got_ratio:.0f}%）  {'✅' if ok else '❌'}"
              f"  指型 {diag_seen.most_common(1)[0][0] if diag_seen else '-'}", flush=True)

    # 先把结果算完、打印、落盘，再关资源（关闭卡住也不影响出报告）
    print("\n" + "=" * 76, flush=True)
    print(f"{'手势':<16}{'期望':<14}{'识别':<14}{'占比':>6}  {'结果':<4}指型", flush=True)
    print("-" * 76, flush=True)
    good = 0
    for cn, want, top, ratio, dg in results:
        ok = top == want
        good += ok
        print(f"{cn:<16}{want:<14}{str(top):<14}{ratio:>5.0f}%  {'OK' if ok else 'XX':<4}{dg}", flush=True)
    print("=" * 76, flush=True)
    print(f"准确率 {good}/{len(results)} = {good / max(1, len(results)) * 100:.0f}%", flush=True)

    # 指出"像采集问题"的项（判据其实没错，是手型/角度没被看清）
    weak = [(cn, want, ratio) for cn, want, top, ratio, dg in results
            if top != want and ratio < 80]
    if weak:
        print("\n[提示] 下面几项更像是采集没看清，而不是判据判错（建议单独重测）：", flush=True)
        for cn, want, ratio in weak:
            print(f"    {cn}（{want}）—— 只认到 {ratio:.0f}% 的帧", flush=True)

    # 落盘一份报告（双击运行时看不到控制台也能事后查）
    out_dir = ROOT / "产物"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"手势准确率-{time.strftime('%Y%m%d-%H%M%S')}.md"
    lines = [f"# 手势识别准确率 {time.strftime('%Y-%m-%d %H:%M')}", "",
             f"窗口 {args.window} 秒/个，共 {len(results)} 个，**准确率 {good}/{len(results)} = "
             f"{good / max(1, len(results)) * 100:.0f}%**", "",
             "| 手势 | 期望 | 识别 | 占比 | 结果 | 指型 |", "|---|---|---|---|---|---|"]
    for cn, want, top, ratio, dg in results:
        lines.append(f"| {cn} | `{want}` | `{top}` | {ratio:.0f}% | "
                     f"{'✅' if top == want else '❌'} | `{dg}` |")
    out.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8-sig")
    print(f"报告已写入：{out}", flush=True)

    # 关键点落盘：以后调阈值直接读这个 json，不必再请用户做一遍手势
    import json as _json
    js = out.with_suffix(".json")
    js.write_text(_json.dumps(
        [{"want": w, "cn": cn, "n": len(pts), "points": pts} for w, cn, pts in points_dump],
        ensure_ascii=False), encoding="utf-8")
    print(f"关键点已存：{js.name}", flush=True)

    # 收尾：实测 grab.close() / lm.close() 会卡住（摄像头线程不退），
    # 而结果已经落盘了，所以直接硬退出，由操作系统回收摄像头句柄。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
