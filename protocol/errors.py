"""应用层错误码与异常定义（服务端/客户端共用，禁止在业务代码硬编码码值）。"""

E_ROOM_NOT_FOUND = 1001
E_AUTH_FAILED = 1002
E_ROOM_LOCKED = 1003
E_Q_NOT_LIVE = 1004
E_DEADLINE_PASSED = 1005
E_UNKNOWN_QID = 1006
E_INVALID_ANSWER = 1007
E_FRAME_TOO_LARGE = 1008
E_BAD_REQUEST = 1009
E_ROLE_FORBIDDEN = 1010

ERROR_NAMES = {
    E_ROOM_NOT_FOUND: "E_ROOM_NOT_FOUND",
    E_AUTH_FAILED: "E_AUTH_FAILED",
    E_ROOM_LOCKED: "E_ROOM_LOCKED",
    E_Q_NOT_LIVE: "E_Q_NOT_LIVE",
    E_DEADLINE_PASSED: "E_DEADLINE_PASSED",
    E_UNKNOWN_QID: "E_UNKNOWN_QID",
    E_INVALID_ANSWER: "E_INVALID_ANSWER",
    E_FRAME_TOO_LARGE: "E_FRAME_TOO_LARGE",
    E_BAD_REQUEST: "E_BAD_REQUEST",
    E_ROLE_FORBIDDEN: "E_ROLE_FORBIDDEN",
}

ERROR_TEXT = {
    E_ROOM_NOT_FOUND: "房间不存在",
    E_AUTH_FAILED: "会话凭证无效或未加入房间",
    E_ROOM_LOCKED: "房间已锁定（保留错误码，MVP 未启用）",
    E_Q_NOT_LIVE: "题目未开始或已结束",
    E_DEADLINE_PASSED: "已超过本题截止时间",
    E_UNKNOWN_QID: "题号不存在",
    E_INVALID_ANSWER: "答案非法：必须是本题选项之一（单选）",
    E_FRAME_TOO_LARGE: "帧长度超过 64 KB 上限",
    E_BAD_REQUEST: "请求格式不合法：JSON 解析失败或字段缺失/类型错误",
    E_ROLE_FORBIDDEN: "当前角色无权执行该操作",
}


class ProtocolError(Exception):
    """协议层业务异常；code 必须是 errors.py 中定义的 1001~1010 之一。"""

    def __init__(self, code: int, msg: str | None = None, ref_msg_id: str | None = None):
        self.code = code
        self.msg = msg or ERROR_TEXT.get(code, "未知协议错误")
        self.ref_msg_id = ref_msg_id
        name = ERROR_NAMES.get(code, "E_UNKNOWN")
        super().__init__(f"[{code} {name}] {self.msg}")


class IncompleteFrame(Exception):
    """缓冲区中的数据不足一个完整帧，调用方应继续 recv 后重试 decode。"""
