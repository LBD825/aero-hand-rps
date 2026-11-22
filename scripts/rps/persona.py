"""人设层（N0）—— **只管说话，绝不出拳**。

★ 为什么单独一层
    对局里 LLM 有两次调用：`llm.LLMDecider`（管手）和 `Persona`（管嘴）。
    两套提示词**完全隔离**：人设不知道策略库，决策不知道人设。
    否则"毒舌/话痨"这种性格会改变出拳分布，M1 的胜率评估就不可比了。

★ 预算
    垃圾话 2 秒硬上限、自我介绍 3 秒；**失败就跳过**（宁可不说话，不许耽误比赛）。

★ 离线也能跑
    `dry_run=True` 时用内置模板（每个风格几句轮换），完全不出网——
    这样 M0 离线全链不需要 key、不需要网络也能演示"它说话了"。

★ 铁律（写进每个 persona/*.md）
    不许说出本轮要出什么。哪怕是暗示、哪怕反着说，都算泄密。
"""
from __future__ import annotations

import json
import os
import random
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PERSONA_DIR = os.path.join(HERE, "persona")
CFG_PATH = os.path.join(PERSONA_DIR, "人设.json")
DEFAULT_CFG = {
    "persona_id": "feiyu", "name": "大肥鱼", "sass": 2, "max_chars": 40,
    "lines": 2, "taunt_rate": 1.0, "intro_lines": 2, "allow_emoji": False,
    "temperature": 0.9,
}

# 离线模板：每个风格几句，按 (赢/输/平/开局) 挑。**都不含出拳信息。**
MOCK = {
    "feiyu": {
        "intro": ["听说你要跟我打石头剪刀布", "我这条鱼今天手气不错哦"],
        "win": ["这把我认了行吧", "手有点滑"],
        "lose": ["诶我又赢了", "怎么感觉你在让我"],
        "draw": ["平了", "再来一把"],
        "neutral": ["来啊", "别磨蹭"],
    },
    "referee": {
        "intro": ["对局开始，五轮一局", "请准备出手"],
        "win": ["本轮结束，比分更新", "继续"],
        "lose": ["本轮结束，比分更新", "继续下一轮"],
        "draw": ["本轮平局", "重置，继续"],
        "neutral": ["等待出手", "请出手"],
    },
    "chunibyo": {
        "intro": ["来了吗", "这一局的胜负，我早已看见"],
        "win": ["不过是让了你一手", "真正的战斗现在才开始"],
        "lose": ["命运的齿轮已经开始转动", "看到我的实力了吧"],
        "draw": ["势均力敌", "下一击定胜负"],
        "neutral": ["出手吧", "让它开始"],
    },
    "analyst": {
        "intro": ["样本量 0，先按均匀分布", "开始采样"],
        "win": ["本轮换拳率上升，样本仍不足", "记录已更新"],
        "lose": ["命中率偏离预期", "分布开始收敛"],
        "draw": ["双方分布重合", "无信息增益"],
        "neutral": ["等待输入", "采样继续"],
    },
    "slacker": {
        "intro": ["行吧", "打几把我下班"],
        "win": ["输了就输了呗", "反正也没加班费"],
        "lose": ["诶我还能赢啊", "那再赢两把"],
        "draw": ["平了", "差不多得了"],
        "neutral": ["随便啦", "来"],
    },
}
STYLES = ("feiyu", "referee", "chunibyo", "analyst", "slacker")
NAMES = {"feiyu": "大肥鱼", "referee": "冷面裁判", "chunibyo": "中二少年",
         "analyst": "数据分析师", "slacker": "摊手咸鱼"}


def load_cfg(path=CFG_PATH):
    cfg = dict(DEFAULT_CFG)
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                cfg.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    except Exception:
        pass
    return cfg


class Persona:
    """一个人的嘴。`talk(kind, ctx)` 返回 ≤2 句台词；失败返回空串（不抛异常）。"""

    def __init__(self, persona_id=None, dry_run=True, timeout=2.0, seed=None, cfg=None):
        self.cfg = dict(cfg or load_cfg())
        self.persona_id = persona_id or self.cfg.get("persona_id", "feiyu")
        self.name = NAMES.get(self.persona_id) or self.cfg.get("name") or self.persona_id
        self.dry_run = dry_run
        self.timeout = timeout
        self.rng = random.Random(seed)
        self._md = self._read_md(self.persona_id)
        self.said = []            # 说过的话（复盘用）
        self.errors = 0
        self.total_latency = 0.0
        self._n = 0

    # ---------- 资源 ----------
    @staticmethod
    def _read_md(pid):
        p = os.path.join(PERSONA_DIR, f"{pid}.md")
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def switch(self, persona_id):
        self.persona_id = persona_id
        self._md = self._read_md(persona_id)
        self.name = NAMES.get(persona_id, persona_id)

    # ---------- 上下文 ----------
    @staticmethod
    def _context(kind, ctx):
        ctx = ctx or {}
        score = ctx.get("score", {})
        hist = ctx.get("history") or []
        tail = hist[-5:]
        parts = [f"场景：{'开局自我介绍' if kind == 'intro' else '本轮已锁定出拳，说一句垃圾话'}"]
        parts.append(f"比分（对手胜-机械手胜-平）：{score.get('human', 0)}-{score.get('robot', 0)}-{score.get('draw', 0)}")
        if tail:
            parts.append("最近几轮：" + "；".join(
                f"对手{r['human']}/我{r['robot']}→{'我赢' if r['result'] == 'robot' else '对手赢' if r['result'] == 'human' else '平'}"
                for r in tail))
        else:
            parts.append("最近几轮：无（这是第一轮）")
        return "\n".join(parts)

    # ---------- 说话 ----------
    def talk(self, kind="taunt", ctx=None):
        """kind: 'intro' | 'taunt'。返回 1~2 句；失败返回 ''。"""
        self._n += 1
        if kind == "taunt" and self.rng.random() > float(self.cfg.get("taunt_rate", 1.0)):
            return ""
        if self.dry_run:
            line = self._mock(kind, ctx)
            self.said.append(line)
            return line
        try:
            line = self._call_llm(kind, ctx)
        except Exception:
            self.errors += 1
            line = self._mock(kind, ctx)      # 联网失败就用离线台词，比赛照跑
        if line:
            self.said.append(line)
        return line

    def _call_llm(self, kind, ctx):
        from . import llm as L
        key = L.load_key()
        if not key:
            raise L.LLMError("没有 key")
        msgs = [{"role": "system", "content": self._md},
                {"role": "user", "content": self._context(kind, ctx)
                 + f"\n\n要求：说 {self.cfg.get('intro_lines', 2) if kind == 'intro' else self.cfg.get('lines', 2)}"
                 + f" 句以内，每句不超过 {self.cfg.get('max_chars', 40)} 字；不要说你要出什么。"}]
        t0 = time.time()
        text, dt, _ = L._post(L.FLASH_BASE, L.FLASH_MODEL, key, msgs, self.timeout,
                              temperature=float(self.cfg.get("temperature", 0.9)),
                              max_tokens=120)
        self.total_latency += time.time() - t0
        return self._clean(text)

    def _clean(self, text):
        text = (text or "").strip().strip('"“”')
        text = text.replace("\n\n", "\n")
        lines = [ln.strip(" \t-•") for ln in text.splitlines() if ln.strip()]
        n = int(self.cfg.get("intro_lines", 2)) if len(self.said) == 0 else int(self.cfg.get("lines", 2))
        lines = lines[:max(1, n)]
        out = "\n".join(ln[:int(self.cfg.get("max_chars", 40))] for ln in lines)
        return out if len(out) <= 200 else out[:200]

    def _mock(self, kind, ctx):
        pool = MOCK.get(self.persona_id) or MOCK["feiyu"]
        if kind == "intro":
            return "\n".join(pool["intro"][:int(self.cfg.get("intro_lines", 2))])
        # 按上一轮结果挑一句，避免每次同一句
        hist = (ctx or {}).get("history") or []
        last = hist[-1] if hist else None
        key = "neutral"
        if last:
            key = {"robot": "lose", "human": "win", "draw": "draw"}.get(last["result"], "neutral")
        cands = pool.get(key) or pool["neutral"]
        return cands[self.rng.randrange(len(cands))]

    def review(self, history, score, timeout=6.0):
        """赛后复盘（Q24 轻量对话版）：用同一人设说 1~3 行，只展示、不参与决策。

        `history` 是本局各轮的 [{human, robot, result}]；失败返回 ''（绝不阻塞收尾）。
        """
        if not history:
            return ""
        tail = history[-5:]
        lines = "；".join(
            f"对手{r['human']}/我{r['robot']}→"
            f"{'我赢' if r['result'] == 'robot' else '对手赢' if r['result'] == 'human' else '平'}"
            for r in tail)
        ctx = (f"比分（对手胜-机械手胜-平）：{score.get('human', 0)}-{score.get('robot', 0)}-"
               f"{score.get('draw', 0)}\n本局五轮：{lines}")
        if self.dry_run:
            pool = MOCK.get(self.persona_id) or MOCK["feiyu"]
            line = (pool.get("lose") or pool["neutral"])[0]
            self.said.append("[复盘]" + line)
            return line
        try:
            from . import llm as L
            key = L.load_key()
            if not key:
                raise L.LLMError("没有 key")
            msgs = [{"role": "system", "content": self._md},
                    {"role": "user", "content": ctx +
                     "\n\n请用不超过 3 行复盘这一局：我输在哪、下一局改什么。"
                     "只说话，不要说你要出什么。"}]
            text, _dt, _u = L._post(L.FLASH_BASE, L.FLASH_MODEL, key, msgs, timeout,
                                    temperature=float(self.cfg.get("temperature", 0.9)),
                                    max_tokens=300)
            out = self._clean(text)
            if out:
                self.said.append("[复盘]" + out)
            return out
        except Exception:
            self.errors += 1
            return ""

    def stats(self):
        return {"persona": self.persona_id, "said": len(self.said),
                "errors": self.errors,
                "avg_latency": round(self.total_latency / max(1, self._n), 2)}


def available():
    return [p[:-3] for p in sorted(os.listdir(PERSONA_DIR))
            if p.endswith(".md") and p[:-3] in STYLES]


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("可用风格:", available())
    for pid in STYLES:
        p = Persona(pid, dry_run=True)
        print(f"\n[{pid} / {p.name}]")
        print(" 开局:", p.talk("intro").replace("\n", " / "))
        print(" 垃圾话:", p.talk("taunt", {"score": {"human": 1, "robot": 2, "draw": 0},
                                        "history": [{"human": "rock", "robot": "scissors", "result": "human"}]}).replace("\n", " / "))
