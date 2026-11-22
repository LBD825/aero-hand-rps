"""决策 LLM（N3 的唯一联网出口）—— 云端 Flash 首选 / 本地 8B 后备 / 规则最终兜底。

★ 设计纪律（犯了就是现场翻车）
  1. **只输出一个词**：解析失败就算失败，**不重试**（重试只会更慢）；
  2. **超时分两档**：`flash=3s`、`local=8s`（2026-11-22 总指挥批准）；
     到点抛 **`LLMTimeout`**，调用方立刻用 Hedge 出本轮。
     〔变更说明，保留历史〕本文档原写"硬超时 8 秒、抛 LLMTimeout"；实测当时
     默认是 **3 秒**、且超时被包成 `LLMError`（`LLMTimeout` 是死代码）。现已按代码
     改正：3 秒给 flash、8 秒留给 local，并把超时真正映射成 `LLMTimeout`。
  3. **不把本轮人手放进上下文**：`decide()` 的入参只有历史，物理上拿不到 human_move；
  4. 人设与决策**两条独立调用**：本文件只管"手"，嘴巴在 `persona.py`。

★ 通道顺序
  Flash（`https://api.deepseek.com/v1`，model `deepseek-flash`，需 key）
    ↓ 失败/超时/无 key
  本机 Ollama（`http://127.0.0.1:11434/v1`，model `deepseek-r1:8b_m`）
    ↓ 再失败
  raise → 调用方用规则后端（Hedge）

★ key 怎么给（**不写进仓库**）
  1. 环境变量 `DEEPSEEK_API_KEY`
  2. `sim2real\\.rps_llm.json` 里的 `{"key_file": "D:\\某处\\key.txt"}`
  3. 默认回退读 `D:\\大肥鱼\\deepseek-key.txt`（本机已有，仅供演示机）
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

MOVES = ("rock", "paper", "scissors")
COUNTERS = {"rock": "paper", "paper": "scissors", "scissors": "rock"}   # 克制 x 的拳
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}       # x 克制的拳


def _res_for(h, m):
    """人类视角的结果：+1 人赢 / 0 平 / -1 人输（h=人出的，m=机器出的）。"""
    if h == m:
        return 0
    return 1 if BEATS[h] == m else -1


def facts(history, my_moves):
    """把六路判据算成一个 dict（`hints()` 与 `hints_summary()` 共用，避免两处逻辑漂移）。"""
    history = list(history or [])
    my = list(my_moves or [])
    n = min(len(history), len(my))
    f = {"n": n}
    if n == 0:
        return f
    last_h, last_m = history[-1], my[-1]
    f.update({"last_h": last_h, "last_m": last_m})

    def rate(fn):
        if n < 2:
            return 0.0, 0
        hit = sum(1 for i in range(1, n) if fn(history[i], history[i - 1], my[i - 1]))
        return hit / (n - 1), hit

    f["counter_rate"] = rate(lambda h, hp, mp: h == COUNTERS[mp])[0]   # 反制我上一手
    f["repeat_rate"] = rate(lambda h, hp, mp: h == mp)[0]              # 模仿我上一手
    f["counter_self_rate"] = rate(lambda h, hp, mp: h == COUNTERS[hp])[0]

    def predict(k):
        table = {}
        for i in range(k, n):
            table.setdefault(tuple(history[i - k:i]), {})
            table[tuple(history[i - k:i])][history[i]] = \
                table[tuple(history[i - k:i])].get(history[i], 0) + 1
        row = table.get(tuple(history[-k:]))
        return max(row, key=row.get) if row else None

    f["markov1"] = predict(1)
    f["markov2"] = predict(2) if n >= 3 else None

    if n >= 2:
        table = {}
        for i in range(1, n):
            key = (_res_for(history[i - 1], my[i - 1]), history[i - 1])
            table.setdefault(key, {})
            table[key][history[i]] = table[key].get(history[i], 0) + 1
        row = table.get((_res_for(last_h, last_m), last_h))
        if row:
            f["outcome_next"] = max(row, key=row.get)
            f["outcome_n"] = sum(row.values())

    streak, cur = 1, last_h
    for i in range(n - 2, -1, -1):
        if history[i] == cur:
            streak += 1
        else:
            break
    f["streak"], f["streak_move"] = streak, cur
    return f


def hints_summary(history, my_moves):
    """一行版（给界面显示用）：反制/模仿/克己 命中率 + 分桶/转移的预测。"""
    f = facts(history, my_moves)
    if not f.get("n"):
        return "参考：首轮，按开局偏好"
    parts = [f"反制{f['counter_rate']:.2f}", f"模仿{f['repeat_rate']:.2f}",
             f"克己{f['counter_self_rate']:.2f}"]
    if f.get("outcome_next"):
        parts.append(f"分桶→{f['outcome_next']}")
    if f.get("markov1"):
        parts.append(f"一阶→{f['markov1']}")
    if f.get("markov2"):
        parts.append(f"二阶→{f['markov2']}")
    if f.get("streak", 1) >= 2:
        parts.append(f"连击{f['streak_move']}×{f['streak']}")
    return "参考：" + " ".join(parts)


def hints(history, my_moves):
    """**用代码先算好事实**，再交给模型判断——LLM 不擅长当场做两级取反。

    实测（2026-11-21）：只写规则 → 对 counter 型仍 32%；把候选摆到面前 → 100%；
    但漏了"结果×上一手"分桶 → outcome1 只有 58.9%（补上后 82.1%）。
    """
    f = facts(history, my_moves)
    if not f.get("n"):
        return ("【代码算好的参考】暂无历史（第一轮）。统计上多数人先出石头 → 建议出布，"
                "也可以随机。")
    rows = ["【代码算好的参考（可直接采用，也可以推翻）】"]
    last_h, last_m = f["last_h"], f["last_m"]
    if f["counter_rate"] >= 0.6:
        rows.append(f"- **反制型**命中率 {f['counter_rate']:.2f} ⇒ 按表 A（再往上算一层）"
                    f"应出 **{BEATS[last_m]}**")
    elif f["repeat_rate"] >= 0.6:
        rows.append(f"- **模仿型**命中率 {f['repeat_rate']:.2f} ⇒ 应出 **{COUNTERS[last_m]}**")
    elif f["counter_self_rate"] >= 0.6:
        rows.append(f"- **克自己型**命中率 {f['counter_self_rate']:.2f} ⇒ 应出 "
                    f"**{COUNTERS[COUNTERS[last_h]]}**")
    else:
        rows.append(f"- 三条类型判据都未达 0.6（反制 {f['counter_rate']:.2f} / "
                    f"模仿 {f['repeat_rate']:.2f} / 克己 {f['counter_self_rate']:.2f}）")
    if f.get("outcome_next"):
        rows.append(f"- **outcome 分桶**（上一轮结果={_res_for(last_h, last_m):+d} × "
                    f"他上一手={last_h}，样本 {f['outcome_n']}）：他下一步最常出 "
                    f"{f['outcome_next']} ⇒ 克制它应出 **{COUNTERS[f['outcome_next']]}**")
    if f.get("markov1"):
        rows.append(f"- 一阶转移：出过 {last_h} 之后最常出 {f['markov1']} ⇒ 应出 "
                    f"**{COUNTERS[f['markov1']]}**")
    if f.get("markov2"):
        rows.append(f"- 二阶转移：最近两手组合之后最常出 {f['markov2']} ⇒ 应出 "
                    f"**{COUNTERS[f['markov2']]}**")
    rows.append(f"- 他当前连击：{f['streak_move']} × {f['streak']}"
                f"{' ⇒ 连到 2 次以上，倾向他会换' if f['streak'] >= 2 else ''}")
    rows.append("- 多条互相矛盾时：选**样本最多**的那条；都不足就随机，别硬套。")
    return "\n".join(rows)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 项目根
SIM = os.path.join(ROOT, "sim2real")
CFG = os.path.join(SIM, ".rps_llm.json")

FLASH_BASE = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com/v1")
FLASH_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
LOCAL_BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434/v1")
LOCAL_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:8b_m")
DEFAULT_KEY_FILE = r"D:\大肥鱼\deepseek-key.txt"


class LLMError(Exception):
    pass


class LLMTimeout(LLMError):
    pass


def _default_strategy():
    """决策提示词里注入的策略库：优先精简版（省思考 token、更快），没有就用全文。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("策略库-精简.md", "策略库.md"):
        p = os.path.join(here, name)
        try:
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read()
        except Exception:
            continue
    return ""


def load_key():
    """按顺序取 key：环境变量 → 配置文件指向的文件 → 本机默认路径。取不到返回 None。"""
    k = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if k:
        return k
    try:
        if os.path.exists(CFG):
            with open(CFG, encoding="utf-8") as f:
                p = (json.load(f) or {}).get("key_file")
            if p and os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read().strip()
    except Exception:
        pass
    if os.path.exists(DEFAULT_KEY_FILE):
        try:
            with open(DEFAULT_KEY_FILE, encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return None
    return None


def _post(base, model, key, messages, timeout, temperature=0.2, max_tokens=2000,
          reasoning_effort="none"):
    """一次 chat/completions。

    ⚠ **必须把思考关掉**（`reasoning_effort="none"`）：实测 `deepseek-flash` 是思考型模型，
    默认会先吐 `reasoning_content`（一次决策 260~2000+ 个思考 token）：
      · `max_tokens=60`  → `finish_reason=length`、`content` 空串 → 100% 降级；
      · `max_tokens=2000` → 仍有 3/12 被截断，P95 延迟 11.6 秒；
      · 加 `reasoning_effort="none"` → **0.50 秒**返回、输出正常（实测）。
    """
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                 data=body, headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    except (TimeoutError, socket.timeout) as e:
        # ★ 2026-11-22：超时单独映射成 LLMTimeout（原来一律包成 LLMError，
        #   导致 LLMTimeout 成了"只有定义、从不抛出"的死代码；调用方也就没法区分
        #   "它在想但超时了" 还是 "根本连不上模型"）。
        raise LLMTimeout(f"超时（{timeout}s）：{type(e).__name__}: {e}")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise LLMTimeout(f"超时（{timeout}s）：{reason}")
        raise LLMError(f"连不上：{reason}")
    except Exception as e:
        raise LLMError(f"{type(e).__name__}: {e}")
    try:
        msg = data["choices"][0]["message"]
        text = msg.get("content") or ""
        finish = data["choices"][0].get("finish_reason")
    except Exception:
        raise LLMError(f"返回结构不对: {str(data)[:200]}")
    if not text.strip():
        raise LLMError(f"content 为空（finish_reason={finish}）—— 多半是 max_tokens 太小，"
                       f"思考 token 把预算吃光了")
    return text, time.time() - t0, data.get("usage") or {}


def parse_move(text: str):
    """从任意输出里抠出第一个合法出拳（大小写不敏感）；抠不到返回 None。"""
    if not text:
        return None
    m = re.search(r"\b(rock|paper|scissors)\b", text, re.I)
    if m:
        return m.group(1).lower()
    zh = {"石头": "rock", "剪刀": "scissors", "布": "paper"}
    for k, v in zh.items():
        if k in text:
            return v
    return None


class LLMDecider:
    """决策用 LLM：给历史 → 一个出拳。带耗时统计与失败计数（供界面显示降级率）。

    **2026-11-22 用户决定：不走本地**（原话"要，不走本地，太麻烦了，我相信我的网络"）
    ⇒ 默认**只走云端 Flash**；本机 8B 通道**默认关闭**（实测带策略库 99.2 秒、关掉也要
    15.26 秒，`local=8s` 必然全超时，等于白等 8 秒）。
    想离线演示时显式打开：`LLMDecider(allow_local=True)` 或环境变量 `RPS_ALLOW_LOCAL=1`。

    **超时分两档**（总指挥批准）：`flash=3s`、`local=8s`（本地档只在 allow_local 时才用）；
    传 `timeout=` 让两档取同一个值（兼容旧调用）。
    """

    def __init__(self, strategy_md: str | None = None, timeout=None,
                 prefer="flash", key=None, flash_timeout=3.0, local_timeout=8.0,
                 allow_local=None):
        if timeout is not None:              # 兼容：老调用只传一个 timeout
            flash_timeout = local_timeout = timeout
        self.flash_timeout = float(flash_timeout)
        self.local_timeout = float(local_timeout)
        self.timeout = self.flash_timeout    # 旧字段保留（有地方读它）
        self.prefer = prefer                 # flash | local（prefer=local 仍需 allow_local）
        # 默认 False：用户拍板"不走本地"；离线演示时用参数或环境变量打开
        self.allow_local = ((os.environ.get("RPS_ALLOW_LOCAL") == "1")
                            if allow_local is None else bool(allow_local))
        # 2026-11-22 用户拍板：本地档**代码级关闭**（原话"要"=采用代码级禁止方案）
        #   默认 never 尝试本地；只有显式 allow_local=True / RPS_ALLOW_LOCAL=1 才开，且启动时大声警告。
        if self.allow_local:
            print("[llm] 🔴 本地档已手动开启（RPS_ALLOW_LOCAL=1）：仅限离线演示！"
                  "现场请关掉——本地 8B 一轮实测 99.2 秒，会白等 8 秒超时。", flush=True)
        else:
            print("[llm] 本地档已禁用（默认）：只走云端 Flash + 规则兜底", flush=True)
        self.local_disabled = not self.allow_local
        self.key = key if key is not None else load_key()
        self.strategy_md = strategy_md if strategy_md is not None else _default_strategy()
        self.calls = 0
        self.failures = 0
        self.timeouts = 0                    # 单列：超时次数（与"连不上"分开统计）
        self.total_latency = 0.0
        self.last_error = ""
        self.latencies = []

    # ---------- 提示词 ----------
    def _system(self):
        parts = [
            "你是石头剪刀布对局里的机器人一方。你只能输出一个 JSON：",
            '{"move":"rock|paper|scissors","why":"不超过20字的理由"}',
            "规则：",
            "1. 你看到的是历史（对手出过什么、你出过什么、结果），听不到也看不到本轮对手出什么；",
            "2. 不要在 why 里写你接下来要出什么（会被提前看穿）；",
            "3. 只输出 JSON，不要解释、不要 Markdown。",
        ]
        if self.strategy_md:
            parts.append("\n【策略库】\n" + self.strategy_md.strip())
        return "\n".join(parts)

    def _user(self, history, my_moves):
        n = min(len(history), len(my_moves) if my_moves else len(history), 10)
        rows = []
        for i in range(-n, 0):
            h = history[i]
            m = my_moves[i] if my_moves and len(my_moves) >= abs(i) else "?"
            if h == m:
                r = "平"
            elif (h, m) in (("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")):
                r = "对手赢"          # 对手克制我
            else:
                r = "我赢"
            rows.append(f"{abs(i)}. 对手出{h}，我出{m} → {r}")
        body = ("历史：这是第一轮，没有任何历史。" if not rows
                else "最近 " + str(n) + " 轮（越靠下越新）：\n" + "\n".join(rows))
        return body + "\n\n" + hints(history, my_moves) + \
               "\n\n下一轮出什么？只回 JSON。"

    # ---------- 调用 ----------
    def _one(self, base, model, key, messages, timeout, retry_backoff=0.4):
        """单通道调用：遇到 429/5xx 只重试一次（仍在超时预算内），解析失败不重试。"""
        last = None
        for attempt in (1, 2):
            try:
                text, dt, usage = _post(base, model, key, messages, timeout)
            except LLMError as e:
                last = e
                transient = any(code in str(e) for code in ("HTTP 429", "HTTP 500",
                                                            "HTTP 502", "HTTP 503", "HTTP 504"))
                if attempt == 1 and transient:
                    time.sleep(retry_backoff)
                    continue
                raise
            move = parse_move(text)
            if move is None:
                raise LLMError(f"输出里没有合法出拳: {text[:120]!r}")
            return move, dt, usage
        raise last or LLMError("未知失败")

    def decide(self, history, my_moves=None, temperature=0.2):
        """返回 (move, info)。**失败一律抛异常**，由调用方兜底（不在这里重试）。

        失败时抛哪个异常是有讲究的：
          · 所有通道都超时 → `LLMTimeout`（调用方提示"思考超时"）
          · 其它（连不上、HTTP 错、输出解析不了）→ `LLMError`（提示"连不上模型"）
        """
        msgs = [{"role": "system", "content": self._system()},
                {"role": "user", "content": self._user(list(history or []), list(my_moves or []))}]
        # 2026-11-22 用户拍板"不走本地"：默认只排云端 Flash。
        # 本地通道只在 allow_local=True / RPS_ALLOW_LOCAL=1 时才排队（离线演示用）。
        # 2026-11-22 起额外兜一道：local_disabled=True 时**绝不放行**（防止 prefer=local 绕过）。
        # 好处：断网时不再白等 8 秒——直接失败、直接降级 Hedge。
        flash = (FLASH_BASE, FLASH_MODEL, self.key, self.flash_timeout)
        local = (LOCAL_BASE, LOCAL_MODEL, None, self.local_timeout)
        if getattr(self, "local_disabled", True) or not self.allow_local:
            chains = [flash]                    # 代码级禁止：prefer=local 也绕不过
        elif self.prefer == "local":
            chains = [local, flash]
        else:
            chains = [flash, local]

        errors = []
        timeout_hits = 0
        for base, model, key, tmo in chains:
            tag = "flash" if base == FLASH_BASE else "local"
            if base == FLASH_BASE and not key:
                errors.append("flash: 没有 key")
                continue
            self.calls += 1
            try:
                move, dt, usage = self._one(base, model, key, msgs, tmo)
                self.total_latency += dt
                self.latencies.append(dt)
                return move, {"channel": tag, "model": model, "latency": round(dt, 2),
                              "usage": usage, "hints": hints_summary(history, my_moves),
                              "timeout": tmo}
            except LLMTimeout as e:
                self.failures += 1
                self.timeouts += 1
                timeout_hits += 1
                errors.append(f"{tag}: {e}")
            except Exception as e:
                self.failures += 1
                errors.append(f"{tag}: {e}")
        self.last_error = " | ".join(errors)
        if errors and timeout_hits == len(errors):
            raise LLMTimeout(f"全部通道超时：{self.last_error}")
        raise LLMError(f"全部通道失败：{self.last_error}")

    # ---------- 统计 ----------
    def stats(self):
        lat = sorted(self.latencies)
        def pct(q):
            if not lat:
                return None
            i = min(len(lat) - 1, max(0, int(round(q * (len(lat) - 1)))))
            return round(lat[i], 2)
        return {"calls": self.calls, "failures": self.failures, "timeouts": self.timeouts,
                "p50": pct(0.50), "p95": pct(0.95), "max": round(max(lat), 2) if lat else None,
                "avg_latency": round(self.total_latency / max(1, len(lat)), 2),
                "fallback_rate": round(self.failures / max(1, self.calls), 3),
                "last_error": self.last_error}


def selftest(timeout=20):
    """命令行自检：`py scripts\\rps\\llm.py` —— 打印通道、耗时、解析结果。"""
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    d = LLMDecider(timeout=timeout)
    print("key:", "有" if d.key else "无", "| 首选:", d.prefer)
    for name, base, model, key in (("flash", FLASH_BASE, FLASH_MODEL, d.key),
                                   ("local", LOCAL_BASE, LOCAL_MODEL, None)):
        if base == FLASH_BASE and not key:
            print(f"{name:6} 跳过（无 key）")
            continue
        try:
            mv, dt, usage = d._one(base, model, key or "",
                                   [{"role": "user",
                                     "content": '只回 JSON：{"move":"rock","why":"测试"}'}],
                                   timeout)
            print(f"{name:6} ✅ {mv}  耗时 {dt:.2f}s  tokens={usage.get('total_tokens')}")
        except Exception as e:
            print(f"{name:6} ❌ {e}")


if __name__ == "__main__":
    selftest()
