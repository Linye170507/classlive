"""消息构造与字段校验：15 类消息的字段契约（与 docs/protocol.md v1.0 一致）。"""

from __future__ import annotations

import time
import uuid

from .errors import E_BAD_REQUEST, ProtocolError

# 客户端 -> 服务端
JOIN = "JOIN"
SUBMIT = "SUBMIT"
RESYNC = "RESYNC"
PING = "PING"
Q_START = "Q_START"
Q_CLOSE = "Q_CLOSE"

# 服务端 -> 客户端
WELCOME = "WELCOME"
Q_STARTED = "Q_STARTED"
SUBMIT_ACK = "SUBMIT_ACK"
SUBMIT_DUP = "SUBMIT_DUP"
STATS = "STATS"
Q_CLOSED = "Q_CLOSED"
RESYNC_STATE = "RESYNC_STATE"
PONG = "PONG"
ERROR = "ERROR"

CLIENT_TO_SERVER = frozenset({JOIN, SUBMIT, RESYNC, PING, Q_START, Q_CLOSE})
SERVER_TO_CLIENT = frozenset(
    {WELCOME, Q_STARTED, SUBMIT_ACK, SUBMIT_DUP, STATS, Q_CLOSED, RESYNC_STATE, PONG, ERROR}
)
ALL_TYPES = CLIENT_TO_SERVER | SERVER_TO_CLIENT

ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
VALID_ROLES = frozenset({ROLE_STUDENT, ROLE_TEACHER})


def now_ts() -> int:
    """当前 UTC 毫秒时间戳（仅用于日志与展示）。"""
    return int(time.time() * 1000)


def make(msg_type: str, room_id: str, **fields) -> dict:
    """构造一条带通用字段的消息（msg_id 由调用方本地生成）。"""
    if msg_type not in ALL_TYPES:
        raise ProtocolError(E_BAD_REQUEST, f"未知消息类型 {msg_type!r}")
    msg = {
        "msg_id": uuid.uuid4().hex,
        "type": msg_type,
        "ts": now_ts(),
        "room_id": room_id,
    }
    msg.update(fields)
    return msg


def make_error(code: int, msg: str, room_id: str, ref_msg_id: str = "") -> dict:
    return make(ERROR, room_id, code=code, msg=msg, ref_msg_id=ref_msg_id)


def _need_str(msg: dict, field: str) -> str:
    value = msg.get(field)
    if not isinstance(value, str) or not value:
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是非空字符串")
    return value


def _need_plain_str(msg: dict, field: str, allow_empty: bool = False) -> str:
    value = msg.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是字符串")
    return value


def _need_int(msg: dict, field: str) -> int:
    value = msg.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是整数")
    return value


def _need_bool(msg: dict, field: str) -> bool:
    value = msg.get(field)
    if not isinstance(value, bool):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是布尔值")
    return value


def _need_dict(msg: dict, field: str) -> dict:
    value = msg.get(field)
    if not isinstance(value, dict):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是对象")
    return value


def _need_str_list(msg: dict, field: str) -> list[str]:
    value = msg.get(field)
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是字符串数组")
    return value


def _need_int_or_none(msg: dict, field: str) -> int | None:
    value = msg.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是整数或 null")
    return value


def _need_dict_or_none(msg: dict, field: str) -> dict | None:
    value = msg.get(field)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProtocolError(E_BAD_REQUEST, f"字段 {field} 必须是对象或 null")
    return value


def _validate_join(msg: dict) -> None:
    _need_str(msg, "student_id")
    _need_str(msg, "nickname")
    role = _need_str(msg, "role")
    if role not in VALID_ROLES:
        raise ProtocolError(E_BAD_REQUEST, "role 必须是 student 或 teacher")
    _need_str(msg, "client_ver")
    _need_str(msg, "token")


def _validate_submit(msg: dict) -> None:
    _need_str(msg, "qid")
    _need_str(msg, "answer")
    _need_str(msg, "nonce")
    _need_int(msg, "local_ts")


def _validate_resync(msg: dict) -> None:
    _need_plain_str(msg, "qid", allow_empty=True)


def _validate_qid_only(msg: dict) -> None:
    _need_str(msg, "qid")


def _validate_welcome(msg: dict) -> None:
    _need_str(msg, "session_id")
    _need_int(msg, "server_ts")
    _need_int(msg, "heartbeat_s")
    _need_str(msg, "room_state")


def _validate_q_started(msg: dict) -> None:
    _need_str(msg, "qid")
    _need_str(msg, "stem")
    _need_str_list(msg, "options")
    _need_int(msg, "deadline_ts")
    _need_int(msg, "server_ts")


def _validate_submit_ack(msg: dict) -> None:
    _need_str(msg, "qid")
    _need_bool(msg, "accepted")
    _need_int(msg, "server_seq")


def _validate_submit_dup(msg: dict) -> None:
    _need_str(msg, "qid")
    _need_int(msg, "first_submit_ts")


def _validate_stats(msg: dict) -> None:
    _need_plain_str(msg, "qid", allow_empty=True)
    _need_dict(msg, "counts")
    _need_int(msg, "total")
    _need_int(msg, "submitted")
    _need_int(msg, "online")


def _validate_q_closed(msg: dict) -> None:
    _need_str(msg, "qid")
    _need_dict(msg, "final_counts")
    _need_str(msg, "correct")


def _validate_resync_state(msg: dict) -> None:
    _need_str(msg, "room_state")
    _need_plain_str(msg, "qid", allow_empty=True)
    _need_int_or_none(msg, "deadline_ts")
    _need_dict_or_none(msg, "my_submission")


def _validate_pong(msg: dict) -> None:
    _need_int(msg, "server_ts")


def _validate_error(msg: dict) -> None:
    _need_int(msg, "code")
    _need_plain_str(msg, "msg", allow_empty=True)
    _need_plain_str(msg, "ref_msg_id", allow_empty=True)


_VALIDATORS = {
    JOIN: _validate_join,
    SUBMIT: _validate_submit,
    RESYNC: _validate_resync,
    PING: lambda msg: None,
    Q_START: _validate_qid_only,
    Q_CLOSE: _validate_qid_only,
    WELCOME: _validate_welcome,
    Q_STARTED: _validate_q_started,
    SUBMIT_ACK: _validate_submit_ack,
    SUBMIT_DUP: _validate_submit_dup,
    STATS: _validate_stats,
    Q_CLOSED: _validate_q_closed,
    RESYNC_STATE: _validate_resync_state,
    PONG: _validate_pong,
    ERROR: _validate_error,
}


def validate(msg: dict) -> None:
    """校验通用字段与消息类型字段；失败抛 ProtocolError(E_BAD_REQUEST)。"""
    if not isinstance(msg, dict):
        raise ProtocolError(E_BAD_REQUEST, "消息必须是 JSON 对象")

    _need_str(msg, "msg_id")
    msg_type = _need_str(msg, "type")
    _need_int(msg, "ts")
    _need_str(msg, "room_id")

    validator = _VALIDATORS.get(msg_type)
    if validator is None:
        raise ProtocolError(E_BAD_REQUEST, f"未知消息类型 {msg_type!r}")
    validator(msg)
