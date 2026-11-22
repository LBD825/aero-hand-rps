"""安全释放机制（2026-11-21 事故后新增）

**事故**：演示脚本被中断时，舵机仍保持最后姿态（ch4 卡在 57777、电流 176），
一路烧到 54°C，用户看到"不动了"。

**原理**：位置控制舵机的出力 ∝ (目标 − 当前)。所以"卸力"不是断电，而是
**把目标设成当前读回的位置** —— 误差为 0，舵机就不再出力，手指靠弹簧回位。

用法（包一层 with，任何退出路径都会卸力）：

    from safe import SafeGuard
    guard = SafeGuard(send_fn, read_pos_fn)
    with guard:
        ... 正常动作 ...

    # 正常结束、异常、Ctrl-C、SIGTERM、甚至进程被杀前的 atexit，都会调 release()。
    # Ctrl-C / SIGTERM 时还会额外打印一行，让人知道"手已卸力"。
"""
from __future__ import annotations

import atexit
import signal
import sys
import time


class SafeGuard:
    def __init__(self, send, read_pos, verbose: bool = True):
        self.send = send              # send(list[7]) —— 下发 7 通道目标
        self.read_pos = read_pos      # read_pos() -> list[7] 或 None
        self.verbose = verbose
        self.released = False
        self._prev_handlers = {}

    # ---------- 释放 ----------
    def release(self, reason: str = "") -> None:
        if self.released:
            return
        self.released = True
        try:
            pos = self.read_pos()
            if pos:
                self.send(list(pos))          # 目标 = 当前位置 → 零误差 → 卸力
                if self.verbose:
                    print(f"\n[安全释放] 目标已设为当前位置，舵机卸力{('（' + reason + '）') if reason else ''}")
        except Exception as e:                # 串口已断之类，无法释放
            if self.verbose:
                print(f"\n[安全释放] 失败：{e}（可能串口已断，请手动断电）", file=sys.stderr)

    # ---------- 信号 ----------
    def _on_signal(self, signum, frame):
        self.release(f"信号 {signum}")
        raise SystemExit(0)

    def install(self):
        atexit.register(self.release)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._prev_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except Exception:
                pass
        return self

    # ---------- with 支持 ----------
    def __enter__(self):
        self.install()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release("异常退出" if exc_type else "正常结束")
        return False


def heartbeat_release(hand_pos_fn, idle_sec: float = 8.0, idle_cb=None):
    """看护用：返回一个"喂狗"函数；超过 idle_sec 没有喂狗，就调 idle_cb（一般是卸力）。

    在主循环里每帧调一次 feed() 即可。用于"动作突然停了但脚本没退出"的场景
    （比如卡在某个阻塞调用里）。
    """
    last = [time.time()]

    def feed():
        last[0] = time.time()

    # 注意：本函数不自带线程，调用方需要在合适处检查 is_idle()
    def is_idle():
        return (time.time() - last[0]) > idle_sec

    return feed, is_idle
