"""Aero Hand 一键启动台（启动台 App）—— 一个窗口管理所有组件

为什么做它（2026-11-21 用户要求"让所有东西一键启动"）：
  原来要记 4~5 个 bat、还要按顺序开（先 API 再识别），关的时候容易漏。
  这个窗口把"谁在跑、能不能点、出没出错"集中显示，一键全开 / 全关 / 急停。

它管的三样东西：
  ① **API 服务**（`.venv`，`scripts\\26_api.py`）—— 手这边，别的程序靠它控制
  ② **手势识别**（`.venv312`，`scripts\\27_vision_gesture.py`）—— 摄像头这边
  ③ **温度监控**（`.venv`，`scripts\\14_monitor.py`）—— 只读，不开也行

按钮：全部启动 / 全部停止 / 急停(卸力) / 打开 GUI / 打开浏览器测试页 / 清日志

启动：双击项目根 `启动台.bat`（或 py scripts\\00_launcher.py）
⚠️ 手只有一个串口：API 与「手动控制」GUI 不能同时开（本启动台会提示）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
PY_MAIN = ROOT / ".venv" / "Scripts" / "python.exe"          # 3.13：串口/API/监控
PY_VIS = ROOT / ".venv312" / "Scripts" / "python.exe"        # 3.12：mediapipe
MODEL_FILE = ROOT / "models" / "hand_landmarker.task"   # 视觉模型（不在 requirements 里，必须手工拷）
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)
API = "http://127.0.0.1:8765"

COMPONENTS = {
    "api":   {"label": "API 服务（手）", "py": PY_MAIN, "script": "scripts\\26_api.py"},
    "vision": {"label": "手势识别（摄像头）", "py": PY_VIS, "script": "scripts\\27_vision_gesture.py"},
    "monitor": {"label": "温度监控", "py": PY_MAIN, "script": "scripts\\14_monitor.py"},
}


def token() -> str:
    p = SIM / ".api_token"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.procs: dict[str, subprocess.Popen] = {}
        root.title("Aero Hand 启动台")
        root.geometry("720x460")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        title = ttk.Label(root, text="Aero Hand 启动台", font=("Microsoft YaHei UI", 15, "bold"))
        title.pack(pady=(10, 2))
        ttk.Label(root, text="一键启动/停止所有组件；手只有一个串口，API 与「手动控制」不能同时开",
                  foreground="#666").pack()

        bar = ttk.Frame(root, padding=8)
        bar.pack(fill="x")
        for text, cmd in (("▶ 全部启动", self.start_all), ("■ 全部停止", self.stop_all),
                          ("⏻ 全关（回open+卸力+杀外部进程）", self.stop_everything),
                          ("🛑 急停(卸力)", self.estop), ("🖐 打开手动控制", self.open_gui),
                          ("🌐 浏览器测试页", self.open_browser), ("🧹 清日志", self.clear_logs)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)

        # 组件行
        body = ttk.Frame(root, padding=(10, 4))
        body.pack(fill="both", expand=True)
        self.rows = {}
        for key, cfg in COMPONENTS.items():
            f = ttk.Frame(body)
            f.pack(fill="x", pady=3)
            ttk.Label(f, text=cfg["label"], width=20).pack(side="left")
            st = ttk.Label(f, text="● 未启动", foreground="#999", width=14)
            st.pack(side="left")
            ttk.Button(f, text="启动", width=6, command=lambda k=key: self.start(k)).pack(side="left", padx=2)
            ttk.Button(f, text="停止", width=6, command=lambda k=key: self.stop(k)).pack(side="left", padx=2)
            ttk.Button(f, text="看日志", width=7, command=lambda k=key: self.show_log(k)).pack(side="left", padx=2)
            self.rows[key] = st

        # 状态区
        ttk.Label(root, text="手的实时状态（来自 API）", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12, pady=(6, 0))
        self.status = tk.Text(root, height=6, font=("Consolas", 9), bg="#f7f7f7")
        self.status.pack(fill="both", expand=True, padx=12, pady=(2, 10))

        self.tick()

    # ---------- 组件控制 ----------
    def _vision_ready(self) -> bool:
        """视觉需要：.venv312 + 模型文件。缺模型时给中文提示，不当异常抛。"""
        if not Path(PY_VIS).exists():
            messagebox.showerror("缺环境", "找不到 .venv312（视觉专用环境）\n先双击「安装依赖.bat」")
            return False
        if not MODEL_FILE.exists():
            messagebox.showwarning(
                "缺模型文件",
                "缺模型文件，复制 models 目录后再启动。\n\n"
                f"需要：{MODEL_FILE}\n（约 7.46 MB，不在 requirements 里，必须手工拷）")
            self.log_status("视觉未启动：缺 models\\hand_landmarker.task")
            return False
        return True

    def start(self, key: str) -> None:
        if key in self.procs and self.procs[key].poll() is None:
            return
        if key == "vision" and not self._vision_ready():
            return
        cfg = COMPONENTS[key]
        py = cfg["py"]
        script = ROOT / cfg["script"]
        if not Path(py).exists():
            messagebox.showerror("缺环境", f"找不到 {py}\n（.venv312 是视觉专用环境）")
            return
        log = (LOGDIR / f"{key}.log").open("a", encoding="utf-8")
        try:
            p = subprocess.Popen([str(py), str(script)], cwd=str(ROOT),
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NEW_CONSOLE if key == "vision" else 0)
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return
        self.procs[key] = p
        self.log_status(f"已启动 {cfg['label']}（PID {p.pid}）")

    def stop(self, key: str) -> None:
        p = self.procs.get(key)
        if not p or p.poll() is not None:
            self.log_status(f"{COMPONENTS[key]['label']} 本来就没在跑")
            return
        try:
            p.terminate()
            time.sleep(0.3)
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
        self.log_status(f"已停止 {COMPONENTS[key]['label']}")

    def start_all(self) -> None:
        # 顺序：先 API（手），再监控，最后视觉
        self.start("api")
        time.sleep(2.0)
        self.start("monitor")
        time.sleep(0.5)
        if self._vision_ready():
            self.start("vision")
        else:
            self.log_status("已跳过视觉（缺模型文件或缺 .venv312），API 与监控已启动")

    def stop_everything(self) -> None:
        """一键全关：调 scripts\\stop_all.py —— 回 open → 卸力 → 杀本项目所有进程（含隐藏窗口的）。

        为什么不用「全部停止」：那个只停本窗口起的子进程；若有用别的窗口/bat 起的残留
        （例如改动前遗留的旧视觉实例），它会漏掉，手就可能被残留进程继续驱动。
        """
        script = ROOT / "scripts" / "stop_all.py"
        if not script.exists():
            messagebox.showerror("缺脚本", f"找不到 {script}")
            return
        try:
            # 新开一个控制台，让用户看到"关了哪些、手有没有松"
            subprocess.Popen([str(PY_MAIN), str(script)],
                             cwd=str(ROOT),
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            self.log_status("已启动全关（回 open → 卸力 → 杀进程），看新弹出的窗口")
        except Exception as e:
            messagebox.showerror("全关失败", str(e))

    def stop_all(self) -> None:
        for k in ("vision", "monitor", "api"):
            self.stop(k)

    # ---------- 快捷操作 ----------
    def estop(self) -> None:
        t = token()
        try:
            req = urllib.request.Request(API + "/estop", data=b"{}",
                                         headers={"Content-Type": "application/json", "X-Token": t})
            urllib.request.urlopen(req, timeout=5).read()
            self.log_status("🛑 已发送急停（卸力）")
        except Exception as e:
            # API 没开也直接写标记，其它脚本会响应
            try:
                (SIM / "ESTOP").write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 启动台急停\n", encoding="utf-8")
                self.log_status(f"API 未响应（{e}），已写 ESTOP 标记")
            except Exception as e2:
                self.log_status(f"急停失败：{e2}")

    def open_gui(self) -> None:
        api_up = self.api_alive()
        if api_up:
            if not messagebox.askyesno("串口冲突", "API 正在占用串口。\n要不要先停止 API 再打开手动控制？"):
                return
            self.stop("api")
            time.sleep(1.5)
        subprocess.Popen([str(PY_MAIN), str(ROOT / "scripts" / "24_gui_manual.py")], cwd=str(ROOT))
        self.log_status("已打开手动控制窗口")

    def open_browser(self) -> None:
        import webbrowser
        webbrowser.open(API + "/")
        self.log_status("已打开浏览器测试页（需要 API 在跑）")

    def clear_logs(self) -> None:
        n = 0
        for f in LOGDIR.glob("*.log"):
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
        self.log_status(f"已清理 {n} 个日志")

    def show_log(self, key: str) -> None:
        f = LOGDIR / f"{key}.log"
        if not f.exists():
            self.log_status(f"{key} 还没有日志")
            return
        tail = f.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        win = tk.Toplevel(self.root)
        win.title(f"{key}.log（最后 40 行）")
        win.geometry("760x420")
        txt = tk.Text(win, font=("Consolas", 9))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", "\n".join(tail))
        txt.see("end")

    # ---------- 状态刷新 ----------
    def api_alive(self) -> bool:
        try:
            urllib.request.urlopen(API + "/health", timeout=2).read()
            return True
        except Exception:
            return False

    def log_status(self, msg: str) -> None:
        self.status.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.status.see("end")

    def tick(self) -> None:
        for key, st in self.rows.items():
            p = self.procs.get(key)
            if p is None:
                st.config(text="● 未启动", foreground="#999")
            elif p.poll() is None:
                st.config(text=f"● 运行中 {p.pid}", foreground="#1a7f37")
            else:
                st.config(text=f"● 已退出 {p.returncode}", foreground="#c62828")
        # 2026-11-21 事故：视觉默认自动执行 → 连续驱动 → 电流 −562 触发 estop → API 进程被杀。
        # 界面必须提示"API 掉线"，否则用户会以为一切正常。
        api_alive = self.api_alive()
        was = getattr(self, "_api_was_alive", None)
        self._api_was_alive = api_alive
        if was is True and api_alive is False:
            self.log_status("⚠ API 已掉线（可能是急停触发了它）——建议同时停止视觉，避免它继续发请求")
        # 手的状态
        try:
            req = urllib.request.Request(API + "/status", headers={"X-Token": token()})
            d = json.loads(urllib.request.urlopen(req, timeout=2).read())
            pos = d.get("pos") or []
            temp = d.get("temp") or []
            cur = d.get("curr") or []
            line = (f"位置 {pos}\n电流 {cur}\n温度 {temp}  最高 {max(temp) if temp else '-'}°C  "
                    f"速度 {d.get('secs')}s")
            self.status.delete("1.0", "end")
            self.status.insert("1.0", line + "\n" + "—" * 40 + "\n" + self.status.get("1.0", "end").strip())
        except Exception:
            pass
        self.root.after(1000, self.tick)

    def on_close(self) -> None:
        if any(p and p.poll() is None for p in self.procs.values()):
            if not messagebox.askyesno("退出", "还有组件在跑，退出会把它们都停掉。确定？"):
                return
        self.stop_all()
        self.root.destroy()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = tk.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
