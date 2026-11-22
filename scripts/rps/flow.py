"""对局状态机（flow 层）—— 一局的骨架：决策 → 锁定 → 垃圾话 → 人出手 → 裁判 → 下发。

★ 每轮的真实顺序（用户 2026-11-21 口述，代码按此实现）
    ① N3 决策：只拿历史（**签名里没有 human_move，物理拿不到**）
    ② N4 锁定：写 `sim2real\\rps_lock.json`，写盘即锁、之后只读
    ③ N0 垃圾话：人设说 1~2 句（2s 上限，失败跳过）
    ④ 人出拳（摄像头/键盘/自动）
    ⑤ N5 裁判 + 记录 + N6 下发（机械手在揭晓这一瞬动）
    六个时间戳进日志，顺序必须是 decide < lock < human < reveal。

★ 为什么垃圾话排在锁定之后
    嘴上再狂也改不了已经写盘的出拳 → 公平性天然成立，不用额外证明。

★ LangChain
    装了 `langchain-core` 就用 LCEL 管道把这些步骤串起来（`build_chain`）；
    没装就走同签名的纯函数（`run_round`）。两条路径行为一致，功能不减。
"""
from __future__ import annotations

import json
import os
import time

from . import backends as B
from . import humans as H

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIM = os.path.join(ROOT, "sim2real")
LOCK_FILE = os.path.join(SIM, "rps_lock.json")
HISTORY_FILE = os.path.join(SIM, "rps_history.json")

# rps_history.json 的写者身份：**固定串**，不写进程号（进程号每次都不一样，查不出是谁）。
# 2026-11-22 总指挥更正：这个字段归 RPS 线改（它在本文件里），执行部只验收。
WRITER_ID = "rps-line"

MOVES = B.MOVES
JUDGE = B.outcome            # judge(robot, human) → +1 机器人赢 / 0 平 / -1 输

# RPS 的出拳名 → poses.json 里的姿势名（布没有独立姿势，用 open）
POSE_OF = {"rock": "rock", "scissors": "scissors", "paper": "open"}


def _now():
    return time.time()


def _iso(ts=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or _now()))


class Timing:
    """时序配置（都可调）。思考窗口默认 **3 秒**：实测 LLM p95 只有 1.18s，
    给 8 秒等于每轮白等（2026-11-21 用户拍板改 3s）。"""

    def __init__(self, think=3.0, taunt=2.0, human=5.0, reveal=4.0):
        self.think = think
        self.taunt = taunt
        self.human = human
        self.reveal = reveal

    def as_dict(self):
        return {"think": self.think, "taunt": self.taunt,
                "human": self.human, "reveal": self.reveal}


def write_lock(round_no, move, strategy):
    """写锁文件：写盘即锁。之后只读，任何人不许改。"""
    os.makedirs(SIM, exist_ok=True)
    payload = {"round": round_no, "move": move, "strategy": strategy,
               "locked_at": _iso()}
    tmp = LOCK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOCK_FILE)          # 原子写：要么没有，要么完整
    return payload


def read_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _result_name(r):
    return {1: "robot", 0: "draw", -1: "human"}[r]


class Game:
    """一局（多轮）的状态机。GUI 与命令行共用。"""

    def __init__(self, strategy="hedge", persona=None, hand=None, dry_run=True,
                 rounds_per_bout=5, timing=None, seed=None, use_llm=False,
                 verbose=True):
        self.strategy = strategy
        self.persona = persona
        self.hand = hand
        self.dry_run = dry_run
        self.rounds_per_bout = rounds_per_bout
        self.timing = timing or Timing()
        self.verbose = verbose
        self.history = []                  # [{round, human, robot, result, mode}]
        self.score = {"human": 0, "robot": 0, "draw": 0}
        self.round_no = 0
        self.bout = 1
        self._backend = None
        self._llm = None
        self._seed = seed
        self.llm_fail_streak = 0
        self.llm_off_for_bout = False
        self._last_degraded = False          # 上一轮是否降级（用来发 recovered 事件）
        self.events = []
        self.before_move = None            # 可选回调：(round_no, robot, human) → 真机动手前调用（倒计时用）
        if strategy == "llm" or use_llm:
            from .llm import LLMDecider
            self._llm = LLMDecider()
        else:
            self._backend = B.make_backend(strategy, seed=seed)

    # ---------- 日志 ----------
    def log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def emit(self, event):
        self.events.append(event)
        return event

    # ---------- ① 决策 ----------
    def decide(self, history, my_moves):
        """只用历史。**注意签名里没有 human_move**——这是公平性的实现保证。"""
        t0 = _now()
        if self._llm is not None and not self.llm_off_for_bout:
            try:
                move, info = self._llm.decide(history, my_moves, temperature=0.2)
                self.llm_fail_streak = 0
                return move, {"decide_ts": t0, "channel": info.get("channel", "llm"),
                              "degraded": False, "hints": info.get("hints"),
                              "latency": info.get("latency")}
            except Exception as e:
                self.llm_fail_streak += 1
                err = str(e)[:160]
                # 区分两种失败：模型在想但超时 / 根本连不上（界面文案不一样）
                is_timeout = type(e).__name__ == "LLMTimeout"
                reason = ("思考超时（模型没算完）" if is_timeout else "连不上模型")
                if self.llm_fail_streak >= 2:
                    self.llm_off_for_bout = True
                    self.log("[决策] LLM 连续失败 2 次 → 本局改用规则（Hedge）")
                if self._backend is None:
                    self._backend = B.make_backend("hedge", seed=self._seed)
                move = self._backend.decide(history, my_moves)
                return move, {"decide_ts": t0, "channel": "rules", "degraded": True,
                              "error": err, "reason": reason,
                              "timeout": is_timeout}
        if self._backend is None:
            self._backend = B.make_backend("hedge", seed=self._seed)
        move = self._backend.decide(history, my_moves)
        return move, {"decide_ts": t0, "channel": "rules"}

    # ---------- ②~⑤ 一轮 ----------
    def play_round(self, get_human, wait=True):
        """跑一轮。`get_human()` 必须返回 'rock'/'paper'/'scissors'（阻塞式取人出拳）。

        返回事件 dict（含四个时间戳、双方出拳、结果、下发目标）。
        """
        self.round_no += 1
        history = [r["human"] for r in self.history]
        my_moves = [r["robot"] for r in self.history]

        # ① 决策（只给历史）
        robot, dmeta = self.decide(history, my_moves)
        self.emit({"type": "decided", "move": robot, **dmeta})
        # 常驻显示"当前出拳方"（总指挥要求：观众随时能看出是谁在打）
        ch = dmeta.get("channel") or "rules"
        label = "LLM" if ch in ("flash", "local", "llm") else "Hedge"
        self.emit({"type": "decider", "channel": ch, "label": label})
        if dmeta.get("degraded"):
            # 总指挥要求：降级必须让界面看得见，不能只在控制台
            self.emit({"type": "degraded",
                       "reason": dmeta.get("reason") or "LLM 不可用",
                       "detail": dmeta.get("error"),
                       "timeout": bool(dmeta.get("timeout"))})
            self.log(f"[降级] {dmeta.get('reason')}（本轮改用规则出拳）")
        elif self._last_degraded:
            # 上一轮是规则、这一轮回到 LLM → 告诉界面"已恢复"
            self.emit({"type": "recovered", "label": label, "channel": ch})
            self.log(f"[恢复] LLM 已恢复，本轮由 {label} 出拳")
        self._last_degraded = bool(dmeta.get("degraded"))
        if dmeta.get("hints"):
            self.emit({"type": "hints", "text": dmeta["hints"]})
            self.log("[参考] " + dmeta["hints"])

        # ② 锁定
        lock = write_lock(self.round_no, robot, self.strategy)
        lock_ts = _now()
        self.emit({"type": "locked", "move": robot, "lock_ts": lock_ts,
                   "locked_at": lock["locked_at"]})
        self.log(f"[锁定] 第 {self.round_no} 轮 → 已写入 rps_lock.json（{dmeta.get('channel')}）"
                 + (f"，耗时 {dmeta.get('latency')}s" if dmeta.get("latency") else ""))

        # ③ 垃圾话（锁定之后；失败不影响比赛）
        taunt = ""
        if self.persona is not None:
            taunt = self.persona.talk("taunt", {"score": self.score, "history": self.history})
            if taunt:
                self.log("[机器人说] " + taunt.replace("\n", " / "))
        self.emit({"type": "taunt", "text": taunt})

        # ④ 人出拳
        human = get_human()
        human_ts = _now()
        if human not in MOVES:
            self.emit({"type": "abort", "reason": f"人出拳非法: {human!r}"})
            return {"type": "abort", "reason": "no_human"}
        self.emit({"type": "human", "move": human, "human_ts": human_ts})

        # ⑤ 裁判 + 下发（同一瞬间）
        r = JUDGE(robot, human)
        name = _result_name(r)
        self.score[name] += 1
        rec = {"round": self.round_no, "human": human, "robot": robot,
               "result": name, "mode": "normal"}
        self.history.append(rec)
        targets = None
        if self.hand is not None:
            pose = POSE_OF.get(robot, robot)
            if callable(self.before_move):      # 真机动手前给界面留倒计时时间
                try:
                    self.before_move(self.round_no, robot, human)
                except Exception as _e:
                    self.log(f"[倒计时] 跳过：{_e}")
            try:
                targets = self.hand.pose_info(pose)["targets"]
            except Exception as e:
                self.log(f"[姿势] 找不到 {pose}：{e}")
                targets = None
            try:
                if self.dry_run:
                    self.log(f"[DRY-RUN] 将要下发 7 个值（{pose}）：{targets}")
                else:
                    self.hand.play(pose, strength=1.0, secs=0.6)
                    time.sleep(0.8)                  # 停一下，让人看清这一拳
                    # 2026-11-21 用户要求：每次出拳完都要恢复到展开
                    self.hand.play("open", strength=1.0, secs=0.7)
                    self.log("[回位] 已回到 open（展开）")
            except Exception as e:
                self.log(f"[下发] 失败：{e}")
        reveal_ts = _now()

        # 时间戳顺序自证
        assert dmeta["decide_ts"] <= lock_ts <= human_ts <= reveal_ts, "时间戳顺序被破坏！"
        ev = {"type": "reveal", "round": self.round_no, "robot": robot, "human": human,
              "result": name, "score": dict(self.score), "taunt": taunt,
              "targets": targets,
              "decide_ts": dmeta["decide_ts"], "lock_ts": lock_ts,
              "human_ts": human_ts, "reveal_ts": reveal_ts,
              "channel": dmeta.get("channel"), "latency": dmeta.get("latency"),
              "order_ok": True}
        self.emit(ev)
        # 2026-11-21 M4 遗留项：把四时间戳写进 history 记录并落盘（原来只有回合/出拳/结果）
        if self.history:
            self.history[-1].update({
                "bout": self.bout, "strategy": self.strategy,
                "persona": getattr(self.persona, "persona_id", None),
                "decide_ts": ev["decide_ts"], "lock_ts": ev["lock_ts"],
                "human_ts": ev["human_ts"], "reveal_ts": ev["reveal_ts"],
                "order_ok": ev["order_ok"], "channel": ev["channel"],
                "degraded": bool(dmeta.get("degraded")), "latency": ev["latency"],
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self.save_history()
        self.log(f"[揭晓] 人={human} 机={robot} → "
                 f"{'机器人赢' if r == 1 else '人赢' if r == -1 else '平'}")
        return ev

    # ---------- 一局 ----------
    def start_bout(self):
        # ★ 新的一局重新给 LLM 机会：上一局因断网被关掉之后，这里必须复位，
        #   否则"网络恢复也不回头"——实测过：整个 session 会永远停在规则策略
        #   （2026-11-21 总指挥点名要验的就是这一条）。
        self.llm_off_for_bout = False
        self.llm_fail_streak = 0
        intro = ""
        if self.persona is not None:
            intro = self.persona.talk("intro", {"score": self.score, "history": self.history})
            if intro:
                self.log("[机器人] " + intro.replace("\n", " / "))
        self.emit({"type": "intro", "text": intro})
        return intro

    def bout_done(self):
        return len(self.history) % self.rounds_per_bout == 0 and self.history

    def finish_bout(self):
        rounds = self.history[-self.rounds_per_bout:]
        w = sum(1 for r in rounds if r["result"] == "robot")
        l = sum(1 for r in rounds if r["result"] == "human")
        d = sum(1 for r in rounds if r["result"] == "draw")
        winner = "robot" if w > l else "human" if l > w else "draw"
        review = ""
        if self.persona is not None:                    # 赛后复盘（轻量对话，Q24）
            try:
                review = self.persona.review(rounds, self.score)
            except Exception as e:
                self.log(f"[复盘] 失败：{e}")
            if review:
                self.log("[复盘] " + review.replace("\n", " / "))
        ev = {"type": "bout", "bout": self.bout, "robot_wins": w,
              "human_wins": l, "draws": d, "winner": winner, "review": review}
        self.save_history(ev)
        self.bout += 1
        # 一局结束＝熔断复位（下一局再给 LLM 一次机会；局内仍然是"连败 2 轮就切规则"）
        self.llm_off_for_bout = False
        self.llm_fail_streak = 0
        self.emit(ev)
        return ev

    def save_history(self, bout_ev=None):
        """写 rps_history.json。总指挥 2026-11-21/22 要求：
        ① 带 `writer` 字段——**固定身份串**（`rps-line`），不再写进程号
           〔2026-11-22 00:2x 更正：这个字段在本文件里，按"RPS 代码唯一写者＝RPS 线"
           归本线改；执行部只验收〕；
        ② 写前比对 mtime/size——文件被别人改过就先把旧的另存 `rps_history.bak-<时钟>.json`，
           再写新的（不加锁也能不丢数据）。
        """
        os.makedirs(SIM, exist_ok=True)
        doc = {"updated": _iso(),
               "writer": {"id": WRITER_ID, "at": _iso(), "script": "rps/flow.py"},
               "rounds": self.history, "score": self.score, "last_bout": bout_ev}
        try:
            if os.path.exists(HISTORY_FILE):
                st = os.stat(HISTORY_FILE)
                sig = (st.st_mtime_ns, st.st_size)
                if getattr(self, "_hist_sig", None) not in (None, sig):
                    bak = HISTORY_FILE.replace(
                        ".json", f".bak-{time.strftime('%Y%m%d-%H%M%S')}.json")
                    try:
                        os.replace(HISTORY_FILE, bak)
                        self.log(f"[落盘] 检测到文件被他人改动，已另存 {os.path.basename(bak)}")
                    except Exception as e:
                        self.log(f"[落盘] 备份失败：{e}")
                self._hist_sig = sig
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
            st2 = os.stat(HISTORY_FILE)
            self._hist_sig = (st2.st_mtime_ns, st2.st_size)
        except Exception as e:
            self.log(f"[落盘] 失败：{e}")

    def build_chain(self):
        """装了 langchain-core 就用 LCEL 串起来；否则返回 None（走 run_round 纯函数）。"""
        try:
            from langchain_core.runnables import RunnableLambda
        except Exception:
            return None
        steps = [
            RunnableLambda(lambda s: {**s, "move": self.decide(s["history"], s["mine"])[0]}),
            RunnableLambda(lambda s: {**s, "lock": write_lock(s["round"], s["move"], self.strategy)}),
            RunnableLambda(lambda s: {**s, "taunt": (self.persona.talk("taunt", s) if self.persona else "")}),
        ]
        chain = steps[0]
        for st in steps[1:]:
            chain = chain | st
        return chain


def run_round(game, get_human):
    """纯函数版（无 LangChain）。与 Game.play_round 等价，方便单测。"""
    return game.play_round(get_human)


# ─────────────────── 离线评估：策略 × 人类模拟器 ───────────────────

def evaluate(strategy="hedge", kinds=None, rounds=200, seeds=(1, 2, 3), llm_rounds=None):
    """离线批量对打。llm_rounds 给定时，对 LLM 策略只跑那么少（省时间/省钱）。"""
    import statistics
    kinds = kinds or H.ALL_KINDS
    out = {}
    for kind in kinds:
        vals = []
        for s in seeds:
            if strategy == "llm":
                from .llm import LLMDecider
                backend = _LLMBackend(LLMDecider(), fallback=B.make_backend("hedge", seed=s))
            else:
                backend = B.make_backend(strategy, seed=s)
            r = H.match(backend, H.make_human(kind, seed=s),
                        rounds=(llm_rounds or rounds), seed=s)
            if r["win_rate"] is not None:
                vals.append(r["win_rate"])
        out[kind] = round(statistics.mean(vals), 4) if vals else None
    return out


class _LLMBackend(B.Backend):
    """把 LLM 包装成后端，供离线评测；失败就用 fallback（并计数）。"""

    name = "LLM"

    def __init__(self, decider, fallback):
        self.d = decider
        self.fb = fallback
        self.fallbacks = 0

    def decide(self, history, my_moves=None):
        try:
            move, _info = self.d.decide(history or [], my_moves or [])
            return move
        except Exception:
            self.fallbacks += 1
            return self.fb.decide(history or [], my_moves or [])

    def observe(self, human, mine, result):
        self.fb.observe(human, mine, result)

    def clone(self):
        return _LLMBackend(self.d, self.fb.clone())
