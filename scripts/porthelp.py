"""串口小工具：带重试与友好提示地打开手（COM5）

为什么要它：`监控-温度.bat` 一开着就独占 COM5，别的脚本再跑就报
"PermissionError(13, 拒绝访问)"，堆一大段红字，看不出真正原因。
这里统一处理：重试若干秒，失败时直接告诉用户"关掉监控窗口"。
"""
from __future__ import annotations

import sys
import time


def open_hand(baud: int = 921600, retries: int = 12, wait: float = 1.0):
    import serial
    from serial.tools import list_ports

    port = None
    for p in list_ports.comports():
        if (p.vid or 0) == 0x303A:
            port = p.device
            break
    if port is None:
        raise SystemExit("找不到手（VID 0x303A）—— 检查 USB 线和板子电源。")

    last = None
    for i in range(retries):
        try:
            s = serial.Serial(port, baud, timeout=0.5)
            time.sleep(1.0)
            s.reset_input_buffer()
            try:
                s.read(500)          # 吃掉上电启动日志
            except Exception:
                pass
            return s
        except Exception as e:  # 多半是端口被独占
            last = e
            if i == 0:
                print(f"{port} 暂时打不开，可能被占用（监控窗口/上一轮脚本还在跑）…重试中", file=sys.stderr)
            time.sleep(wait)

    raise SystemExit(
        f"打不开 {port}：{last}\n"
        "常见原因与处理：\n"
        "  1) 监控-温度.bat 窗口还开着 → 关掉它（它会独占串口）\n"
        "  2) 上一个脚本还没退出 → 等它结束或关掉那个窗口\n"
        "  3) USB 线松了 / 板子掉电 → 重新插一下\n"
    )
