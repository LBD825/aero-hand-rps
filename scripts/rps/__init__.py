"""`rps` 包 —— RPS 人机对局的全部分层。

    backends.py  4 个规则策略（random/stats/fusion/hedge，已修 clone bug）
    humans.py    13 个人类模拟器（离线评估用）
    llm.py       决策 LLM（云端 Flash 首选 / 本地 8B 后备）
    persona.py   人设（5 种风格，只管说话）
    flow.py      对局状态机（决策→锁定→垃圾话→人出拳→裁判→下发）+ 离线评估
    策略库.md    注入决策提示词的行为规律库
    persona/     人设文件（每个风格一个 md）+ 人设.json 参数
"""
from . import backends, flow, humans, llm, persona  # noqa: F401

__all__ = ["backends", "flow", "humans", "llm", "persona"]
