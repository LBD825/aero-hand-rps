# Aero Hand 本地 API 说明（v5，2026-11-21）

> 目的：让**程序**（语音智能体、网页、另一台电脑、Python/C++ 脚本）能控制这只手，
> 不用关心串口和 16 字节帧格式——那些都在 API 内部。

---

## 一、启动

双击项目根 **`启动API.bat`**（或 `py scripts\26_api.py`），会开一个黑窗口：

```
✅ Aero Hand API v2：http://127.0.0.1:8765/
   串口 COM5；14 个姿势 / 4 个序列 / 速度 0s（瞬时）
   接口说明：http://127.0.0.1:8765/docs
```

- **关掉黑窗口 = 停止 API**（退出前会自动卸力）；
- 浏览器打开 `http://127.0.0.1:8765/` 是个**可点按钮的测试页**；
- **不能和 GUI 同时开**（同一个串口）。

参数：

| 参数 | 作用 |
|---|---|
| `--host 127.0.0.1` | 默认，只允许本机（安全） |
| `--host 0.0.0.0` | 允许局域网访问（**API 无鉴权，别开公网**） |
| `--port 8765` | 端口，改动后记得同步客户端 |

---

## 二、接口一览

### 只读（GET）

| 路径 | 返回 |
|---|---|
| `/` | 测试页（HTML，可点按钮） |
| `/docs` | 本说明（纯文本） |
| `/health` | 心跳，**不读串口**，最省：`{"ok":true,"port":"COM5","poses":13}` |
| `/status` | 位置/电流/温度/当前速度 |
| `/poses` | 14 个姿势 + 序列（含 label/note/slow） |
| `/limits` | 安全区间（每通道 lo/hi） |
| `/user_poses` | 我在 GUI 里记录的姿势 |
| `/state` | **当前目标值 + 实测 + 速度 + 是否在播序列 + 急停标记** |
| `/estop_status` | 急停标记是否存在、内容 |

### 动作（POST，JSON body）

| 路径 | body | 说明 |
|---|---|---|
| `/pose` | `{"name":"rock","secs":1.5}` | 按名字做姿势（`secs` 可省，用当前速度） |
| `/open` | `{}` | 回张开姿势 |
| `/targets` | `{"targets":[7个数]}` | 瞬时下发（自动按 limits 夹紧） |
| `/move` | `{"targets":[7个数],"secs":1.5}` | 平滑移动 |
| `/sequence` | `{"name":"demo_gestures","secs":1.5}` | 播放序列（**异步**，立刻返回） |
| `/stop` | `{}` | 停止正在播放的序列/动作 |
| `/release` | `{}` | 卸力（目标=当前位置） |
| `/estop` | `{}` | 急停（卸力 + 写 ESTOP 标记，会杀掉所有脚本） |
| `/speed` | `{"factor":2}` 或 `{"secs":0.75}` | 设置动作速度 |
| `/channel` | `{"ch":3,"value":40000,"secs":1.2}` | **单通道**设值（`secs` 可省＝瞬时） |
| `/nudge` | `{"ch":3,"delta":1000}` | 单通道**微调 ±N**（等价 GUI 的 -1k/+1k） |
| `/clear_estop` | `{}` | 清除急停标记 |

### 编辑姿势

| 路径 | body | 说明 |
|---|---|---|
| `/pose/targets` | `{"name":"rock","targets":[7个数]}` | **覆盖** `poses.json` 里某姿势（并在 note 标记"API 覆盖"） |
| `/save_pose` | `{"name":"我的姿势","targets":[7个数]}` | 存进 `poses_user.json`（可省 targets＝用当前实测位置） |
| `/user_pose` | `{"name":"我的姿势","secs":1.5}` | 载入并执行 poses_user 里的姿势 |
| `/delete_user` | `{"name":"我的姿势"}` | 删除 poses_user 里的记录 |

---

## 三、速度换算

| factor | secs | 感觉 |
|---|---|---|
| **0** | **0（默认）** | **瞬时——立刻下发目标**（舵机自己还有 0.3~0.6s 行程） |
| 0.5 | 3.0 | 慢，适合细看 |
| 1.0 | 1.5 | 标准（默认） |
| 1.5 | 1.0 | 快 |
| 2.0 | 0.75 | 很快 |

> 姿势里若有 `slow` 字段（如某些姿势的拇指要慢），会在该速度基础上再乘倍数。

---

## 四、调用示例

### PowerShell（注意中文要转 UTF-8 字节）

```powershell
$base = "http://127.0.0.1:8765"

# 查状态
Invoke-RestMethod "$base/status"

# 做石头
Invoke-RestMethod "$base/pose" -Method Post -ContentType "application/json" `
  -Body '{"name":"rock"}'

# 设速度 2×
Invoke-RestMethod "$base/speed" -Method Post -ContentType "application/json" `
  -Body '{"factor":2}'

# 存中文名的姿势（必须用 UTF-8 字节，否则中文变问号！）
$json  = '{"name":"中文姿势","targets":[25000,300,400,150,150,150,0]}'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
Invoke-RestMethod "$base/save_pose" -Method Post `
  -ContentType "application/json; charset=utf-8" -Body $bytes
```

### Python

```python
import json, urllib.request

def post(path, obj):
    req = urllib.request.Request("http://127.0.0.1:8765" + path,
                                 data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

print(post("/pose", {"name": "rock"}))
print(post("/sequence", {"name": "opposition", "secs": 1.2}))
```

### C++（给自己写的程序用，最简单的方式是调 PowerShell/curl）

```cpp
// 或者用 libcurl：POST http://127.0.0.1:8765/pose  body {"name":"rock"}
```

---

## 四之二、鉴权（token）

API 默认**要 token**，防止同一局域网的其它程序随手动手。

- token 存 `sim2real\.api_token`（启动时自动生成，删掉会重新生成）；
- 启动日志里会打印：`🔑 token（请求头 X-Token）：xxxx`；
- 两种传法：

```powershell
# 方式 A：请求头
Invoke-RestMethod http://127.0.0.1:8765/state -Headers @{"X-Token"="<token>"}

# 方式 B：URL 参数
Invoke-RestMethod "http://127.0.0.1:8765/status?token=<token>"
```

| 路径 | 要不要 token |
|---|---|
| `/`、`/health` | **免**（测试页、健康检查） |
| 其它全部 | **要**（没有就返回 `401`） |

调试时若不想带 token：`py scripts\26_api.py --no-auth`（**只在本机临时用**）。

---

## 四之三、请求队列（FIFO）

所有"会动手"的请求都进一个**队列**，由一个 worker 线程**串行**执行：

```
请求1 ─┐
请求2 ─┼─► queue.Queue ─► 单 worker ─► 串口（一次只有一个动作在跑）
请求3 ─┘
```

**好处**：多个客户端（语音智能体 + 网页 + 脚本）同时发请求时，**不会互相打断**，
而是按到达顺序一个个执行。实测：并发 4 个 `/pose` 全部完成，终态＝最后一个请求。

**注意**：队列是"先到先服务"，没有优先级；长动作（如 3 秒的序列）会把后面的请求堵住一会儿。

---

## 四之四、瞬时模式（默认速度 0）

用户 2026-11-21 要求：**所有速度默认瞬时**。

- API 启动默认 `secs = 0`；GUI 的速度下拉框默认也是「瞬时」；
- 瞬时＝**立刻下发目标值**，舵机再自己按梯形加减速走完（约 0.3~0.6 秒）；
- 因此**刚发完就读 `/status` 可能看到"半路"位置**，隔 0.5 秒再读更可靠；
- 想要平滑过渡就显式指定：`{"name":"rock","secs":1.5}` 或 `POST /speed {"factor":1}`。

---

## 五、安全约定

| 项 | 说明 |
|---|---|
| 值夹紧 | 所有下发值都按 `limits.json` 夹紧，发不出越界值 |
| 温度 | ≥50°C 卸力（`estop.py` 在 ≥52°C 硬停） |
| 电流 | 连续 3 次 ≥320 触发急停 |
| 退出 | 关窗口/ Ctrl-C 都会先卸力 |
| 并发 | 所有动作串行（一把锁），不会两个请求同时动手 |
| 网络 | 默认只绑 127.0.0.1；开 0.0.0.0 前先想清楚谁能访问 |

---

## 六、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `找不到手（VID 0x303A）` | USB 或 6V 没接；插好后等 3 秒再启动 |
| `打不开 COM5` | GUI/监控窗口占着串口 → 关掉它 |
| 中文名变 `????` | PowerShell 没用 UTF-8 字节发 body（见上面示例） |
| 想边看界面边用 API | 做不到（一个串口）；用浏览器测试页代替 GUI |
