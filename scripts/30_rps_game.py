"""RPS 人机对局（30）—— 状态机 + 界面 + 摄像头 + 机械手。

★ 怎么跑（默认全离线，不碰串口、不开摄像头）
    py scripts\\30_rps_game.py                        # 图形界面 + 假手 + 模拟人类（最安全）
    py scripts\\30_rps_game.py --cli --rounds 20      # 命令行连打 20 轮（M0 验收用这条）
    py scripts\\30_rps_game.py --cli --strategy llm    # 用大模型出拳（走云端 Flash）
    py scripts\\30_rps_game.py --live                 # 真机 + 图形界面（需要手在手边）
    py scripts\\30_rps_game.py --cam                  # 摄像头认人出拳（需 .venv312）

★ 三种"人出拳"来源（按优先级）
    --cam         摄像头识别（复用 27_vision_gesture.classify，只认 rock/scissors/open）
    界面按钮       图形界面里点 石头/剪刀/布；或键盘 1/2/3
    模拟人类       --no-cam 时的默认，用 rps.humans 里的模型自动出拳（M0 用）

★ 环境提醒（真机 + 摄像头同进程）
    摄像头要 mediapipe（只有 `.venv312` 有），串口要 pyserial（主 `.venv` 有）。
    所以"真机+摄像头"要用 **.venv312** 跑，并先装一行：`.venv312\\Scripts\\pip install pyserial`。
    只跑 dry-run / 命令行时，两个环境都行。

★ 安全
    · 默认 `dry_run=True`：一个字节都不发，只打印"将要下发的 7 个值"；
    · `--live` 才碰串口；急停（Esc/按钮）→ `agent.estop()`；
    · 对局进程自己占串口，**不要同时开 24 手动控制台 / 26 API / 监控-温度**。
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from rps import backends as B          # noqa: E402
from rps import flow as F              # noqa: E402
from rps import humans as H            # noqa: E402
from rps import llm as L               # noqa: E402
from rps import persona as P           # noqa: E402

ZH = {"rock": "✊ 石头", "paper": "✋ 布", "scissors": "✌ 剪刀"}
RESULT_ZH = {"human": "你赢了", "robot": "机械手赢了", "draw": "平局"}


# ─────────────────────────── 人出拳的三种来源 ───────────────────────────

class AutoHuman:
    """模拟人类（M0 用）：内部就是一个 humans.py 的模型。"""

    def __init__(self, kind="wsls", seed=None):
        self.model = H.make_human(kind, seed=seed)
        self.opp, self.mine = [], []

    def __call__(self):
        move = self.model.decide(self.opp, self.mine)
        return move

    def feedback(self, human_move, robot_move, result_for_human):
        self.model.observe(human_move, robot_move, result_for_human)
        self.opp.append(robot_move)
        self.mine.append(human_move)


class KeyHuman:
    """键盘/界面出拳：阻塞等人按 1/2/3（会 print 提示）。"""

    KEYS = {"1": "rock", "2": "scissors", "3": "paper"}

    def __call__(self):
        while True:
            k = input("你出拳（1=石头 2=剪刀 3=布，q=退出）：").strip().lower()
            if k in self.KEYS:
                return self.KEYS[k]
            if k == "q":
                raise SystemExit(0)
            print("只认 1/2/3")


class LiveVision:
    """**常开**摄像头视觉：采集线程 + 识别线程一直在跑，谁想看就读最新状态。

    2026-11-21 用户要求：「摄像头预览几乎不动，还有只有点击下一轮才会激活，不要，要一直激活」。
    所以这里把采集和识别都做成后台常驻线程，界面定时器只负责"读最新帧 + 最新标签"。
    """

    STABLE = 3

    def __init__(self, index=0):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "vision27b", os.path.join(HERE, "27b_vision_rps.py"))
        self.V = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.V)
        self.grab = self.V.FrameGrabber(index=index, size=(640, 480))   # 采集线程常开
        self.rec = self.V.RpsRecognizer()
        self.frame = None          # 最新原始帧（BGR，未翻转）
        self.label = None          # 最新单帧识别结果
        self.cand = None           # 候选
        self.streak = 0            # 连续帧数
        self.stable = None         # 稳定结果（>=STABLE 帧）
        self.stable_seq = 0        # 稳定结果序号（用来等"新的一次"）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.fps = 0.0
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        import cv2
        n = 0
        t0 = time.time()
        while not self._stop.is_set():
            fr = self.grab.latest()
            if fr is None:
                time.sleep(0.005)
                continue
            fr = cv2.flip(fr, 1)
            label, _ = self.rec.raw(fr)
            with self._lock:
                self.frame = fr
                self.label = label
                if label is None:
                    self.cand, self.streak = None, 0
                else:
                    if label == self.cand:
                        self.streak += 1
                    else:
                        self.cand, self.streak = label, 1
                    if self.streak >= self.STABLE:
                        if self.stable != label:
                            self.stable = label
                            self.stable_seq += 1
            n += 1
            if n % 20 == 0:
                self.fps = n / max(time.time() - t0, 1e-6)

    def snapshot(self):
        with self._lock:
            return (None if self.frame is None else self.frame.copy(),
                    self.label, self.streak, self.cand, self.stable, self.stable_seq)

    def wait_stable(self, timeout=8.0):
        """等到"新的一次稳定结果"，超时抛 TimeoutError。"""
        with self._lock:
            base = self.stable_seq
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                if self.stable and self.stable_seq != base:
                    return self.stable
            time.sleep(0.02)
        raise TimeoutError("没识别到出拳（把手放正、手背朝镜头，光线要够）")

    def close(self):
        self._stop.set()
        self._th.join(timeout=1.0)
        try:
            self.rec.close()
        except Exception:
            pass
        try:
            self.grab.close()
        except Exception:
            pass


class CameraHuman:
    """对局用的人出拳来源：包一层 LiveVision，等一次新的稳定结果。"""

    def __init__(self, index=0, timeout=8.0, preview=None):
        self.live = LiveVision(index)
        self.timeout = timeout
        self.preview = preview          # 预览改由界面定时器统一刷新，这里保留参数兼容

    def __call__(self):
        print(f"摄像头出拳：{self.timeout:.0f} 秒内出手（常开识别中）", flush=True)
        mv = self.live.wait_stable(self.timeout)
        return mv

    def close(self):
        self.live.close()


# ─────────────────────────── 命令行模式（M0 验收） ───────────────────────────

def run_cli(args):
    persona = P.Persona(args.persona, dry_run=args.dry_run or not L.load_key())
    hand = None if args.no_hand else _open_hand(args)
    game = F.Game(strategy=args.strategy, persona=persona, hand=hand,
                  dry_run=args.dry_run, rounds_per_bout=args.rounds_per_bout,
                  timing=F.Timing(think=args.think), seed=args.seed)

    cam = None
    if args.cam and not args.no_cam:
        try:                                   # 2026-11-21 修复：--cli --cam 以前不会初始化摄像头
            cam = CameraHuman(args.cam_index, timeout=8.0)
            get_human = cam
            print("[相机] 已启用（猜拳专用识别，4 帧稳定）", flush=True)
        except Exception as e:
            print(f"[相机] 不可用（{e}）→ 改用键盘", flush=True)
            get_human = KeyHuman()
    elif args.no_cam:
        auto = AutoHuman(args.auto_human, seed=args.seed)
        get_human = auto.__call__
    else:
        get_human = KeyHuman()

    game.start_bout()
    ok = 0
    for i in range(args.rounds):
        try:
            ev = game.play_round(get_human)
        except TimeoutError as e:
            print(f"[跳过] {e}")
            continue
        except SystemExit:
            break
        if ev.get("type") != "reveal":
            continue
        ok += 1
        if args.no_cam and hasattr(get_human, "__self__"):
            get_human.__self__.feedback(ev["human"], ev["robot"],
                                        -B.outcome(ev["robot"], ev["human"]))
        print(f"    {game.score}  时间戳 {'✅' if ev['order_ok'] else '❌'}  "
              f"通道={ev['channel']}")
    if game.history:
        ev = game.finish_bout()
        print(f"\n本局结束：机器人 {ev['robot_wins']} / 你 {ev['human_wins']} / 平 {ev['draws']}"
              f" → {ev['winner']}")
    print(f"\n共 {ok} 轮；比分 {game.score}")
    if game._llm is not None:
        print(f"LLM 统计：{game._llm.stats()}")
    if hand is not None:
        hand.close()


# ─────────────────────────── 图形界面 ───────────────────────────

def run_gui(args):
    import queue
    import tkinter as tk
    from tkinter import ttk

    persona = P.Persona(args.persona, dry_run=args.dry_run or not L.load_key())
    hand = None if args.no_hand else _open_hand(args)
    game = F.Game(strategy=args.strategy, persona=persona, hand=hand,
                  dry_run=args.dry_run, rounds_per_bout=args.rounds_per_bout,
                  timing=F.Timing(think=args.think), seed=args.seed)

    cam = None
    if args.cam:
        try:
            cam = CameraHuman(args.cam_index, timeout=game.timing.human)
        except Exception as e:
            print(f"[相机] 不可用（{e}）→ 改用按钮/键盘", flush=True)

    root = tk.Tk()
    root.title("Aero Hand · 石头剪刀布")
    root.geometry("760x620")
    box = queue.Queue()

    big = tk.StringVar(value="点「下一轮」开始")
    score_v = tk.StringVar(value="比分  你 0 : 0 机械手（平 0）")
    say = tk.StringVar(value="")
    degr = tk.StringVar(value="")          # 降级提示（总指挥要求：必须界面可见）
    hint_v = tk.StringVar(value="")        # 决策参考（六路统计，现场讲解用）
    log_v = tk.StringVar(value="")
    busy = {"v": False}

    tk.Label(root, textvariable=big, font=("Microsoft YaHei", 26, "bold"),
             fg="#1d4ed8").pack(pady=10)
    tk.Label(root, textvariable=score_v, font=("Microsoft YaHei", 14)).pack()
    who_v = tk.StringVar(value="当前出拳方：—")
    tk.Label(root, textvariable=who_v, font=("Microsoft YaHei", 11, "bold"),
             fg="#0f766e").pack()
    sayrow = ttk.Frame(root)
    sayrow.pack(pady=6)
    try:                                   # 大肥鱼头像（没有素材就用 assets 里生成的那张）
        from PIL import Image as _Img, ImageTk as _ImgTk
        _av = _Img.open(str(HERE.parent / "assets" / "头像-大肥鱼.png")).resize((64, 64))
        avatar_img = _ImgTk.PhotoImage(_av)
        tk.Label(sayrow, image=avatar_img).pack(side="left", padx=8)
    except Exception:
        tk.Label(sayrow, text="🐟", font=("Microsoft YaHei", 30)).pack(side="left", padx=8)
    tk.Label(sayrow, textvariable=say, font=("Microsoft YaHei", 13), fg="#7c3aed",
             wraplength=620, justify="left").pack(side="left")
    tk.Label(root, textvariable=degr, font=("Microsoft YaHei", 11, "bold"),
             fg="#b91c1c", wraplength=700, justify="center").pack()
    tk.Label(root, textvariable=hint_v, font=("Consolas", 9), fg="#6b7280",
             wraplength=720, justify="center").pack(pady=2)
    tk.Label(root, textvariable=log_v, font=("Consolas", 9), fg="#555",
             wraplength=720, justify="left").pack(pady=2)

    ttk.Label(root, text="🗨 垃圾话记录", font=("Microsoft YaHei", 10, "bold"),
              foreground="#6b7280").pack(anchor="w", padx=14)
    talk = tk.Text(root, height=4, font=("Microsoft YaHei", 10), bg="#fafafa",
                   fg="#374151", wrap="word")
    talk.pack(fill="x", padx=14, pady=(0, 4))
    talk.configure(state="disabled")

    row = ttk.Frame(root)
    row.pack(pady=4)
    tk.Label(root, text="（下面三个按钮是「兜底」：摄像头认不到你时，点它们代替出手）",
             font=("Microsoft YaHei", 9), fg="#9ca3af").pack()
    mv = {}
    for key, m in (("1", "rock"), ("2", "scissors"), ("3", "paper")):
        b = ttk.Button(row, text=f"兜底：{ZH[m]}", state="disabled",
                       command=lambda mm=m: box.put(("human", mm)))
        b.pack(side="left", padx=3)
        mv[m] = b
    nxt = ttk.Button(row, text="下一轮", command=lambda: box.put(("start", None)))
    nxt.pack(side="left", padx=10)
    ttk.Button(row, text="急停 Esc", command=lambda: box.put(("estop", None))).pack(side="left")

    # ── 摄像头小窗 + 识别状态（2026-11-21 用户要求：要看得见）──
    camrow = ttk.Frame(root)
    camrow.pack(pady=6)
    cambox = tk.Frame(camrow, bg="#1f2937", bd=2, relief="solid")
    cambox.pack(side="left", padx=8)
    tk.Label(cambox, text="📷 摄像头预览", bg="#1f2937", fg="#e5e7eb",
             font=("Microsoft YaHei", 9)).pack(anchor="w", padx=6, pady=(4, 0))
    cam_img = tk.Label(cambox, width=360, height=203, bg="#0b1220",
                       text="等待摄像头…", fg="#6b7280", font=("Microsoft YaHei", 10))
    cam_img.pack(padx=6, pady=(2, 6))
    cam_txt = tk.StringVar(value="摄像头：未启用")
    cam_col = tk.Label(camrow, textvariable=cam_txt, font=("Microsoft YaHei", 13, "bold"),
                       fg="#374151", width=22, justify="left", anchor="w")
    cam_col.pack(side="left", padx=8)

    def cam_tick():
        """**常开**：每 100ms 读一次最新帧与最新识别状态（不依赖点按钮）。"""
        if cam is not None:
            try:
                import cv2 as _cv2
                from PIL import Image, ImageDraw, ImageFont, ImageTk
                fr, label, streak, cand, stable, _seq = cam.live.snapshot()
                if fr is not None:
                    rgb = _cv2.cvtColor(fr, _cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(rgb).resize((360, 203))
                    d = ImageDraw.Draw(img)
                    d.rectangle([0, 165, 360, 203], fill=(15, 23, 42))
                    try:
                        fnt = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
                    except Exception:
                        fnt = ImageFont.load_default()
                    zh = {"rock": "石头", "scissors": "剪刀", "paper": "布"}
                    if label:
                        txt, col = f"{zh.get(label, label)}  {streak}/3", (52, 211, 153)
                    elif cand:
                        txt, col = f"不稳 {zh.get(cand, cand)}  {streak}/3", (251, 191, 36)
                    else:
                        txt, col = "未检测到手", (148, 163, 184)
                    d.text((10, 171), txt, font=fnt, fill=col)
                    ph = ImageTk.PhotoImage(img)
                    cam_img.configure(image=ph, text="")
                    cam_img.image = ph
                    if stable:
                        cam_txt.set(f"已识别：{zh.get(stable, stable)}\n（常开 · {cam.live.fps:.0f} fps）")
                        cam_col.configure(fg="#059669")
                    else:
                        cam_txt.set(f"识别中…\n（常开 · {cam.live.fps:.0f} fps）")
                        cam_col.configure(fg="#6b7280")
            except Exception:
                pass
        root.after(100, cam_tick)

    root.after(300, cam_tick)

    top = ttk.Frame(root)
    top.pack(pady=2)
    ttk.Label(top, text="策略：").pack(side="left")
    strat = tk.StringVar(value=args.strategy)
    ttk.Combobox(top, textvariable=strat, state="readonly", width=16,
                 values=[k for k, _ in B.choices()] + ["llm"]).pack(side="left", padx=4)
    ttk.Label(top, text="人设：").pack(side="left")
    per = tk.StringVar(value=persona.persona_id)
    ttk.Combobox(top, textvariable=per, state="readonly", width=12,
                 values=P.available()).pack(side="left", padx=4)
    ttk.Label(top, text="（下一局生效）", foreground="#777").pack(side="left")

    def refresh():
        score_v.set(f"比分  你 {game.score['human']} : {game.score['robot']} 机械手"
                    f"（平 {game.score['draw']}）")

    def worker(_mode):
        """下一轮：后台线程跑，界面不卡。人出拳来源＝相机 > 按钮。"""
        nonlocal cam
        busy["v"] = True
        nxt.config(state="disabled")
        for b in mv.values():
            b.config(state="normal")
        if cam is not None:
            def get_human():
                """3-2-1 倒计时 **与** 摄像头识别同时开始（用户要求：出拳同时识别）。"""
                box.put(("countdown", None))
                t0 = time.time()
                try:
                    mv = cam()                       # 摄像头认人（8 秒窗口）
                except Exception:
                    box.put(("log", "相机没认到，请点兜底按钮"))
                    return _wait_click()
                rest = 2.6 - (time.time() - t0)      # 保证 3-2-1 完整走完
                if rest > 0:
                    time.sleep(rest)
                return mv
        else:
            get_human = _wait_click
        try:
            game.start_bout() if not game.history else None
            n0 = len(game.events)
            ev = game.play_round(get_human)
            for e in game.events[n0:]:          # 把降级/参考/出拳方/恢复事件转给界面
                if e.get("type") in ("degraded", "hints", "decider", "recovered"):
                    box.put((e["type"], e))
            if ev.get("type") == "reveal":
                box.put(("reveal", ev))
                if game.bout_done():
                    box.put(("bout", game.finish_bout()))
        except Exception as e:
            box.put(("log", f"出错：{type(e).__name__} {e}"))
        finally:
            busy["v"] = False
            for b in mv.values():
                b.config(state="disabled")
            nxt.config(state="normal")

    pending = {"ev": None}

    def _wait_click():
        while True:
            try:
                kind, val = box.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "human":
                return val
            if kind == "estop":
                raise SystemExit(0)
            pending.setdefault("ev", None)

    def drain():
        try:
            while True:
                kind, val = box.get_nowait()
                if kind == "human":
                    box.put((kind, val))     # 交回给 worker
                    break
                if kind == "start":
                    degr.set("")
                    hint_v.set("")
                    threading.Thread(target=worker, args=("start",), daemon=True).start()
                elif kind == "degraded":
                    degr.set(f"⚠ {val.get('reason') or 'LLM 不可用'} —— 本轮按规则出拳")
                elif kind == "decider":
                    lab = val.get("label") or "Hedge"
                    who_v.set(f"当前出拳方：{lab}")
                elif kind == "recovered":
                    lab = val.get("label") or "LLM"
                    who_v.set(f"当前出拳方：{lab}")
                    log_v.set(f"已恢复 {lab}")            # 灰字一行（总指挥要求）
                elif kind == "hints":
                    hint_v.set(val.get("text") or "")
                elif kind == "reveal":
                    ev = val
                    big.set(f"你 {ZH[ev['human']]}  vs  机械手 {ZH[ev['robot']]}"
                            f"  →  {RESULT_ZH[ev['result']]}")
                    say.set(ev.get("taunt") or "")
                    refresh()
                    t = (ev.get("taunt") or "").strip()
                    if t:
                        talk.configure(state="normal")
                        talk.insert("end", f"第{ev['round']}轮 大肥鱼：{t}\n")
                        talk.see("end")
                        talk.configure(state="disabled")
                    log_v.set(f"第{ev['round']}轮 通道={ev['channel']}"
                              f"  耗时={ev['latency']}  时间戳顺序={'OK' if ev['order_ok'] else 'FAIL'}")
                elif kind == "bout":
                    big.set(f"本局结束：机器人 {val['robot_wins']} / 你 {val['human_wins']}"
                            f" / 平 {val['draws']}")
                    refresh()
                elif kind == "cam":
                    frame, label, streak, cand = val
                    try:
                        import cv2 as _cv2
                        from PIL import Image, ImageDraw, ImageFont, ImageTk
                        rgb = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)
                        img = Image.fromarray(rgb).resize((360, 203))
                        d = ImageDraw.Draw(img)
                        # 画面底部半透明信息条
                        d.rectangle([0, 165, 360, 203], fill=(15, 23, 42))
                        try:
                            fnt = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
                        except Exception:
                            fnt = ImageFont.load_default()
                        zh = {"rock": "石头", "scissors": "剪刀", "paper": "布"}
                        if label:
                            txt = f"{zh.get(label, label)}  {streak}/3"
                            col = (52, 211, 153)
                        elif cand:
                            txt = f"不稳 {zh.get(cand, cand)}  {streak}/3"
                            col = (251, 191, 36)
                        else:
                            txt = "未检测到手"
                            col = (148, 163, 184)
                        d.text((10, 171), txt, font=fnt, fill=col)
                        ph = ImageTk.PhotoImage(img)
                        cam_img.configure(image=ph, text="")
                        cam_img.image = ph          # 防 GC
                    except Exception:
                        pass
                    if label:
                        cam_txt.set(f"识别中：{ZH.get('paper' if label == 'paper' else label, label)}"
                                    f"  {streak}/4")
                        cam_col.configure(fg="#059669")
                    elif cand:
                        cam_txt.set(f"不稳：{cand} {streak}/4")
                        cam_col.configure(fg="#b45309")
                    else:
                        cam_txt.set("未检测到手\n（正对镜头、手背朝前）")
                        cam_col.configure(fg="#6b7280")
                elif kind == "countdown":
                    for i, txt in enumerate(("3", "2", "1", "出拳！")):
                        root.after(i * 800, lambda t=txt: big.set(t))
                elif kind == "log":
                    log_v.set(str(val))
                elif kind == "estop":
                    raise SystemExit(0)
        except queue.Empty:
            pass
        root.after(60, drain)

    def on_estop():
        try:
            if hand is not None:
                hand.estop()
        except Exception:
            pass
        big.set("已急停")
        root.after(300, root.destroy)

    root.bind("<Escape>", lambda e: on_estop())
    for k, m in (("1", "rock"), ("2", "scissors"), ("3", "paper")):
        root.bind(k, lambda e, mm=m: box.put(("human", mm)))

    refresh()
    root.after(60, drain)
    try:
        root.mainloop()
    finally:
        if cam is not None:
            cam.close()
        if hand is not None:
            hand.close()


# ─────────────────────────── 公共 ───────────────────────────

def _open_hand(args):
    from agent import open_hand
    h = open_hand(dry_run=args.dry_run)
    print(f"[手] {'DRY-RUN（不碰串口）' if args.dry_run else '真机 ' + str(h.port)}", flush=True)
    return h


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Aero Hand · 石头剪刀布人机对局")
    ap.add_argument("--cli", action="store_true", help="命令行模式（不开窗口）")
    ap.add_argument("--no-cam", action="store_true", help="不用摄像头（模拟人类/键盘）")
    ap.add_argument("--cam", action="store_true", help="用摄像头认人出拳（需 .venv312）")
    ap.add_argument("--cam-index", type=int, default=0)
    ap.add_argument("--auto-human", default="wsls",
                    help="模拟人类类型：" + ",".join(H.ALL_KINDS))
    ap.add_argument("--strategy", default="hedge",
                    choices=[k for k, _ in B.choices()] + ["llm"], help="机器人策略")
    ap.add_argument("--persona", default=P.load_cfg()["persona_id"], choices=P.available())
    ap.add_argument("--rounds", type=int, default=20, help="--cli 模式打几轮")
    ap.add_argument("--rounds-per-bout", type=int, default=5)
    ap.add_argument("--think", type=float, default=3.0,
                    help="LLM 思考窗口（秒）。实测 p95 1.18s，默认 3 秒")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--live", action="store_true", help="真机（默认 dry-run，一个字节不发）")
    ap.add_argument("--no-hand", action="store_true", help="连假手都不用（纯逻辑）")
    args = ap.parse_args()
    args.dry_run = not args.live

    print("=" * 60)
    print(f" 策略={args.strategy}  人设={args.persona}  "
          f"{'DRY-RUN' if args.dry_run else '真机'}  "
          f"{'摄像头' if args.cam else ('模拟人类' if args.no_cam else '键盘')}")
    print("=" * 60)
    (run_cli if args.cli else run_gui)(args)


if __name__ == "__main__":
    main()
