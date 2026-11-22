"""hal 层（硬件抽象）—— **全工程唯一碰串口的地方**。

★ 第 0 节 · 为什么要有这一层
    在 `agent\\` 之前，"开串口 + 拼 16 字节帧 + 读回解析"这件事在
    `26_api.py`、`24_gui_manual.py`、`09/11/18/19/20/21` 里各写了一份（最多时 8 份）。
    后果：改一处帧格式要改 8 个文件，漏一个就出现"某个脚本行为不一样"。
    现在：字节在 `link.py`，串口在 `hal.py`，**上层只调方法**。

★ 第 1 节 · 找手与开串口（两条铁律）
    ① 认板不认口名：找 `VID 0x303A`（XIAO ESP32-S3）自动选口，不写死 COM5；
    ② 开不上不许直接崩：`监控-温度.bat` 会独占串口，重试 12 秒并给"关掉监控窗口"的提示。

★ 第 2 节 · 16 字节一问一答（细节在 link.py）
    读：先 `reset_input_buffer()` 清残帧 → 发问帧 → 等 80ms → 读满 16 字节 → 校验首字节 = op；
    不成就在内部重试 2 次，仍失败返回 **None**（不是 0！缺数据就是缺数据，别造假）。

★ 第 3 节 · 电平和速度
    `send()` 只下发目标值，**速度/加减速由舵机自己走梯形曲线**（0.3~0.6 秒）。
    所以"瞬时下发"≠"瞬间到位"，这也是 `/status` 刚发完可能读到半路位置的原因。
"""
from __future__ import annotations

import threading
import time

from . import link

BAUD = 921600
VID_AERO = 0x303A
PORT_RETRIES = 12
PORT_RETRY_WAIT = 1.0
READ_TIMEOUT = 0.5
READ_SETTLE = 0.08


def find_port():
    """按 VID 找手，返回串口名；找不到返回 None。"""
    from serial.tools import list_ports
    for p in list_ports.comports():
        if (p.vid or 0) == VID_AERO:
            return p.device
    return None


class RealHand:
    """真机的最小接口：send / pos / curr / temp / port / close。"""

    def __init__(self, port: str | None = None, baud: int = BAUD):
        import serial
        from . import AgentError

        self._lock = threading.Lock()
        self.port = port or find_port()
        if self.port is None:
            raise AgentError("NO_HAND", "找不到手（VID 0x303A）—— 检查 USB 和 6V 电源")

        last = None
        for i in range(PORT_RETRIES):
            try:
                self._s = serial.Serial(self.port, baud, timeout=READ_TIMEOUT)
                break
            except Exception as e:                      # 多半是被监控窗口独占
                last = e
                print(f"[hal] {self.port} 暂时打不开（第 {i + 1} 次）：{e} —— 1 秒后重试",
                      flush=True)
                time.sleep(PORT_RETRY_WAIT)
        else:
            raise AgentError(
                "PORT_BUSY",
                f"打不开 {self.port}：{last}\n"
                "  常见原因：① 监控-温度.bat 还开着（独占串口）② 上一个脚本没退 ③ USB 松了",
            )
        time.sleep(1.2)                                  # 等固件启动日志吐完
        self._s.reset_input_buffer()
        try:
            self._s.read(400)
        except Exception:
            pass

    # ---------- 底层读 ----------
    def _read(self, op: int):
        with self._lock:
            for _ in range(2):
                try:
                    self._s.reset_input_buffer()
                    self._s.write(link.build_query(op))
                    self._s.flush()
                    time.sleep(READ_SETTLE)
                    vals = link.parse_frame(self._s.read(link.FRAME_LEN), op)
                    if vals is not None:
                        return vals
                except Exception:
                    return None
            return None

    def pos(self):
        return self._read(link.OP_GET_POS)

    def curr(self):
        return link.signed_all(self._read(link.OP_GET_CURR))

    def temp(self):
        return self._read(link.OP_GET_TEMP)

    # ---------- 底层写 ----------
    def send(self, targets):
        with self._lock:
            self._s.write(link.build_set_targets(targets))
            self._s.flush()

    def close(self):
        with self._lock:
            try:
                self._s.close()
            except Exception:
                pass
