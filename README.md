# Aero Hand RPS · 石头剪刀布人机对局

用一只**开源的腱驱动灵巧手**（Aero Hand Open，16 关节 / 7 舵机）跟人玩石头剪刀布：
摄像头认你的手势，机器人**先锁定出拳**（只根据历史，不看你这轮出什么），然后真手出拳、并带一个会说垃圾话的人设。

> 本项目是**二次开发**：硬件与固件来自 Aero Hand Open；这里的代码是"把它跑起来、能演示、能验收"的那一层。

## 它能做什么

- **猜拳对局**（核心）：摄像头认石头/剪刀/布 → 机器人按策略出拳 → 真机摆手势；一局 10 轮
- **手势识别**：两版 —— 猜拳专用（3 类，稳）/ 全量（12 类）
- **手动控制台**：7 通道滑块 + 17 个实测姿势 + 序列播放 + 保存/覆盖
- **本地 HTTP API**：24 个接口、token 鉴权、FIFO 队列，供别的程序驱动
- **安全**：急停（温度 52°C / 电流 320×3 / 标记文件三路）、退出即卸力、一键全关

## 快速开始（只玩对局，不接真手）

```bash
# 1) 装 uv（https://docs.astral.sh/uv/）
# 2) 建环境
uv venv --python 3.12 .venv312
uv pip install --python .venv312/Scripts/python.exe -r requirements-vision.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
# 3) 确认模型在 models/hand_landmarker.task
# 4) 双击 启动对局-摄像头.bat
```

> 视觉必须 **Python 3.12**（mediapipe 没有 3.13 的轮子）；模型文件不在 requirements 里，需随仓库一起拿。

## 接真手（可选）

需要 Aero Hand Open 实物 + 6V 电源 + `agent/` 与 `sim2real/`（本仓库已包含）。
先双击 `上电自检.bat` 看电流/温度，再 `启动对局-真机.bat`；出事就 `急停.bat` 或 `全关.bat`。

## 目录

| 路径 | 作用 |
|---|---|
| `scripts/rps/` | 对局内核：4 个规则策略、13 个模拟人、LLM 决策链、5 个人设、状态机 |
| `scripts/30_rps_game.py` | 对局 GUI（摄像头认人 + 真机出拳 + 四时间戳） |
| `scripts/27b_vision_rps.py` | 猜拳专用识别（3 类，中间态一律丢弃） |
| `scripts/27_vision_gesture.py` | 全量识别（12 类手势） |
| `agent/` | 唯一碰串口的薄壳（8 个公开 API + 5 个错误码） |
| `scripts/24_gui_manual.py` | 手动控制台 v3 |
| `scripts/26_api.py` | 本地 HTTP API（24 接口） |
| `sim2real/` | 姿势库（17 个真机实测值）+ 安全上下限 |
| `手册-操作-v1.md` | **给别人用的操作手册**（开箱 → 四个入口 → 故障表 → 换电脑四步） |

## 公平性怎么保证

机器人的决策函数签名是 `decide(history, my_moves)` —— **参数里没有"本轮人手"**，物理上拿不到；
出拳后立刻写入 `sim2real/rps_lock.json`（锁定时间戳），然后才轮到人出拳；
一轮内有四个时间戳 `decide ≤ lock ≤ human ≤ reveal`，代码里有 `assert` 硬校验。

## 对外口径（引用本项目时请带上）

- 姿势：**17 个系统姿势**（真机实测）
- 手势识别：**12 种**
- LLM：**云端 Flash + 规则兜底**（不走本地模型）
- 胜率：同条件 **72.4% vs Hedge 64.9%**；**对随机人类上限 50%**（不要宣传 100%）

## 致谢与许可

- 硬件/固件/仿真：**Aero Hand Open**（原项目，遵循其许可）
- 本仓库代码：**MIT**（见 `LICENSE`），欢迎照着手册搭一只
- 模型：MediaPipe HandLandmarker（Google，Apache-2.0）

## 免责

这是**演示级**装置：手指会动、会夹、有舵机，操作时请有人看着，别让小孩/宠物靠近；
上电前先读 `手册-操作-v1.md` 的四条红线。
