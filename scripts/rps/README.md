# `scripts\rps\` · RPS 人机对局的核心包

> 编制 v4 下的**唯一 RPS 代码写者**：本线（RPS 线）。
> 只改这个目录；`30_rps_game.py`（界面）、`poses.json`/`limits.json` 归执行部，**不碰**。

## 一、文件分工

| 文件 | 干什么 |
|---|---|
| `backends.py` | 4 个规则策略（random/stats/fusion/hedge），**clone 已修**（专家改类） |
| `humans.py` | 13 个人类模拟器 + `match()`（两侧种子错开、无决胜轮返回 None） |
| `llm.py` | 决策 LLM：**只走云端 Flash**（2026-11-22 用户拍板"不走本地"；本地 8B 带策略库实测 99.2s，`local=8s` 必然超时）。离线演示想开本地：`LLMDecider(allow_local=True)` 或环境变量 `RPS_ALLOW_LOCAL=1`。
**代码级默认禁止（2026-11-22 用户拍板）**：`LLMDecider` 启动就打印一行 `[llm] 本地档已禁用（默认）：只走云端 Flash + 规则兜底`；决策链里 `local_disabled` 是硬兜底，`prefer="local"` 也绕不过。
> 🔴 **仅离线演示可用，现场禁止开**：本地 8B 带策略库实测 **一轮 99.2 秒**，而 local 超时只有 8 秒 ⇒ 误开＝每轮白等 8 秒再降级，观众看到的就是卡顿。含 `facts()/hints()/hints_summary()` 六路判据 |
| `persona.py` | 5 种人设 + `review()` 赛后复盘（只说话不出拳） |
| `flow.py` | 对局状态机：决策 → 锁定 → 垃圾话 → 人出拳 → 裁判 → 下发；事件 `decider/degraded/recovered/hints`；`rps_history.json` 落盘（`writer` 固定串 `rps-line`） |
| `策略库.md` / `策略库-精简.md` | 8 条人类规律 + 表 A（反制型正确应答）；精简版注入提示词 |
| `eval_m1.py` / `eval_q23.py` | 离线评估（策略 × 对手） |
| `check_degraded.py` | **降级/恢复自检**（见下） |

## 二、改 `flow.py` 之前必须知道的两条

1. **改熔断/降级逻辑 → 必跑** `py scripts\rps\check_degraded.py`，要求**四项全 PASS、退出码 0**；
2. **`check_degraded.py` 是手写验证**：它不会自动跟随 `flow.py` 变化，改熔断逻辑必须**同步维护它**；
   而且它**不许手动把待测状态置回**（例如 `game.llm_off_for_bout = False`）——要模拟"新的一局"，
   只能调真实入口 `game.start_bout()`。这是 2026-11-21 的"假 PASS"教训，已立为质量红线。

## 三、CLI 常用命令

```powershell
# 降级/恢复自检（纯软件，不需要手）
py scripts\rps\check_degraded.py

# 离线评估：M1（策略 × 4 对手）
py scripts\rps\eval_m1.py --rounds 60 --seeds 1 --kinds counter,wsls,outcome1,random --strategies llm

# 离线评估：Q23（6 对手，样本更大，慢）
py scripts\rps\eval_q23.py --rounds 100 --seeds 1,2 --strategies hedge,fusion,llm
```

## 四、约定

- 决策函数签名**只有 `(history, my_moves)`**，物理上拿不到本轮人出拳——这是公平性的实现保证，**不许改**；
- 失败文案要分清「思考超时」与「连不上模型」（`LLMTimeout` vs `LLMError`）；
- 文档写错时**按代码改正，但保留变更说明**（例：`llm.py` 头部"原本写 8 秒"那段别删）。
