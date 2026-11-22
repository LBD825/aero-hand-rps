"""Aero Hand 本地 HTTP API v2 —— 把 GUI 的全部功能都暴露成接口

**为什么**：GUI 是给人点的，API 是给程序（语音智能体、网页、另一台电脑）调的。
本文件把 GUI 里的每个按钮/控件都对应成一个接口，一对一，不多不少。

启动：
    py scripts\\26_api.py                 # 只允许本机（默认，最安全）
    py scripts\\26_api.py --host 0.0.0.0  # 允许局域网（无鉴权，别开公网）
    py scripts\\26_api.py --port 8765

========================= 接口一览 =========================
【只读】
  GET  /                  浏览器测试页（按钮 + 实时状态）
  GET  /docs              所有接口的文字说明（纯文本）
  GET  /status            位置 / 电流 / 温度 / 当前速度
  GET  /poses             14 个姿势 + 序列 + limits
  GET  /limits            安全区间（7 个通道 lo/hi）
  GET  /user_poses        我自己在 GUI 里记录的姿势（poses_user.json）

【动作】
  POST /pose      {"name":"rock","secs":1.5}         ← GUI 双击姿势
  POST /open                                          ← GUI「回张开」
  POST /targets   {"targets":[7个数],"secs":0}        ← GUI 滑块的「应用」（secs>0 则平滑）
  POST /move      {"targets":[7个数],"secs":1.5}      ← 平滑移动（等价 /targets + secs）
  POST /sequence  {"name":"demo_gestures","secs":1.5} ← GUI「播放」（异步）
  POST /stop                                          ← GUI「停止」
  POST /release                                       ← GUI「急停(卸力)」
  POST /estop                                         ← 急停（卸力 + 写标记，会杀掉所有脚本）
  POST /speed     {"factor":1.5} 或 {"secs":1.0}      ← GUI「动作速度」下拉框

【编辑姿势（对应 GUI 的「保存覆盖 / 记录本姿势」）】
  POST /pose/targets {"name":"rock","targets":[...]}  ← 覆盖 poses.json 里某个姿势
  POST /save_pose    {"name":"我的姿势"}               ← 把"当前滑块值"存进 poses_user.json
  POST /user_pose    {"name":"我的姿势"}               ← 载入 poses_user 里的姿势并执行
  POST /delete_user  {"name":"我的姿势"}               ← 删除 poses_user 里的记录

【系统】
  GET  /health            心跳（不读串口，最省）

调用示例（PowerShell）：
    Invoke-RestMethod http://127.0.0.1:8765/status
    Invoke-RestMethod http://127.0.0.1:8765/pose -Method Post -ContentType application/json -Body '{"name":"rock"}'
    Invoke-RestMethod http://127.0.0.1:8765/speed -Method Post -ContentType application/json -Body '{"factor":2}'
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim2real"
POSES_FILE = SIM / "poses.json"
USER_FILE = SIM / "poses_user.json"
CTRL_POS, GET_POS, GET_CURR, GET_TEMP = 0x11, 0x22, 0x24, 0x25
FACTOR_TO_SECS = {0.0: 0.0, 0.2: 4.0, 0.5: 3.0, 1.0: 1.5, 1.5: 1.0, 2.0: 0.75}
TOKEN_FILE = SIM / ".api_token"

# ── 串口层已收口到 agent\（2026-11-21 P0）────────────────────────────
# 本文件原来自带一份完整 serial 实现（find_port / 16 字节帧 / 读回解析），
# 与 24/09/11/18… 各写一遍。现在**只有 agent\hal.py 碰串口**，这里只做翻译。
import sys as _sys                    # noqa: E402

if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))
from agent import open_hand, AgentError      # noqa: E402,F401


def load_json(p: Path, default=None):
    if not p.exists():
        return default if default is not None else {}
    return json.loads(p.read_text(encoding="utf-8-sig"))


def save_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def minjerk(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 10 * t**3 - 15 * t**4 + 6 * t**5


class Hand:
    """对 `agent.Hand` 的薄适配（保留旧的 send/pos/curr/temp/port/busy 形状）。

    为什么留这一层：HTTP 路由里到处是 `c.hand.pos()` 这类调用，改签名会牵动 600 行；
    适配层让"串口逻辑只剩 agent 一份"这个目标达成，又不用重写路由。
    构造参数 `real=False` ＝ dry-run（网络能跑、一个字节不发）。
    """

    def __init__(self, real: bool = True):
        self._h = open_hand(dry_run=not real)
        self.busy = threading.Lock()
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


class Ctx:
    """所有共享状态 + 动作实现（HTTP 处理器只做参数解析）。"""

    def __init__(self, hand):
        self.hand = hand
        self.reload()
        self.secs = 0.0                  # 当前"动作速度"：0＝瞬时（默认，用户 2026-11-21 要求）
        self.stop_evt = threading.Event()
        self.gen = 0                     # 代际：新动作取消旧动作
        self.last_targets = None         # 最近一次下发（供 /state）
        self.playing = ""                # 正在播放的序列名（供 /status）
        # 请求队列：所有动作按到达顺序排队执行（多客户端时先到先服务）
        self.jobs = queue.Queue()
        self.job_ids = 0
        threading.Thread(target=self._worker, daemon=True, name="jobq").start()

    def _worker(self):
        while True:
            fn, ev, box = self.jobs.get()
            try:
                box["result"] = fn()
            except Exception as e:
                box["error"] = str(e)
            finally:
                ev.set()
                self.jobs.task_done()

    def run(self, fn, timeout: float = 120.0):
        """把动作塞进队列并等它跑完（保证 FIFO，不会两个客户端同时动手）。"""
        ev, box = threading.Event(), {}
        self.job_ids += 1
        self.jobs.put((fn, ev, box))
        ev.wait(timeout)
        if "error" in box:
            raise RuntimeError(box["error"])
        return box.get("result")

    def out(self, targets):
        """统一出口：记录 + 下发。所有动作都走这里，/state 才有真实数据。"""
        t = [int(v) for v in targets]
        self.last_targets = t
        self.hand.send(t)
        return t

    def reload(self):
        lim = load_json(SIM / "limits.json")
        self.limits = {i: (int(lim[str(i)]["lo"]), int(lim[str(i)]["hi"])) for i in range(7)}
        doc = load_json(POSES_FILE)
        self.pose_docs = doc["poses"]
        self.poses = {k: v["targets"] for k, v in self.pose_docs.items()}
        self.seqs = doc.get("sequences", {})

    def clamp(self, targets):
        out = []
        for i, v in enumerate(targets):
            lo, hi = self.limits[i]
            out.append(max(lo, min(hi, int(v))))
        return out

    # ---------- 动作 ----------
    def move_to_pose(self, name: str, secs: float | None = None, gen: int | None = None) -> None:
        secs = self.secs if secs is None else secs
        tgt = self.poses[name]
        if secs <= 0:                       # 瞬时：直接下发
            self.out(self.clamp(tgt))
            return
        slow = (self.pose_docs.get(name) or {}).get("slow") or {}
        cur = self.hand.pos() or list(self.poses["open"])
        mid = [cur[i] if str(i) in slow else tgt[i] for i in range(7)]
        steps = max(1, int(secs * 25))
        for k in range(1, steps + 1):
            if self.stop_evt.is_set() or (gen is not None and gen != self.gen):
                return
            f = minjerk(k / steps)
            self.out([int(c + (m - c) * f) for c, m in zip(cur, mid)])
            time.sleep(1 / 25)
        if not slow:
            return
        factor = max(slow.values())
        cur2 = list(mid)
        steps2 = max(1, int(secs * factor * 25))
        for k in range(1, steps2 + 1):
            if self.stop_evt.is_set() or (gen is not None and gen != self.gen):
                return
            f = minjerk(k / steps2)
            self.out([int(c + (t - c) * f) for c, t in zip(cur2, tgt)])
            time.sleep(1 / 25)

    def move_to_targets(self, targets, secs: float, gen: int | None = None) -> None:
        tgt = self.clamp(targets)
        if secs <= 0:                       # 瞬时
            self.out(tgt)
            return
        cur = self.hand.pos() or tgt
        steps = max(1, int(max(secs, 0.05) * 25))
        for k in range(1, steps + 1):
            if self.stop_evt.is_set() or (gen is not None and gen != self.gen):
                return
            f = minjerk(k / steps)
            self.out([int(c + (t - c) * f) for c, t in zip(cur, tgt)])
            time.sleep(1 / 25)


CTX: Ctx | None = None
TOKEN = ""          # 启动时从 .api_token 读取（没有就生成）


def load_or_create_token() -> str:
    import secrets
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    t = secrets.token_urlsafe(18)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(t, encoding="utf-8")
    return t


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            print(f"[api] {self.address_string()} {fmt % a}", flush=True)

        # ---------- 工具 ----------
        def _send(self, body: bytes, ctype="application/json; charset=utf-8", code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), code=code)

        def _err(self, msg, code=400):
            self._json({"ok": False, "error": msg}, code)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        def _authed(self) -> bool:
            """除了 / 与 /health，其它都需要 token（请求头 X-Token 或 ?token=）。"""
            if TOKEN == "":
                return True                     # 未启用鉴权
            path = self.path.split("?")[0]
            if path in ("/", "/index.html", "/health"):
                return True
            got = self.headers.get("X-Token") or ""
            if not got and "token=" in self.path:
                got = self.path.split("token=", 1)[1].split("&")[0]
            return got == TOKEN

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        # ---------- GET ----------
        def do_GET(self):
            if not self._authed():
                return self._json({"ok": False, "error": "需要 token（请求头 X-Token 或 ?token=）"}, 401)
            self.route = self.path.split("?", 1)[0]     # 剥掉 ?token=... 再路由
            c = CTX
            if self.route in ("/", "/index.html"):
                page = """<!doctype html><meta charset=utf-8><title>Aero Hand API</title>
<style>body{font-family:system-ui;margin:24px;max-width:760px}button{padding:6px 12px;margin:3px}
#s{font-family:monospace;background:#f5f5f5;padding:8px;display:block;word-break:break-all}</style>
<h2>Aero Hand 本地 API</h2>
<code id=s>读取中…</code>
<p>速度：<select id=sp><option value=0.5>0.5×慢</option><option value=1 selected>1×标准</option>
<option value=1.5>1.5×快</option><option value=2>2×很快</option></select></p>
<div id=p></div><div id=u></div>
<script>
const show=o=>document.getElementById('s').textContent=JSON.stringify(o);
async function st(){show(await (await fetch('/status')).json());}
async function call(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
 show(await r.json()); st();}
async function poses(){const j=await (await fetch('/poses')).json();const d=document.getElementById('p');d.innerHTML='';
 for(const n of Object.keys(j.poses)){const b=document.createElement('button');b.textContent=n;b.onclick=()=>call('/pose',{name:n});d.appendChild(b);}
 const b=document.createElement('button');b.textContent='回张开';b.onclick=()=>call('/open');d.appendChild(b);
 const e=document.createElement('button');e.textContent='卸力';e.onclick=()=>call('/release');d.appendChild(e);
 const x=document.createElement('button');x.textContent='停止';x.onclick=()=>call('/stop');d.appendChild(x);}
async function users(){const j=await (await fetch('/user_poses')).json();const d=document.getElementById('u');d.innerHTML='<b>我的姿势：</b>';
 for(const n of Object.keys(j.poses||{})){const b=document.createElement('button');b.textContent=n;b.onclick=()=>call('/user_pose',{name:n});d.appendChild(b);}}
document.getElementById('sp').onchange=e=>call('/speed',{factor:parseFloat(e.target.value)});
poses();users();st();setInterval(st,1500);
</script>"""
                self._send(page.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.route == "/docs":
                self._send((__doc__ or "").encode("utf-8"), "text/plain; charset=utf-8")
                return
            if self.route == "/health":
                self._json({"ok": True, "port": c.hand.port, "poses": len(c.poses)})
                return
            if self.route == "/state":
                from estop import FLAG
                self._json({"ok": True, "targets": c.last_targets, "pos": c.hand.pos(),
                            "curr": c.hand.curr(), "temp": c.hand.temp(),
                            "secs": c.secs, "playing": c.playing,
                            "estop_flag": FLAG.exists()})
                return
            if self.route == "/estop_status":
                from estop import FLAG
                msg = ""
                if FLAG.exists():
                    try:
                        msg = FLAG.read_text(encoding="utf-8").strip()
                    except Exception:
                        msg = ""
                self._json({"ok": True, "estop_flag": FLAG.exists(), "message": msg})
                return
            if self.route == "/status":
                self._json({"ok": True, "port": c.hand.port, "pos": c.hand.pos(),
                            "curr": c.hand.curr(), "temp": c.hand.temp(), "secs": c.secs,
                            "targets": c.last_targets, "playing": c.playing})
                return
            if self.route == "/poses":
                self._json({"ok": True, "poses": c.poses, "detail": c.pose_docs,
                            "sequences": c.seqs})
                return
            if self.route == "/limits":
                self._json({"ok": True, "limits": {str(k): v for k, v in c.limits.items()}})
                return
            if self.route == "/user_poses":
                self._json({"ok": True, "poses": load_json(USER_FILE, {"poses": {}}).get("poses", {})})
                return
            self._err("unknown path", 404)

        # ---------- POST ----------
        def do_POST(self):
            if not self._authed():
                return self._json({"ok": False, "error": "需要 token（请求头 X-Token 或 ?token=）"}, 401)
            self.route = self.path.split("?", 1)[0]
            c = CTX
            d = self._body()
            if self.route == "/pose":
                name = d.get("name")
                if name not in c.poses:
                    return self._err(f"没有这个姿势：{name}")
                secs = float(d.get("secs") or c.secs)
                c.gen += 1
                gen = c.gen
                c.stop_evt.clear()
                c.run(lambda: c.move_to_pose(name, secs, gen))
                return self._json({"ok": True, "pose": name, "secs": secs,
                                   "targets": c.poses[name]})

            if self.route == "/open":
                c.gen += 1
                c.run(lambda: c.move_to_pose("open", c.secs, c.gen))
                return self._json({"ok": True, "pose": "open"})

            if self.route in ("/targets", "/move"):
                t = d.get("targets")
                if not isinstance(t, list) or len(t) != 7:
                    return self._err("targets 必须是 7 个数")
                secs = float(d.get("secs") or (c.secs if self.route == "/move" else 0) or 0)
                tt = c.clamp(t)
                c.stop_evt.clear()
                if secs <= 0:
                    with c.hand.busy:
                        c.out(tt)
                else:
                    c.gen += 1
                    c.run(lambda: c.move_to_targets(t, secs, c.gen))
                return self._json({"ok": True, "sent": tt, "secs": secs})

            if self.route == "/channel":
                ch = d.get("ch")
                if not isinstance(ch, int) or not (0 <= ch <= 6):
                    return self._err("ch 必须是 0~6")
                if "value" not in d:
                    return self._err("需要 value")
                cur = c.last_targets or c.hand.pos() or list(c.poses["open"])
                t = list(cur)
                lo, hi = c.limits[ch]
                t[ch] = max(lo, min(hi, int(d["value"])))
                secs = float(d.get("secs") or 0)
                if secs <= 0:
                    c.run(lambda: c.out(t))
                else:
                    c.gen += 1
                    c.run(lambda: c.move_to_targets(t, secs, c.gen))
                return self._json({"ok": True, "ch": ch, "targets": t, "secs": secs})

            if self.route == "/nudge":
                ch = d.get("ch")
                if not isinstance(ch, int) or not (0 <= ch <= 6):
                    return self._err("ch 必须是 0~6")
                delta = int(d.get("delta") or 0)
                cur = c.last_targets or c.hand.pos() or list(c.poses["open"])
                t = list(cur)
                lo, hi = c.limits[ch]
                t[ch] = max(lo, min(hi, t[ch] + delta))
                c.run(lambda: c.out(t))
                return self._json({"ok": True, "ch": ch, "delta": delta, "targets": t})

            if self.route == "/sequence":
                name = d.get("name")
                seq = c.seqs.get(name)
                if not seq:
                    return self._err(f"没有这个序列：{name}")
                secs = float(d.get("secs") or c.secs)
                c.gen += 1
                gen = c.gen
                c.stop_evt.clear()

                def run():
                    c.playing = name
                    if True:
                        for p in seq:
                            if c.stop_evt.is_set() or gen != c.gen:
                                break
                            if p in c.poses:
                                c.move_to_pose(p, secs, gen)
                                time.sleep(0.35)
                        pos = c.hand.pos()
                        if pos:
                            c.out(list(pos))
                    c.playing = ""

                threading.Thread(target=run, daemon=True).start()
                return self._json({"ok": True, "sequence": seq, "async": True, "secs": secs})

            if self.route == "/stop":
                c.stop_evt.set()
                c.gen += 1
                return self._json({"ok": True, "stopped": True})

            if self.route == "/release":
                pos = c.hand.pos()
                if pos:
                    c.out(list(pos))
                return self._json({"ok": True, "released": True, "pos": pos})

            if self.route == "/estop":
                from estop import trigger_estop
                pos = c.hand.pos()
                if pos:
                    c.out(list(pos))
                trigger_estop("HTTP /estop")
                return self._json({"ok": True, "estop": True, "pos": pos})

            if self.route == "/estop_status":
                from estop import FLAG
                msg = ""
                if FLAG.exists():
                    try:
                        msg = FLAG.read_text(encoding="utf-8").strip()
                    except Exception:
                        msg = ""
                return self._json({"ok": True, "estop_flag": FLAG.exists(), "message": msg})

            if self.route == "/clear_estop":
                from estop import clear_estop
                clear_estop()
                return self._json({"ok": True, "cleared": True})

            if self.route == "/speed":
                if "secs" in d:
                    c.secs = max(0.0, float(d["secs"]))
                elif "factor" in d:
                    f = float(d["factor"])
                    c.secs = 0.0 if f <= 0 else FACTOR_TO_SECS.get(round(f, 2), 1.5 / max(f, 0.1))
                else:
                    return self._err("需要 secs 或 factor")
                return self._json({"ok": True, "secs": c.secs, "instant": c.secs <= 0})

            # ---------- 编辑姿势 ----------
            if self.route == "/pose/targets":
                name, t = d.get("name"), d.get("targets")
                if name not in c.poses:
                    return self._err(f"没有这个姿势：{name}")
                if not isinstance(t, list) or len(t) != 7:
                    return self._err("targets 必须是 7 个数")
                doc = load_json(POSES_FILE)
                tt = c.clamp(t)
                doc["poses"][name]["targets"] = tt
                doc["poses"][name]["note"] = doc["poses"][name].get("note", "") + "｜API 覆盖"
                save_json(POSES_FILE, doc)
                c.reload()
                return self._json({"ok": True, "name": name, "targets": tt})

            if self.route == "/save_pose":
                name = (d.get("name") or "").strip()
                if not name:
                    return self._err("需要 name")
                t = d.get("targets") or c.hand.pos() or []
                if len(t) != 7:
                    return self._err("targets 必须是 7 个数（或省略＝当前实测位置）")
                doc = load_json(USER_FILE, {"poses": {}})
                doc.setdefault("poses", {})[name] = {
                    "targets": c.clamp(t), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "pos": c.hand.pos(), "curr": c.hand.curr(), "temp": c.hand.temp()}
                save_json(USER_FILE, doc)
                return self._json({"ok": True, "saved": name, "targets": doc["poses"][name]["targets"]})

            if self.route == "/user_pose":
                name = d.get("name")
                up = load_json(USER_FILE, {"poses": {}}).get("poses", {})
                if name not in up:
                    return self._err(f"没有这个记录：{name}")
                tgt = up[name]["targets"]
                c.gen += 1
                c.stop_evt.clear()
                c.run(lambda: c.move_to_targets(tgt, float(d.get("secs") or c.secs), c.gen))
                return self._json({"ok": True, "user_pose": name, "targets": tgt})

            if self.route == "/delete_user":
                name = d.get("name")
                doc = load_json(USER_FILE, {"poses": {}})
                if name not in doc.get("poses", {}):
                    return self._err(f"没有这个记录：{name}")
                doc["poses"].pop(name)
                save_json(USER_FILE, doc)
                return self._json({"ok": True, "deleted": name})

            self._err("unknown path", 404)

    return H


def main() -> None:
    import sys

    global CTX
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-auth", action="store_true", help="关闭鉴权（仅本机调试用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不碰串口（假手），用来测接口/写客户端")
    args = ap.parse_args()

    global TOKEN
    TOKEN = "" if args.no_auth else load_or_create_token()

    hand = Hand(real=not args.dry_run)
    from estop import install_estop
    install_estop(hand.send, hand.pos, hand.curr, hand.temp)
    CTX = Ctx(hand)

    srv = ThreadingHTTPServer((args.host, args.port), make_handler())
    print(f"✅ Aero Hand API v2：http://{args.host}:{args.port}/", flush=True)
    print(f"   串口 {hand.port}{'（DRY-RUN，不碰硬件）' if hand.dry_run else ''}；"
          f"{len(CTX.poses)} 个姿势 / {len(CTX.seqs)} 个序列 / 速度 {CTX.secs}s", flush=True)
    print(f"   接口说明：http://{args.host}:{args.port}/docs", flush=True)
    if TOKEN:
        print(f"   🔑 token（请求头 X-Token）：{TOKEN}", flush=True)
        print(f"      token 存在 {TOKEN_FILE}", flush=True)
    else:
        print("   ⚠ 鉴权已关闭（--no-auth），只建议本机临时调试", flush=True)
    print(f"   动作速度默认 {CTX.secs}s（0＝瞬时）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退出（先卸力）", flush=True)
    finally:
        pos = hand.pos()
        if pos:
            hand.send(list(pos))
        srv.server_close()


if __name__ == "__main__":
    main()
