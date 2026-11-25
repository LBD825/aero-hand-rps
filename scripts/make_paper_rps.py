# -*- coding: utf-8 -*-
"""生成给大一同学的实验报告：HTML（好看、可打印）+ DOCX（发微信群，带封面与目录）

版本：A 版（约 3800 字），按用户 2026-11-25 的删改意见：
  · 删掉「0 先讲个真事」
  · 删掉规则里的「种子要分开」那条
  · 删掉「五条可执行建议」与「老实交代踩的坑」两节
  · 「慢热学生 B」→「罗不叮算法」
  · A 标明是 Kaggle 第 37 名方案（不写"冠军"）
  · 复现改为「代码已开源在 GitHub」+ 仓库链接
  · docx 加封面页、目录、页脚页码

用法：py scripts\\make_paper_rps.py
输出：C:\\Users\\llact\\Desktop\\桌面文档\\石头剪刀布里的读心术.html / .docx
"""
from __future__ import annotations

import html as H
import re
import sys
from pathlib import Path

OUT_DIR = Path(r"C:\Users\llact\Desktop\桌面文档")
TITLE = "石头剪刀布里的读心术"
SUBTITLE = "三个机器人、七万五千局，和一场关于「猜不透」的实验"
AUTHOR = "罗京镜"
DATE = "2026-11-25"
NOTE = "实验与撰写由 AI 辅助；数据全部来自本机实测，可复现"
REPO = "https://github.com/LBD825/aero-hand-rps"

BLOCKS: list[tuple] = []


def h2(t): BLOCKS.append(("h2", t))
def h3(t): BLOCKS.append(("h3", t))
def p(t): BLOCKS.append(("p", t))
def quote(t): BLOCKS.append(("quote", t))
def tip(t): BLOCKS.append(("tip", t))
def warn(t): BLOCKS.append(("warn", t))
def ul(items): BLOCKS.append(("list", items))
def table(head, rows): BLOCKS.append(("table", head, rows))
def code(t): BLOCKS.append(("code", t))
def faq(items): BLOCKS.append(("faq", items))


# ═══════════════════════════ 摘要 ═══════════════════════════
quote(
    "**一句话结论**：我们让四种「猜拳策略」两两互打 **75,000 局**。"
    "除了一个特例，所有算法都只能拿到约 **1/3** 的胜率。那个特例是 **41.3%**，"
    "它的原因不是「算法更强」，而是**对手的行为更容易被读**。"
    "更反直觉的是：我前后调整了 15 组关键参数，**一次都没能突破 35%**。"
)
p(
    "这篇文章写给刚进大学的同学。它讲的是一件很小的事——石头剪刀布——"
    "但它会用掉你未来科研里最常用的几样东西：**怎么设计一个不算耍赖的实验、"
    "怎么读一张统计表、怎么解释「我赢了你」和「我比你强」的区别**。"
)
p(
    "全文没有一张图，因为我想让你习惯用**数字和表格**说话；"
    "术语第一次出现时都会翻译成大白话。如果你时间很紧，读第 2、4、5 节就够。"
)

# ═══════════════════════════ 1 ═══════════════════════════
h2("1 · 这张桌子：三个机器人")
p("为了把「谁更强」变成能测的东西，我们造了三个会自己出拳的「选手」。先认识一下：")
table(
    ["外号", "它到底是什么", "它怎么决定出什么"],
    [
        ["**读心者 A**", "20 个「小专家」组成的委员会", "每个专家各自猜我下一步出什么；过去谁猜得准，这次就让它多投票（份额随机抽）"],
        ["**罗不叮算法**", "一个会记笔记的玩家", "**前 500 局故意瞎出**（顺便偷学对手规律）；后 500 局改用学到的规律反制"],
        ["**老赌徒 Hedge7**", "7 个老江湖同时下注", "谁最近准就加大谁的注；下注时还带随机性，不让人看穿"],
    ],
)
p(
    "另外我们还放了一个「纯随机」当**校准线**：它不参与排名，只用来验证装置。"
    "如果某个算法连纯随机都打不过，那说明它不是「不够聪明」，而是**代码写错了**。"
)
h3("1.1 读心者 A：考试第 37 名的方案")
p(
    "A 的结构来自 Kaggle 2020-21 年「石头剪刀布」比赛的**第 37 名公开方案**"
    "（`0Zeta/RockPaperScissors`，2852 行、33 个类）。"
    "说清楚一点：**它不是冠军**。冠军方案的正文挂在 Kaggle 上、要登录才能读，我没能拿到；"
    "第 37 名这一份是完整可读的，而且用的正是这一类「多专家 + 狄利克雷加权」的思路，所以我拿它做骨架。"
)
p(
    "它内部有 20 个专家，每个专家都是一个「猜测小模型」，比如："
    "「对手上一次出石头时，这次常出布」「最近三手是 石-石-布 时，后面常接剪刀」"
    "「对手刚输了一局，通常会改出别的」。"
)
p(
    "每一局，20 个专家同时举手说出自己的建议；A 再按**每个专家过去的战绩**分配投票份额，"
    "而份额**每一局都重新随机抽取**。战绩好的专家抽到大份额的概率更高，但它也可能抽到小份额。"
    "这个「按战绩分蛋糕、每次重切」的动作，背后的数学叫**狄利克雷分布采样**。"
)
p(
    "为什么非要随机？如果永远只听最强的那个专家，对手只要摸清「A 现在听谁的」就能反制。"
    "加入随机性，等于**给自己戴上手套，让别人摸不出你的牌路**——"
    "这和扑克高手「偶尔诈唬」是同一个道理。"
)
h3("1.2 罗不叮算法：先装傻 500 局，再摊牌")
p(
    "这个算法的设计很像考试策略。前 500 局它**完全随机出拳**，看起来毫无威胁；"
    "但每局结束后它都在做笔记：对手在「我出石头之后」常出什么、"
    "「最近两手是 布-剪刀 之后」常接什么、以及**它自己上一个动作被对手怎么对待**。"
)
p(
    "500 局一过，它拿出 10 个「预测器」，让它们各自猜对手下一步，"
    "然后**只挑当前战绩最好的那一个**，出「克制它」的拳；另外留 10% 的余量随机出，免得自己被看穿。"
)
p(
    "这个设计有个隐含赌注：**它假设对手的规律是稳定的**。"
    "只要对手确实有固定习惯，它就会越打越准。第 5 节你会看到，这个赌注在遇到「也会适应」的对手时，会付出代价。"
)
h3("1.3 老赌徒 Hedge7：七个老江湖同时下注")
p(
    "Hedge7 里有 7 个专家，风格各异：一个永远说「各三分之一」当底噪、"
    "一个统计对手整体偏好、三个分别看最近一手/两手/三手的组合、"
    "一个记「上一局赢了之后对手会不会重复」、最后一个专门猜「对手在想我在想什么」。"
)
p(
    "它**不投票**，而是给 7 个专家**连续加权**：谁最近预测得准，权重就被放大；谁预测错，权重就被压低。"
    "最后把 7 个专家的预测按权重混成一张概率表，然后**按概率抽一个出拳**——"
    "不是抽最可能的，是**按概率抽**。这个「按概率抽」看着保守，实际很滑头："
    "对手就算完全知道权重表，也没法百分百预测它这一手出什么。"
)

# ═══════════════════════════ 2 ═══════════════════════════
h2("2 · 怎么比，才算不耍赖")
p("要让结论站得住，规则比算法更重要。下面几条，建议直接抄进你的实验本：")
ul([
    "**局数要够**：每一格 **3 局 × 1000 轮 × 5 个随机种子 = 15,000 轮**；五种对阵共 **75,000 局**，跑完 109 秒。",
    "**必须分前后半场统计**：因为罗不叮算法前 500 局故意乱出，会把总胜率稀释到 50% 上限。",
    "**必须报「抖动幅度」**：15,000 局下胜率的标准误约 **0.4 个百分点**，差别小于 1 个点一律写「打平」。",
    "**必须留一个已知答案的对照**：这里就是「打纯随机」，理论值各三分之一。",
])
p(
    "第 3 条那个 0.4 是算出来的，不是拍的：胜率的标准误 ≈ √(p(1−p)/n)。"
    "取 p=0.33、n=15000，得到 √(0.33×0.67/15000) ≈ 0.0038，也就是 **0.38 个百分点**。"
    "所以「33.7% 对 34.1%」这种 0.4 个点的差距，恰好在一倍标准误内——**它几乎一定是运气**。"
)
warn(
    "**这一条最容易错**：很多人只报一个总胜率。但在「前 500 局故意乱出」的设计里，"
    "总胜率的上限被硬压到 50%——哪怕后 500 局全胜，总共也只有 50%。"
    "所以我们把**主指标定为「后 500 轮胜率」**。**指标选错，实验做得再认真也说明不了问题。**"
)
tip(
    "**给大一同学**：以后写实验报告，先问自己一句——"
    "「我这个指标，会不会被实验设计本身压住上限？」如果会，就换指标或者分段统计。"
)

# ═══════════════════════════ 3 ═══════════════════════════
h2("3 · 结果：一张让人不太舒服的表")
table(
    ["对阵", "前 500 局胜率", "**后 500 局胜率**", "总胜 / 平 / 负"],
    [
        ["**读心者 A vs 罗不叮算法**", "32.8%", "**41.3%**", "37.0 / 28.8 / 34.1"],
        ["读心者 A vs 纯随机", "32.8%", "32.1%", "32.4 / 33.6 / 34.0"],
        ["罗不叮算法 vs 纯随机", "33.3%", "33.1%", "33.2 / 33.6 / 33.2"],
        ["读心者 A vs 老赌徒 Hedge7", "33.7%", "33.7%", "33.7 / 32.2 / 34.1"],
        ["罗不叮算法 vs 老赌徒 Hedge7", "33.3%", "33.5%", "33.4 / 33.0 / 33.6"],
    ],
)
p("盯着这张表看三秒，你会发现三件怪事。")
h3("怪事一：所有数字都挤在 33% 附近")
p(
    "除了第一行，其余四行全落在 **32.1% ~ 33.7%**。"
    "而 33.3% 恰好是「**各三分之一**」——胜、平、负各占一成。"
    "换句话说，**这些算法互相之间基本是白打的**。"
)
h3("怪事二：对纯随机，谁都赢不了")
p(
    "读心者 A 对纯随机是 **32.4%**，罗不叮算法是 **33.2%**。"
    "这不是它们弱：对手没有规律可读时，**任何策略的期望就是各三分之一**。"
    "想超过三分之一，你只能指望对手**有规律**。"
)
h3("怪事三：唯一显著的优势，来自「对手好读」")
p(
    "读心者 A 打罗不叮算法，后半场拿到 **41.3%**，比对方的 34.1% 高出**整整 7 个百分点**"
    "（15000 局下这个差距约等于 10 个标准误，不可能是运气）。"
)
p(
    "但同一个 A 去打 Hedge7，就只有 **33.7%**——和打纯随机几乎没差别。"
    "同一份代码，面对不同对手，一个像「完胜」，一个像「白打」。"
    "这就是为什么第 4 节要说：**「谁更强」这个问题，离开对手就没法回答**。"
)

# ═══════════════════════════ 4 ═══════════════════════════
h2("4 · 为什么大家都在 33% 上卡住")
p(
    "打个比方。你和朋友玩猜拳，你们都开始「读」对方："
    "他注意到你爱连出石头，于是出布；你注意到他发现你连出石头，于是改出剪刀；"
    "他发现你在改，于是又改……**几轮之后，双方都变成了「随便出」。**"
)
p(
    "这不是段子，而是博弈论里的标准结论：石头剪刀布**没有「纯策略」的稳定解**。"
    "任何固定的出拳习惯都会被对手利用，所以唯一稳定的做法是**三者等概率混合**。"
    "而当双方都做到这一点时，各自的胜率就都是三分之一。"
)
quote(
    "这个状态有个名字叫**无信息均衡**：对手的下一步对你来说不可预测时，"
    "你所有策略的期望收益都一样。而「互相适应」的过程，"
    "恰恰会把对方变得不可预测——**你的读心术越强，对手就越随机；对手越随机，你的读心术就越没用。**"
)
p(
    "更细一点说，这里有一个正反馈："
    "我一旦抓到你的规律，你就会因为输而改掉它；你一改，我原来的模型就失效，只能退回保守；"
    "我一保守，你就重新有空间建立新规律。**双方都在踩油门和刹车，最后车速趋同。**"
)
tip(
    "**给大一同学**：这解释了为什么很多论文里的「提升 3%」经不起复现——"
    "换一个对手、换一份数据，那 3% 就没了。看到「我们的方法显著优于基线」时，"
    "先问一句：**基线的行为可不可预测？**"
)

# ═══════════════════════════ 5 ═══════════════════════════
h2("5 · 那 41.3% 到底是怎么来的")
p(
    "既然 A 打谁都只有 33%，为什么打罗不叮算法能到 41%？答案不在 A 变强了，而在**对手的结构**。"
    "回到 1.2 节，它后半场做三件事：让 10 个预测器各猜一次 → **只信当前分数最高的那一个** → 出「克制它」的拳。"
)
ul([
    "**它只信一个预测器**：另外 9 个的意见被完全丢掉，一旦最准的那个过时，就整段瞎打。",
    "**它是确定性的**：只有 10% 的概率随机，其余 90% 严格按预测反制，行为高度可预测。",
    "**它会「跟」**：某个规律一旦被它抓住，它会一直跟——这就形成了**可以被反过来利用的惯性**。",
])
p(
    "而 A 有 20 个专家，其中就有专门盯「对手在跟我的节奏」这一类的。"
    "于是局面变成：**对手在认真地读 A，A 在认真地读「对手在读我」**。多一层，胜负就分出来了。"
)
table(
    ["选手", "结构特点", "是否容易被读", "结果"],
    [
        ["罗不叮算法", "只信一个预测器 + 90% 确定性反制", "**容易被读**", "被 A 打到 34.1%"],
        ["老赌徒 Hedge7", "7 个专家连续加权 + 按概率抽拳", "**不容易被读**", "与 A 打平 33.7%"],
    ],
)
p(
    "所以那个 41.3% 的正确解读是：**「A 能利用对手的惯性」**，不是「A 比 Hedge7 强」。"
    "这两句话在实验报告里是完全不同的结论，而很多报道会把它们混为一谈。"
)

# ═══════════════════════════ 6 ═══════════════════════════
h2("6 · 我拧了 15 个旋钮，结果一点没变")
p(
    "读心者 A 有三个「旋钮」：`scale`（赢者通吃程度：越大越专宠当前最强的专家）、"
    "`min_alpha`（每个专家的保底票数：越小越激进）、`decay`（忘性：越小越健忘）。"
    "看起来调一调就能变强，对吧？我试了："
)
table(
    ["实验", "组数", "结果"],
    [
        ["扫 scale × min_alpha（decay 固定 0.995）", "12 组", "平均 36.7% ~ 39.4%，**全在噪声带内**"],
        ["扫 decay（用上面最优的两个旋钮）", "3 组", "0.995 最好（39.4%），0.99 与 0.999 都更差"],
        ["**大样本确认**（15,000 局/对手）", "2 组", "默认 **41.3% / 33.7%** → 搜优 **41.6% / 33.9%**"],
    ],
)
p(
    "**调参只换来 +0.3 和 +0.2 个百分点**，而标准误是 0.4——等于没变。"
    "更要紧的是：**对 Hedge7 的胜率，在全部 12 组里都落在 30.5%~34.9%，从没突破 35%。**"
)
p(
    "为什么会这样？因为 A 拿不到「新信息」。"
    "20 个专家已经把这几种可用的规律（上一步、上两步、主频、模式匹配、我的反应）都覆盖了，"
    "换权重温度只是改变「它们在什么情况下更被信任」，**不产生新知识**。"
    "参数只能在**信息量不变**的前提下调整决策风格，所以打不过的对手还是打不过。"
)
quote(
    "**结论**：A 的瓶颈**不在参数**，而在「专家池对适应型对手拿不到新信息」。"
    "参数只让「运气」的波动变大变小，改不了平均值。"
)
tip(
    "**给大一同学**：这是特别值得记住的一条实验直觉——"
    "**先判断瓶颈是「信息」还是「参数」**。"
    "如果是信息问题，调参永远调不出来；如果是参数问题，粗调通常就够了。"
)

# ═══════════════════════════ 7 ═══════════════════════════
h2("7 · 这对「智能体」意味着什么")
p(
    "你可能会问：那我们能不能写出一个「必胜」的算法？"
    "在对手会学习的设定下，**不存在一招制胜**。但还有另一条路：**迭代**。"
)
p(
    "我们写不出最强的那一个，但可以让系统**不断变强**。这不是文字游戏："
    "近十年的博弈类 AI 基本都走这条路——**让模型自己和自己打，从对抗里长出能力**，"
    "而不是由人类手写规则。"
)
h3("7.1 迭代的四个层次（从易到难）")
ul([
    "**① 对手池**：别只打一个对手。攒一堆风格不同的对手轮流打，避免「只会打一个人」。",
    "**② 自博弈**：让「现在的我」打「上一版的我」，赢的那版留下来。这是 AlphaZero 一类系统的核心。",
    "**③ 课程学习**：先打弱的、再打强的，像刷题从易到难。直接打最强对手，往往学不动。",
    "**④ 欺骗 / 诱饵**：故意露出一个假规律，等对手上钩，再反过来吃它。"
    "这一层我们从没实现过，但它**最有可能突破 1/3 的天花板**——因为它是唯一「主动制造信息」的手段。",
])
p(
    "第四层值得多说两句。前三种都是「努力变强」，第四种是「**让对手变弱**」——"
    "在零和对抗里，后者往往更划算。现实中的例子：球队故意暴露战术迷惑对手、"
    "棋手开局走「看起来有坑」的着法钓对手。"
)
h3("7.2 再往上：学习架构")
p(
    "「具体怎么迭代」就不是这篇文章能回答的了，它属于**学习架构**层面："
    "用什么模型表示历史、怎么在探索与利用之间取舍、怎么防止「训练时很强、上场就崩」、"
    "怎么让多个版本互相竞争又不互相污染。"
    "这些是我们接下来要学的，也是我说「这篇实验只能指个方向」的意思。"
)
quote(
    "所以「能不能针对某个算法必胜」的诚实回答是：**能，但只在对手不长记性的前提下**。"
    "一旦对手也在迭代，胜负就变成一场双方都在变强的赛跑——而赛跑没有终点。"
)

# ═══════════════════════════ 8 ═══════════════════════════
h2("8 · 局限：哪些话我们还不能说")
ul([
    "**对手不是真人**。真人会累、会分心、会迷信「剪刀手」，这些规律我们还没测。",
    "**探索期只测了 500 局**。如果改成 200 或 800，结论可能变化。",
    "**超参只搜了 15 组**，没做完整网格，也没做多种子下的稳健性检查。",
    "**没有外部对手**（比如 Kaggle 上其他参赛程序），所以「强度」只在我们的池子里成立。",
    "**读心者 A 是第 37 名的结构复现，不是冠军实现**，两者能力可能差一截。",
])
p("以上任何一条被补上，本文的结论都可能需要修正。**能说清「什么时候不成立」，比多给一个数字更有用。**")

# ═══════════════════════════ 附录 A ═══════════════════════════
h2("附录 A · 术语翻译表")
table(
    ["专业说法", "大白话", "在这篇文章里指什么"],
    [
        ["狄利克雷分布采样", "按战绩分蛋糕，每次重新切", "给 20 个专家随机分投票份额"],
        ["多臂老虎机", "一排老虎机，边拉边学哪台出钱多", "在多个专家间按表现分配信任"],
        ["马尔可夫 / 转移矩阵", "看上一手是什么，猜下一手", "「他上次出石头，这次常出布」"],
        ["模式匹配", "翻旧账：找出历史上相同的片段", "「最近三步是 石-石-布，上次之后他出了剪刀」"],
        ["衰减（decay）", "记性：越久的事越模糊", "老战绩对当前判断的影响变小"],
        ["超参数", "旋钮", "scale / min_alpha / decay 这类事先定好的数值"],
        ["无信息均衡", "互相猜不透，只好都瞎出", "所有算法胜率都趋近 1/3"],
        ["标准误", "平均值的抖动幅度", "15,000 局下约 0.4 个百分点"],
        ["自博弈", "左右手互搏", "自己打自己，赢的版本留下"],
        ["课程学习", "刷题从易到难", "先打弱对手再打强对手"],
        ["乘法权重 / Hedge", "谁准就给谁加权，错了就减权", "Hedge7 更新专家权重的方法"],
        ["ε 探索", "偶尔故意随机一下", "罗不叮算法保留的 10% 随机出拳"],
    ],
)

# ═══════════════════════════ 附录 B ═══════════════════════════
h2("附录 B · 复现：代码已经开源")
p(
    "这套实验的全部代码已放进开源仓库，**不需要你装任何东西就能读**："
)
code(REPO)
p(
    "直接跑到结果所需的三个文件：`scripts\\rps\\backends_kaggle.py`（读心者 A、罗不叮算法）、"
    "`scripts\\eval_kaggle.py`（对战与统计）、`scripts\\eval_kaggle_sweep.py`（超参搜优）。"
    "仓库根目录的 `记录-RPS算法对抗-20261122.md` 有完整数据表与分析过程。"
)
p("在仓库里复现，两条命令（纯 CPU，两分钟内跑完）：")
code(
    "python scripts/eval_kaggle.py --rounds 1000 --matches 3 --seeds 5 --controls\n"
    "python scripts/eval_kaggle_sweep.py"
)
p(
    "复现时请先确认三件事：① 报胜率时同时报标准误；② 保留「对纯随机」那一格作为校准；"
    "③ 前后半场分开统计。这三条任一缺失，结果都不能引用。"
)

# ═══════════════════════════ 参考 ═══════════════════════════
h2("参考与延伸阅读")
ul([
    "Kaggle 竞赛 Rock, Paper, Scissors（2020-2021）：https://www.kaggle.com/competitions/rock-paper-scissors",
    "公开源码 0Zeta/RockPaperScissors（该赛事**第 37 名**方案；本文只参考其算法结构，未拷贝代码，因其仓库未声明许可证）",
    "Freund, Y. & Schapire, R. E. (1997). A decision-theoretic generalization of on-line learning and an application to boosting. JCSS.",
    "Billings, D. (2000). The First International RoShamBo Programming Competition（多层反制策略 Iocaine Powder 的来源）。",
    "本文全部代码与数据：https://github.com/LBD825/aero-hand-rps",
])

# ───────────────────────── 渲染：HTML ─────────────────────────
CSS = """
:root{--ink:#1a1f2b;--muted:#5b6472;--line:#e3e7ee;--accent:#0f766e;
--accent2:#b45309;--bg:#fbfbfd;--card:#ffffff;--tip:#eefaf7;--warn:#fff7ed;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif;
line-height:1.85;font-size:16.5px;-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:40px 22px 90px}
header.cover{border-bottom:3px solid var(--accent);padding-bottom:26px;margin-bottom:30px}
.kicker{letter-spacing:.22em;font-size:12.5px;color:var(--accent);font-weight:700}
h1{font-size:37px;line-height:1.25;margin:14px 0 8px;letter-spacing:-.5px}
.sub{font-size:17.5px;color:var(--muted);margin:0 0 18px}
.meta{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:13.5px;color:var(--muted)}
.meta b{color:var(--ink);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:26px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 14px}
.card .n{font-size:27px;font-weight:800;color:var(--accent);line-height:1.2}
.card .l{font-size:12.5px;color:var(--muted);margin-top:6px}
nav.toc{background:#f3f6fa;border:1px solid var(--line);border-radius:14px;
padding:16px 20px;margin:26px 0 34px;font-size:14.5px}
nav.toc ol{margin:8px 0 0;padding-left:22px;columns:2;column-gap:34px}
nav.toc a{color:var(--ink);text-decoration:none;border-bottom:1px dotted #b8c2d0}
nav.toc a:hover{color:var(--accent);border-bottom-color:var(--accent)}
h2{font-size:24px;margin:52px 0 14px;padding-left:13px;border-left:5px solid var(--accent);letter-spacing:-.2px}
h3{font-size:18.5px;margin:26px 0 8px;color:#0b3d3a}
p{margin:13px 0}
strong{font-weight:700}
blockquote{margin:22px 0;padding:16px 20px;background:var(--tip);
border-left:5px solid var(--accent);border-radius:0 12px 12px 0;font-size:16.5px}
blockquote p{margin:0}
.tip{margin:20px 0;padding:15px 18px;background:#eefaf7;border:1px dashed #7fd1c0;border-radius:12px;font-size:15.5px}
.warn{margin:20px 0;padding:15px 18px;background:var(--warn);border:1px solid #f6c177;border-radius:12px;font-size:15.5px}
.tip::before{content:"💡 给大一同学　";font-weight:700;color:#0f766e}
.warn::before{content:"⚠️ 注意　";font-weight:700;color:#b45309}
table{width:100%;border-collapse:collapse;margin:20px 0;font-size:14.8px;background:var(--card);
border-radius:12px;overflow:hidden;box-shadow:0 1px 0 var(--line)}
th,td{padding:11px 13px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f1f5f9;font-weight:700;font-size:14px;color:#0b3d3a}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fcfdff}
td strong{color:#0b3d3a}
ul,ol{margin:13px 0;padding-left:26px}
li{margin:8px 0}
pre{background:#0f172a;color:#e2e8f0;padding:16px 18px;border-radius:12px;overflow-x:auto;font-size:13.8px;line-height:1.7}
code{font-family:"Cascadia Code",Consolas,monospace;font-size:.92em}
p code,li code{background:#eef2f7;color:#0b3d3a;padding:1px 6px;border-radius:6px}
footer{margin-top:64px;padding-top:22px;border-top:1px solid var(--line);color:var(--muted);font-size:13.5px}
@media (max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}nav.toc ol{columns:1}h1{font-size:29px}}
@media print{body{background:#fff;font-size:11.5pt}.wrap{max-width:100%;padding:0}
nav.toc{break-inside:avoid}h2{break-after:avoid}table{break-inside:avoid}
.card,blockquote,.tip,.warn{break-inside:avoid}}
"""


def inline(t: str) -> str:
    s = H.escape(t)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def anchor(t: str) -> str:
    return re.sub(r"[^\w]+", "-", t)[:40]


def render_html() -> str:
    parts: list[str] = []
    for b in BLOCKS:
        kind = b[0]
        if kind == "h2":
            parts.append(f'<h2 id="{anchor(b[1])}">{inline(b[1])}</h2>')
        elif kind == "h3":
            parts.append(f"<h3>{inline(b[1])}</h3>")
        elif kind == "p":
            parts.append(f"<p>{inline(b[1])}</p>")
        elif kind == "quote":
            parts.append(f"<blockquote><p>{inline(b[1])}</p></blockquote>")
        elif kind == "tip":
            parts.append(f'<div class="tip">{inline(b[1])}</div>')
        elif kind == "warn":
            parts.append(f'<div class="warn">{inline(b[1])}</div>')
        elif kind == "list":
            parts.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ul>")
        elif kind == "table":
            head, rows = b[1], b[2]
            th = "".join(f"<th>{inline(h)}</th>" for h in head)
            tb = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            parts.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
        elif kind == "code":
            parts.append(f"<pre><code>{H.escape(b[1])}</code></pre>")

    toc = "".join(f'<li><a href="#{anchor(b[1])}">{inline(b[1])}</a></li>'
                  for b in BLOCKS if b[0] == "h2")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{TITLE} · {SUBTITLE}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header class="cover">
  <div class="kicker">写给大一同学的实验报告 · 多智能体对抗</div>
  <h1>{TITLE}</h1>
  <p class="sub">{SUBTITLE}</p>
  <div class="meta"><span>作者：<b>{AUTHOR}</b></span><span>{DATE}</span><span>{NOTE}</span></div>
  <div class="cards">
    <div class="card"><div class="n">75,000</div><div class="l">总局数</div></div>
    <div class="card"><div class="n">1/3</div><div class="l">几乎所有算法的归宿</div></div>
    <div class="card"><div class="n">41.3%</div><div class="l">唯一显著优势（后半场）</div></div>
    <div class="card"><div class="n">+0.3pp</div><div class="l">调 15 组超参的收益</div></div>
  </div>
</header>
<nav class="toc"><b>目录</b><ol>{toc}</ol></nav>
<article>
{chr(10).join(parts)}
</article>
<footer>
  <p>{AUTHOR} · {DATE}　|　{NOTE}</p>
  <p>数据与代码：<a href="{REPO}">{REPO}</a></p>
  <p>本文为科普向实验报告，结论限定于「本文的三个对手与 75,000 局」这一范围。</p>
</footer>
</div></body></html>"""


# ───────────────────────── 渲染：DOCX（带封面 + 目录） ─────────────────────────
def render_docx(path: Path) -> None:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    ACCENT = (15, 118, 110)
    DEEP = (11, 61, 58)
    MUTED = (91, 100, 114)
    WARN = (180, 83, 9)

    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = sec.bottom_margin = Cm(2.2)
    sec.left_margin = sec.right_margin = Cm(2.4)

    normal = doc.styles["Normal"]
    normal.font.name = "微软雅黑"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    normal.paragraph_format.line_spacing = 1.55
    normal.paragraph_format.space_after = Pt(6)

    def set_cn(run, size=10.5, bold=False, color=None):
        run.font.name = "微软雅黑"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        run.font.size = Pt(size)
        run.bold = bold
        if color:
            run.font.color.rgb = RGBColor(*color)

    def rich(par, text, size=10.5):
        for chunk in re.split(r"(\*\*.+?\*\*|`.+?`)", text):
            if not chunk:
                continue
            if chunk.startswith("**") and chunk.endswith("**"):
                set_cn(par.add_run(chunk[2:-2]), size, bold=True)
            elif chunk.startswith("`") and chunk.endswith("`"):
                r = par.add_run(chunk[1:-1])
                r.font.name = "Consolas"
                r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
                r.font.size = Pt(size - 0.5)
            else:
                set_cn(par.add_run(chunk), size)

    def hrule(par):
        pPr = par._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "12")
        bottom.set(qn("w:space"), "6")
        bottom.set(qn("w:color"), "0F766E")
        pbdr.append(bottom)
        pPr.append(pbdr)

    def shade(par, hexcolor):
        pPr = par._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), hexcolor)
        pPr.append(shd)

    def add_page_number_footer():
        footer = sec.footer
        par = footer.paragraphs[0]
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = par.add_run()
        for el, attr in (("w:fldChar", "begin"), ("w:instrText", "PAGE"), ("w:fldChar", "end")):
            e = OxmlElement(el)
            if el == "w:fldChar":
                e.set(qn("w:fldCharType"), attr)
            else:
                e.set(qn("xml:space"), "preserve")
                e.text = " PAGE "
            run._r.append(e)
        set_cn(run, 9, False, MUTED)

    add_page_number_footer()

    # ── 封面 ──
    for _ in range(3):
        doc.add_paragraph()
    kick = doc.add_paragraph()
    kick.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(kick.add_run("写给大一同学的实验报告 · 多智能体对抗"), 11, True, ACCENT)
    doc.add_paragraph()
    tp = doc.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(tp.add_run(TITLE), 34, True, ACCENT)
    sp = doc.add_paragraph()
    sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(sp.add_run(SUBTITLE), 13, False, MUTED)
    doc.add_paragraph()
    line = doc.add_paragraph()
    line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hrule(line)
    doc.add_paragraph()
    mp = doc.add_paragraph()
    mp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(mp.add_run(f"作者：{AUTHOR}"), 11.5, True, DEEP)
    dp = doc.add_paragraph()
    dp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(dp.add_run(DATE), 10.5, False, MUTED)
    np_ = doc.add_paragraph()
    np_.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(np_.add_run(NOTE), 9.5, False, MUTED)
    doc.add_paragraph()
    rp = doc.add_paragraph()
    rp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(rp.add_run(REPO), 9.5, False, ACCENT)
    doc.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)

    # ── 目录（手工列出章节，不带页码，Word 里点标题可导航） ──
    t = doc.add_paragraph()
    set_cn(t.add_run("目录"), 18, True, ACCENT)
    for b in BLOCKS:
        if b[0] == "h2":
            par = doc.add_paragraph()
            par.paragraph_format.left_indent = Cm(0.6)
            par.paragraph_format.space_after = Pt(3)
            set_cn(par.add_run(b[1].replace("**", "")), 11, False, DEEP)
    doc.paragraphs[-1].add_run().add_break(WD_BREAK.PAGE)

    # ── 正文 ──
    for b in BLOCKS:
        kind = b[0]
        if kind == "h2":
            par = doc.add_paragraph()
            par.paragraph_format.space_before = Pt(16)
            par.paragraph_format.space_after = Pt(8)
            set_cn(par.add_run(b[1].replace("**", "")), 15, True, ACCENT)
            hrule(par)
        elif kind == "h3":
            par = doc.add_paragraph()
            par.paragraph_format.space_before = Pt(9)
            set_cn(par.add_run(b[1].replace("**", "")), 12, True, DEEP)
        elif kind == "p":
            rich(doc.add_paragraph(), b[1])
        elif kind in ("quote", "tip", "warn"):
            par = doc.add_paragraph()
            par.paragraph_format.left_indent = Cm(0.55)
            par.paragraph_format.space_before = Pt(8)
            par.paragraph_format.space_after = Pt(8)
            shade(par, "F3F7FB" if kind == "quote" else ("EEFAF7" if kind == "tip" else "FFF7ED"))
            head = {"quote": "", "tip": "💡 给大一同学： ", "warn": "⚠️ 注意： "}[kind]
            if head:
                set_cn(par.add_run(head), 10.5, True, WARN if kind == "warn" else ACCENT)
            rich(par, b[1])
        elif kind == "list":
            for item in b[1]:
                rich(doc.add_paragraph(style="List Bullet"), item)
        elif kind == "table":
            head, rows = b[1], b[2]
            tb = doc.add_table(rows=1, cols=len(head))
            tb.style = "Light Grid Accent 1"
            tb.alignment = WD_TABLE_ALIGNMENT.CENTER
            for i, hh in enumerate(head):
                cell = tb.rows[0].cells[i]
                cell.text = ""
                set_cn(cell.paragraphs[0].add_run(hh.replace("**", "")), 9.5, True)
            for r in rows:
                cells = tb.add_row().cells
                for i, c in enumerate(r):
                    cells[i].text = ""
                    rich(cells[i].paragraphs[0], c, 9.0)
            doc.add_paragraph()
        elif kind == "code":
            par = doc.add_paragraph()
            shade(par, "F1F5F9")
            r = par.add_run(b[1])
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.size = Pt(9)
            par.paragraph_format.left_indent = Cm(0.4)

    doc.add_paragraph()
    fin = doc.add_paragraph()
    fin.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn(fin.add_run("本文为科普向实验报告，结论限定于「本文的三个对手与 75,000 局」这一范围。"),
           9.5, False, MUTED)
    doc.save(path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUT_DIR / f"{TITLE}.html"
    docx_path = OUT_DIR / f"{TITLE}.docx"
    html_path.write_text(render_html(), encoding="utf-8")
    render_docx(docx_path)

    text = "".join(str(b[1]) for b in BLOCKS if isinstance(b[1], str))
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    print(f"✅ HTML：{html_path}（{html_path.stat().st_size/1024:.1f} KB）")
    print(f"✅ DOCX：{docx_path}（{docx_path.stat().st_size/1024:.1f} KB）")
    print(f"   正文汉字约 {cn} 字；标题 {sum(1 for b in BLOCKS if b[0]=='h2')} 节；表格 {sum(1 for b in BLOCKS if b[0]=='table')} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
