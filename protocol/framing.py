"""帧编解码：4 字节大端长度前缀 + 1 字节版本 + UTF-8 JSON 消息体。

帧格式（LEN 含 VER，即 LEN = 5 + len(body)）：

    0        3 4      5                              N
    +----------+--------+--------------------------------+
    | LEN 4B   | VER 1B | BODY (UTF-8 JSON object)       |
    +----------+--------+--------------------------------+

边界规则：
- 最多 64 KB；LEN 超限立即抛 E_FRAME_TOO_LARGE（不等待剩余字节）。
- 数据不足一个完整帧时抛 IncompleteFrame，调用方保留缓冲区继续收。
- 空 body（LEN == 5）解析为 {}，是否合法由 messages.validate 判定。
"""

from __future__ import annotations

import json
import struct

from .errors import E_BAD_REQUEST, E_FRAME_TOO_LARGE, IncompleteFrame, ProtocolError

FRAME_VER = 0x01
MAX_FRAME_BYTES = 64 * 1024
_HEADER_LEN = 5
_LEN_STRUCT = struct.Struct(">IB")


def encode(msg: dict) -> bytes:
    """把消息字典编码为完整帧；超过 64 KB 视为编程错误并抛 E_FRAME_TOO_LARGE。"""
    if not isinstance(msg, dict):
        raise ProtocolError(E_BAD_REQUEST, "待编码消息必须是 dict")

    body = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = _HEADER_LEN + len(body)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(
            E_FRAME_TOO_LARGE,
            f"帧长度 {length} 超过上限 {MAX_FRAME_BYTES} 字节",
        )
    return _LEN_STRUCT.pack(length, FRAME_VER) + body


def decode(buf: bytes) -> tuple[dict, bytes]:
    """从缓冲区解析一帧，返回 (msg, rest)；数据不足时抛 IncompleteFrame。"""
    if len(buf) < 4:
        raise IncompleteFrame("等待 4 字节长度前缀")

    (length,) = struct.unpack(">I", buf[:4])
    if length < _HEADER_LEN:
        raise ProtocolError(E_BAD_REQUEST, f"帧长度 {length} 小于最小帧头")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(
            E_FRAME_TOO_LARGE,
            f"帧长度 {length} 超过上限 {MAX_FRAME_BYTES} 字节",
        )
    if len(buf) < _HEADER_LEN:
        raise IncompleteFrame("等待 1 字节版本号")

    ver = buf[4]
    if ver != FRAME_VER:
        raise ProtocolError(E_BAD_REQUEST, f"不支持的帧版本 0x{ver:02x}，期望 0x01")

    if len(buf) < length:
        raise IncompleteFrame(f"等待帧体，已有 {len(buf)} / {length} 字节")

    body = buf[_HEADER_LEN:length]
    if not body:
        return {}, buf[length:]

    try:
        msg = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(E_BAD_REQUEST, f"BODY JSON 解析失败: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError(E_BAD_REQUEST, "BODY 必须是 JSON 对象")
    return msg, buf[length:]
