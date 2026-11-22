"""帧编解码（link 层）—— 只做字节，不碰串口、不碰文件。

★ 第 0 节 · 这层管什么
    Aero Hand 的固件协议是**定长 16 字节帧**，一问一答：
        [op][0][ch0_lo][ch0_hi] ... [ch6_lo][ch6_hi]      ← 1 + 1 + 7×2 = 16
    · 下发（op=0x11）：7 个 u16 目标值，低字节在前
    · 查询（0x22 位置 / 0x24 电流 / 0x25 温度）：发 16 字节"问帧"（只有 op 有值），回 16 字节"答帧"
    为什么单独一层：上层（hal / 上层程序）不该知道字节序和偏移；改协议只改这一个文件。

★ 第 1 节 · 为什么写死 16 字节
    固件按固定长度读，短一帧就会错位。所以 `build_query` 一定补齐 16 字节；
    `parse_frame` 一定校验 `r[0] == op` 且长度 16，不合法就返回 None（让上层重试）。

★ 第 2 节 · 电流为什么要有符号
    舵机回的是 u16，但电流是**带符号**的（同一通道正反向出力）。约定：≥32768 视为负数
    （减 65536）。这条在旧脚本里散落过多份，现在只在这里做一次。
"""
from __future__ import annotations

OP_CTRL_POS = 0x11      # 下发 7 个目标
OP_GET_POS = 0x22       # 读位置
OP_GET_CURR = 0x24      # 读电流（带符号）
OP_GET_TEMP = 0x25      # 读温度

FRAME_LEN = 16
N_CH = 7


def build_set_targets(targets) -> bytes:
    """7 个目标值 → 16 字节下发帧（u16 小端，低字节在前）。"""
    if len(targets) != N_CH:
        raise ValueError(f"需要 {N_CH} 个值，收到 {len(targets)} 个")
    payload = bytearray([OP_CTRL_POS, 0])
    for v in targets:
        vi = int(v) & 0xFFFF
        payload += bytes([vi & 0xFF, (vi >> 8) & 0xFF])
    return bytes(payload)


def build_query(op: int) -> bytes:
    """问帧：16 字节，只有首字节是 op，其余补 0。"""
    if len(bytes([op])) != 1:
        raise ValueError("op 必须是 0~255")
    return bytes([op, 0] + [0] * (FRAME_LEN - 2))


def parse_frame(buf: bytes, op: int):
    """16 字节答帧 → 7 个 u16；长度或首字节不对返回 None。"""
    if buf is None or len(buf) != FRAME_LEN or buf[0] != op:
        return None
    return [(buf[2 + 2 * i + 1] << 8) | (buf[2 + 2 * i]) for i in range(N_CH)]


def to_signed(v: int) -> int:
    """u16 → 带符号整数（电流用）。"""
    return v - 65536 if v >= 32768 else v


def signed_all(values):
    """整串 u16 → 带符号；None 原样返回 None（数据缺了就是缺了，别造 0）。"""
    if values is None:
        return None
    return [to_signed(v) for v in values]
