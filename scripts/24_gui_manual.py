"""手动控制台（GUI v3）—— 滑块+输入框精细控制 / 全部姿势 / 序列播放 / 命名保存

v3 相比 v2 的优化（2026-11-21）：
  ① **精确到位**：每通道除了滑块，还有**输入框**（可直接敲数值）+ `−1k / +1k` 微调按钮；
  ② **全部姿势可选**：右侧列出 `poses.json` 里**全部 14 个姿势**，双击即载入并下发；
  ③ **序列播放**：直接跑 `demo_gestures` / `opposition`（后台线程，界面不卡，可随时停）；
  ④ **Esc = 急停卸力**（键盘随时能停）；
  ⑤ **状态栏**：显示最热通道、峰值电流、当前在做什么；
  ⑥ 温度着色（<45 绿 / ≥45 橙 / ≥50 红并自动卸力）。

安全：
  · 滑块与输入框都被 `limits.json` 夹紧
  · 温度 ≥50°C → 卸力 + 锁死下发
  · 关窗 / Esc / 急停按钮 → 卸力（目标=当前位置）
  · 接 `estop.py`（温度 / 电流 / 标记文件三路保护，独立线程）

启动：双击项目根 `手动控制.bat`
"""
from __future__ import annotations

import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
USER_POSES = SIM / "poses_user.json"
CTRL_POS, GET_POS, GET_CURR, GET_TEMP = 0x11, 0x22, 0x24, 0x25
NAMES = ["拇指外展", "拇指屈", "拇指腱", "食指", "中指", "无名指", "小指"]
FINE = 1000

# ── 串口层已收口到 agent\（2026-11-21 P0）──
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from agent import open_hand, AgentError      # noqa: E402,F401


def load_json(p: Path, default=None):
    if not p.exists():
        return default if default is not None else {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def minjerk(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 10 * t**3 - 15 * t**4 + 6 * t**5


class Hand:
    """对 `agent.Hand` 的薄适配 —— 串口/帧格式已收口到 agent 包（2026-11-21 P0）。

    原来这里有一份完整 serial 实现（与 26_api / 09 / 11 / 18… 各写一份）。
    现在本文件只保留界面逻辑；`real=False` ＝ dry-run。
    """

    def __init__(self, real: bool = True):
        self._h = open_hand(dry_run=not real)
        self.dry_run = self._h.dry_run

    @property
    def port(self):
        return self._h.port

    def pos(self):
        return self._h.raw_pos()

    def curr(self):
        return self._h.raw_curr()

    def temp(self):
        return self._h.raw_temp()

    def send(self, targets):
        return self._h.raw_send(targets)

    def close(self):
        try:
            self._h.close()
        except Exception:
            pass


class App:
    def __init__(self, root, hand, limits, poses, pose_docs, sequences):
        self.root, self.hand, self.limits = root, hand, limits
        self.poses = poses          # name -> targets（供滑块使用）
        self.pose_docs = pose_docs  # name -> 完整字典（含 slow 等元数据）
        self.seqs = sequences
        self.stop = False
        self.playing = threading.Event()
        self.motion_gen = 0
        self.vars, self.entries = [], []
        self.lbl_pos, self.lbl_cur, self.lbl_tmp = [], [], []
        self.live = tk.BooleanVar(value=False)

        root.title("Aero Hand 手动控制台 v3")
        root.geometry("1100x620")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Escape>", lambda e: self.release())

        top = ttk.Frame(root, padding=6)
        top.pack(fill="x")
        for text, cmd in (("读当前", self.sync_from_hand), ("应用", self.apply),
                          ("回张开", self.go_open), ("急停(卸力) Esc", self.release),
                          ("记录本姿势", self.record_named)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=3)
        ttk.Checkbutton(top, text="实时跟随", variable=self.live).pack(side="left", padx=10)
        ttk.Label(top, text="动作速度：").pack(side="left", padx=(14, 2))
        self.speed = tk.StringVar(value="瞬时")
        cb = ttk.Combobox(top, textvariable=self.speed, width=12, state="readonly",
                          values=["瞬时", "0.5×（慢）", "1×（标准）", "1.5×（快）", "2×（很快）"])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.set_status(f"动作速度：{self.speed.get()}"))

        body = ttk.Frame(root, padding=(6, 0, 6, 0))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, width=300)
        right.pack(side="right", fill="y", padx=(8, 0))

        for i in range(7):
            lo, hi = self.limits[i]
            ttk.Label(left, text=f"ch{i} {NAMES[i]}", width=12).grid(row=i, column=0, sticky="w")
            v = tk.IntVar(value=int(self.poses["open"][i]))
            s = ttk.Scale(left, from_=lo, to=hi, orient="horizontal", length=330,
                          command=lambda val, idx=i: self.on_slide(idx, val))
            s.set(v.get())
            s.grid(row=i, column=1, padx=3, pady=1)
            e = ttk.Entry(left, textvariable=v, width=7)
            e.grid(row=i, column=2, padx=2)
            e.bind("<Return>", lambda ev, idx=i: self.commit_entry(idx))
            ttk.Button(left, text="-1k", width=4,
                       command=lambda idx=i: self.nudge(idx, -FINE)).grid(row=i, column=3)
            ttk.Button(left, text="+1k", width=4,
                       command=lambda idx=i: self.nudge(idx, +FINE)).grid(row=i, column=4)
            lp = ttk.Label(left, text="pos --", width=10)
            lp.grid(row=i, column=5, padx=(6, 0))
            lc = ttk.Label(left, text="cur --", width=9)
            lc.grid(row=i, column=6)
            lt = ttk.Label(left, text="--", width=7)
            lt.grid(row=i, column=7)
            self.vars.append(v)
            self.entries.append(e)
            self.lbl_pos.append(lp)
            self.lbl_cur.append(lc)
            self.lbl_tmp.append(lt)

        ttk.Label(right, text="姿势（双击＝载入并下发）").pack(anchor="w")
        self.pose_list = tk.Listbox(right, height=10)
        self.pose_list.pack(fill="x")
        self.pose_list.bind("<Double-Button-1>", lambda e: self.load_selected_pose())
        self._pose_names = list(self.poses.keys())
        for n in self._pose_names:
            self.pose_list.insert(tk.END, n)

        rowp = ttk.Frame(right)
        rowp.pack(fill="x", pady=2)
        ttk.Button(rowp, text="载入到滑块", command=self.load_selected_pose).pack(
            side="left", expand=True, fill="x")
        ttk.Button(rowp, text="保存覆盖", command=self.save_selected_pose).pack(
            side="left", expand=True, fill="x")

        ttk.Label(right, text="序列").pack(anchor="w", pady=(6, 0))
        self.seq_list = tk.Listbox(right, height=3)
        self.seq_list.pack(fill="x")
        self._seq_names = list(self.seqs.keys())
        for n in self._seq_names:
            self.seq_list.insert(tk.END, n)
        row = ttk.Frame(right)
        row.pack(fill="x", pady=2)
        ttk.Button(row, text="播放", command=self.play_selected_seq).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="停止", command=self.stop_play).pack(side="left", expand=True, fill="x")

        ttk.Label(right, text="我的姿势").pack(anchor="w", pady=(6, 0))
        self.user_list = tk.Listbox(right, height=5)
        self.user_list.pack(fill="x")
        row2 = ttk.Frame(right)
        row2.pack(fill="x", pady=2)
        ttk.Button(row2, text="载入", command=self.load_selected_user).pack(side="left", expand=True, fill="x")
        ttk.Button(row2, text="删除", command=self.delete_selected_user).pack(side="left", expand=True, fill="x")
        ttk.Button(row2, text="刷新", command=self.refresh_user).pack(side="left", expand=True, fill="x")

        self.status = ttk.Label(root, text="就绪", relief="sunken", anchor="w", padding=4)
        self.status.pack(fill="x", side="bottom")

        self.refresh_user()
        self.tick()

    # ---------- 通道 ----------
    def on_slide(self, idx, val):
        self.vars[idx].set(int(float(val)))
        if self.live.get() and not self.stop:
            self.apply()

    def commit_entry(self, idx):
        try:
            v = int(self.entries[idx].get())
        except Exception:
            return
        lo, hi = self.limits[idx]
        self.vars[idx].set(max(lo, min(hi, v)))
        self.apply()

    def nudge(self, idx, delta):
        lo, hi = self.limits[idx]
        self.vars[idx].set(max(lo, min(hi, self.vars[idx].get() + delta)))
        self.apply()

    def current_targets(self):
        out = []
        for i, v in enumerate(self.vars):
            lo, hi = self.limits[i]
            out.append(max(lo, min(hi, int(v.get()))))
        return out

    def move_secs(self) -> float:
        """把"动作速度"下拉框换算成秒数；0 表示瞬时到位。"""
        v = self.speed.get()
        table = {"瞬时": 0.0, "0.5×（慢）": 3.0, "1×（标准）": 1.5,
                 "1.5×（快）": 1.0, "2×（很快）": 0.75}
        return table.get(v, 1.5)

    # ---------- 动作 ----------
    def apply(self):
        if self.stop:
            return
        t = self.current_targets()
        try:
            self.hand.send(t)
            self.set_status(f"已下发 {t}")
        except Exception as e:
            self.set_status(f"下发失败：{e}")

    def sync_from_hand(self):
        p = self.hand.pos()
        if p:
            for i in range(7):
                lo, hi = self.limits[i]
                self.vars[i].set(max(lo, min(hi, p[i])))
            self.set_status("滑块已对齐实测位置")

    def go_open(self):
        if self.stop:
            return
        for i, v in enumerate(self.poses["open"]):
            self.vars[i].set(v)
        self.apply()

    def release(self):
        p = self.hand.pos()
        if p:
            for i in range(7):
                self.vars[i].set(p[i])
            try:
                self.hand.send(list(p))
            except Exception:
                pass
            self.set_status("已卸力（目标=当前位置）")

    # ---------- 姿势 / 序列 ----------
    def load_pose(self, name):
        for i, v in enumerate(self.poses[name]):
            self.vars[i].set(v)
        secs = self.move_secs()
        if secs <= 0:
            self.apply()                       # 瞬时
            return
        threading.Thread(target=self._smooth_to, args=(name, secs), daemon=True).start()

    def _smooth_to(self, name: str, secs: float) -> None:
        """在后台线程里平滑到位（走 move_to_pose，支持 slow 字段）。

        用"代际号"取消旧动作：快速连点多个姿势时，只有最后一个会跑完，
        避免多个线程同时往串口写导致动作一顿一顿。
        """
        self.motion_gen += 1
        gen = self.motion_gen
        self.playing.set()
        try:
            self.move_to_pose(name, secs, gen=gen)
        finally:
            if gen == self.motion_gen:
                self.playing.clear()

    def load_selected_pose(self):
        sel = self.pose_list.curselection()
        if sel:
            self.load_pose(self._pose_names[sel[0]])

    def save_selected_pose(self):
        """把滑块当前值**覆盖写进 poses.json 里选中的姿势**（编辑已有动作用）。"""
        sel = self.pose_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "先在姿势列表里选中要覆盖的那一个")
            return
        name = self._pose_names[sel[0]]
        if not messagebox.askyesno("覆盖确认", f"用当前滑块的值覆盖姿势「{name}」？"):
            return
        t = self.current_targets()
        doc = load_json(SIM / "poses.json")
        doc["poses"][name]["targets"] = t
        doc["poses"][name]["note"] = (doc["poses"][name].get("note", "") +
                                      "｜2026-11-21 GUI 编辑覆盖")
        (SIM / "poses.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        self.poses[name] = t
        print(f"[覆盖] {name} = {t}", flush=True)
        self.set_status(f"已覆盖姿势「{name}」：{t}")

    def play_selected_seq(self):
        sel = self.seq_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一个序列")
            return
        name = self._seq_names[sel[0]]
        seq = self.seqs[name]
        if self.playing.is_set():
            messagebox.showinfo("提示", "正在播放中，先点停止")
            return
        self.playing.set()

        def worker():
            try:
                self.root.after(0, lambda: self.set_status(f"播放：{' → '.join(seq)}"))
                for pose_name in seq:
                    if not self.playing.is_set() or self.stop:
                        break
                    self.move_to_pose(pose_name, self.move_secs() or 1.5)
                    time.sleep(0.35)                 # 到位后短暂停顿（连贯感靠这里）
                    t = self.hand.temp()
                    if t and max(t) >= 44:           # 只有热了才卸力降温，否则保持连续
                        p = self.hand.pos()
                        if p:
                            self.hand.send(list(p))
                        time.sleep(1.2)
                    self.root.after(0, lambda n=pose_name: self.set_status(f"✔ {n}"))
            finally:
                self.playing.clear()
                self.root.after(0, lambda: self.set_status("序列播放结束"))

        threading.Thread(target=worker, daemon=True).start()

    def move_to_pose(self, name: str, secs: float = 1.5, gen: int | None = None) -> None:
        """移动到某姿势；姿势里若有 slow={通道:倍数}，那些通道**先不动**，
        等其它通道到位后再单独用 倍数×secs 的时间慢慢到位（如 rock 的拇指）。"""
        tgt = self.poses[name]
        slow = (self.pose_docs.get(name) or {}).get("slow") or {}
        cur = self.hand.pos() or list(self.poses["open"])
        # 第一段：慢通道保持不动，其它通道正常速度
        mid = [cur[i] if str(i) in slow else tgt[i] for i in range(7)]
        steps = max(1, int(secs * 25))
        for k in range(1, steps + 1):
            if not self.playing.is_set() or self.stop:
                return
            if gen is not None and gen != self.motion_gen:
                return
            f = minjerk(k / steps)
            self.hand.send([int(c + (m - c) * f) for c, m in zip(cur, mid)])
            time.sleep(1 / 25)
        if not slow:
            return
        # 第二段：慢通道单独慢慢到位
        cur2 = list(mid)
        factor = max(slow.values()) if slow else 1.0
        steps2 = max(1, int(secs * factor * 25))
        for k in range(1, steps2 + 1):
            if not self.playing.is_set() or self.stop:
                return
            if gen is not None and gen != self.motion_gen:
                return
            f = minjerk(k / steps2)
            self.hand.send([int(c + (t - c) * f) for c, t in zip(cur2, tgt)])
            time.sleep(1 / 25)

    def stop_play(self):
        self.playing.clear()
        self.set_status("已请求停止序列")

    # ---------- 我的姿势 ----------
    def record_named(self):
        name = simpledialog.askstring("记录姿势", "给这个姿势起个名字：", parent=self.root)
        if not name:
            return
        key = name.strip()
        data = load_json(USER_POSES, {"poses": {}})
        data.setdefault("poses", {})[key] = {
            "targets": self.current_targets(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pos": self.hand.pos(), "curr": self.hand.curr(), "temp": self.hand.temp(),
        }
        USER_POSES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[记录] {key} = {data['poses'][key]['targets']}", flush=True)
        self.refresh_user()
        self.set_status(f"已保存「{key}」")

    def refresh_user(self):
        self.user_list.delete(0, tk.END)
        self._user_names = sorted(load_json(USER_POSES, {"poses": {}}).get("poses", {}).keys())
        for n in self._user_names:
            self.user_list.insert(tk.END, n)

    def load_selected_user(self):
        sel = self.user_list.curselection()
        if not sel:
            return
        name = self._user_names[sel[0]]
        data = load_json(USER_POSES, {"poses": {}})["poses"][name]
        for i, v in enumerate(data["targets"]):
            self.vars[i].set(v)
        self.apply()
        self.set_status(f"已载入「{name}」")

    def delete_selected_user(self):
        sel = self.user_list.curselection()
        if not sel:
            return
        name = self._user_names[sel[0]]
        if not messagebox.askyesno("删除", f"确定删除「{name}」？"):
            return
        data = load_json(USER_POSES, {"poses": {}})
        data["poses"].pop(name, None)
        USER_POSES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_user()
        self.set_status(f"已删除「{name}」")

    # ---------- 状态 ----------
    def set_status(self, text):
        self.status.config(text=text)

    def tick(self):
        if not self.stop:
            p, c, t = self.hand.pos(), self.hand.curr(), self.hand.temp()
            if p:
                for i in range(7):
                    self.lbl_pos[i].config(text=f"pos {p[i]}")
            if c:
                for i in range(7):
                    self.lbl_cur[i].config(text=f"cur {c[i]:+d}")
            if t:
                hottest = max(range(7), key=lambda i: t[i])
                for i in range(7):
                    color = "#1a7f37" if t[i] < 45 else ("#b26b00" if t[i] < 50 else "#c62828")
                    self.lbl_tmp[i].config(text=f"{t[i]}°C" + ("!" if t[i] >= 45 else ""), foreground=color)
                if max(t) >= 50:
                    self.release()
                    self.stop = True
                    self.set_status(f"温度 {max(t)}°C >=50：已卸力并停止下发，请等它凉")
                elif not self.playing.is_set():
                    pk = max((abs(x) for x in c), default=0)
                    self.status.config(text=f"就绪 | 最热 ch{hottest} {t[hottest]}°C | 峰值电流 {pk}")
        self.root.after(300, self.tick)

    def on_close(self):
        self.playing.clear()
        self.release()
        self.stop = True
        time.sleep(0.2)
        self.hand.close()
        self.root.destroy()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lim_raw = load_json(SIM / "limits.json")
    limits = {i: (int(lim_raw[str(i)]["lo"]), int(lim_raw[str(i)]["hi"])) for i in range(7)}
    poses_doc = load_json(SIM / "poses.json")
    pose_docs = poses_doc["poses"]
    poses = {k: v["targets"] for k, v in pose_docs.items()}
    seqs = poses_doc.get("sequences", {})

    hand = Hand(real="--dry-run" not in sys.argv)
    from estop import install_estop
    install_estop(hand.send, hand.pos, hand.curr, hand.temp)
    root = tk.Tk()
    App(root, hand, limits, poses, pose_docs, seqs)
    mode = "DRY-RUN（不碰串口）" if hand.dry_run else f"已连接 {hand.port}"
    print(f"{mode}；{len(poses)} 个姿势 / {len(seqs)} 个序列", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
