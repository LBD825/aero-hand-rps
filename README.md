# Aero Hand RPS · 石头剪刀布人机对局

[![Read the write-up](https://img.shields.io/badge/%E8%AE%BA%E6%96%87-%E7%9F%B3%E5%A4%B4%E5%89%AA%E5%88%80%E5%B8%83%E9%87%8C%E7%9A%84%E8%AF%BB%E5%BF%83%E6%9C%AF-0f766e)](https://lbd825.github.io/aero-hand-rps/)

[English](README.en.md) ｜ **中文**

[![CI](https://github.com/LBD825/aero-hand-rps/actions/workflows/ci.yml/badge.svg)](https://github.com/LBD825/aero-hand-rps/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-3776ab.svg)](https://www.python.org/)
[![Hardware](https://img.shields.io/badge/hardware-Aero%20Hand%20Open-0f766e.svg)](https://github.com/hand-e-open/AeroHandOpen)

用一只**开源的腱驱动灵巧手**（Aero Hand Open，16 关节 / 7 舵机、欠驱动）跟人玩石头剪刀布：
摄像头认你的手势，机器人**先锁定出拳**（只根据对局历史，物理上拿不到你这轮出什么），
然后真手出拳、并带一个会说垃圾话的人设。

> 本项目是**二次开发**：硬件与固件来自 Aero Hand Open；这里的代码是"把它跑起来、能演示、能验收"的那一层。

## 它能做什么

| 功能 | 说明 |
|---|---|
| **猜拳对局**（核心） | 摄像头认石头/剪刀/布 → 按策略决策 → 真机摆手势；一局 10 轮 |
| **手势识别（两版）** | 猜拳专用（3 类，很稳）/ 全量（12 类） |
| **手动控制台** | 7 通道滑块 + **17 个真机实测姿势** + 序列播放 + 保存/覆盖 |
| **本地 HTTP API** | **24 个接口**、token 鉴权、FIFO 队列，供别的程序驱动 |
| **安全** | 急停三路触发（52°C / 电流 320×3 / 标记文件）、退出即卸力、一键全关 |

## 快速开始（只玩对局，不接真手）

```bash
# 1) 装 uv（https://docs.astral.sh/uv/）
# 2) 建视觉环境 —— 必须 Python 3.12（mediapipe 没有 3.13 轮子）
uv venv --python 3.12 .venv312
uv pip install --python .venv312/bin/python -r requirements-vision.txt
# 3) 确认 models/hand_landmarker.task 在（7.5 MB，不在 requirements 里）
# 4) 跑 scripts/30_rps_game.py --cam（Windows 双击对应 .bat）
```

> 国内网络建议加清华镜像：`--index-url https://pypi.tuna.tsinghua.edu.cn/simple`

## 接真手（可选）

需要 Aero Hand Open 实物 + 6V 电源 + `agent/` 与 `sim2real/`（本仓库已包含）。
先跑上电自检看电流/温度，再进对局；**急停/全关**脚本会立刻卸力。

## 它为什么不会出老千

决策函数签名是 `decide(history, my_moves)` —— **参数里没有"本轮人手"**，物理上拿不到；
出拳后立刻写入锁定文件（带时间戳），然后才轮到人出拳；
一轮内有四个时间戳 `decide ≤ lock ≤ human ≤ reveal`，代码里有 `assert` 硬校验。

## 怎么验证的

| 项 | 结果 |
|---|---|
| 契约测试 `tests/test_agent_dryrun.py` | **32 项断言**，CI 在 Linux + Windows × Python 3.12/3.13 跑 |
| 真机对局 | **72 轮**，四时间戳 **72/72** 有序，手势识别 **72/72**，0 超时 |
| 姿势库 | **17 个**，全部真机实测，每通道都落在 `sim2real/limits.json` 内 |

## 对外口径（引用本项目时请带上）

- 姿势：**17 个系统姿势**（真机实测）
- 手势识别：**12 种**
- LLM：**云端 Flash + 规则兜底**（不走本地模型）
- 胜率：同条件 **72.4% vs Hedge 64.9%**；**对随机人类上限 50%**（不要宣传 100%）

## 目录

```
agent/          唯一碰串口的薄壳（8 个公开 API + 5 个错误码）
scripts/rps/    对局内核：4 个规则策略、13 个模拟人、LLM 决策链、5 个人设
scripts/        对局 GUI、两版手势识别、手动控制台、本地 API、安全与工具
sim2real/       姿势库（17 个实测值）+ 安全上下限
models/         MediaPipe 手势模型
tests/          契约测试（dry-run，不碰硬件）
手册-操作-v1.md  给别人用的操作手册（开箱 → 四个入口 → 故障表 → 换电脑四步）
```

## 致谢与许可

- 硬件/固件/仿真：**Aero Hand Open**（遵循其许可）
- 本仓库代码：**MIT**（见 [LICENSE](LICENSE)）
- 模型：MediaPipe HandLandmarker（Google，Apache-2.0）

## 免责

演示级装置：手指会动、会夹、有舵机，通电时请有人看着，别让小孩/宠物靠近；
首次上电前先读 `手册-操作-v1.md` 的安全红线。
