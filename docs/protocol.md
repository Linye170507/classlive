# ClassLive 应用层协议（v1.0）

> 维护人：成员 A（协议与连接层）
> 状态：**v1.0 定稿，M0（第 1 周周三）会议确认后正式冻结**
> 适用范围：`protocol/`、`server/`、`clients/`、`demo/` 全部模块。代码与本文档不一致时，以本文档为准并立即修代码。
> 实现对照：`protocol/framing.py`、`protocol/messages.py`、`protocol/errors.py`

---

## 1. 帧格式

```text
 0        1        2        3        4        5                    N
+--------+--------+--------+--------+--------+--------------------+
|        LEN (4 bytes, big-endian)    | VER 1B |  BODY (UTF-8 JSON) |
+--------+--------+--------+--------+--------+--------------------+
```

| 项 | 规则 |
|----|------|
| `LEN` | 4 字节无符号大端，值 = `1 + len(body_bytes)`，**包含 VER** |
| `VER` | 1 字节，固定 `0x01` |
| `BODY` | UTF-8 编码的 JSON 对象（服务端/客户端都必须收到 JSON object） |
| 最大帧 | `LEN <= 65536`（64 KB）；超过即 `E_FRAME_TOO_LARGE(1008)` |
| 最小帧 | `LEN >= 5`（VER 至少 1 字节，body 可为空） |
| 空 body | 允许，解析为 `{}`；随后由 `messages.validate` 判定字段缺失 |
| 未知 VER | 视为协议错误，回 `E_BAD_REQUEST(1009)` 后断开 |

**解析约定**（`framing.decode`）：

1. 输入是「已收到的全部字节」，返回 `(msg, rest)`；`rest` 是尚未消费的尾部字节，调用方负责继续拼接。
2. 数据不足一个完整帧 → 抛 `IncompleteFrame`，调用方继续 `recv`，**不得**当作错误断开。
3. 长度非法 / 版本不符 / JSON 解析失败 / body 不是对象 → 抛 `ProtocolError`，调用方回 `ERROR` 并关闭连接。
4. 只解析到 `LEN` 就能判定超限：一旦 `LEN > 65536` 立即抛 `1008`，不等待超大 body 传输完成。
5. 必须支持：粘包（一次 `recv` 多个帧）、半包（一个帧跨多次 `recv`）、空 body、恰好 65536 字节。

---

## 2. 通用字段（所有 BODY 必带）

| 字段 | 类型 | 说明 |
|------|------|------|
| `msg_id` | string | 发送方生成，`uuid4().hex`；服务端回包用 `ref_msg_id` 引用它 |
| `type` | string | 消息类型，见第 3 节；必须在 15 类之内 |
| `ts` | int | 发送方 UTC 毫秒时间戳，**仅用于日志，不参与业务判定** |
| `room_id` | string | 房间标识，MVP 固定 `"demo-room"`；不匹配回 `1001` |

命名约定：字段名一律小写下划线；布尔值必须是 JSON `true/false`，不得用 0/1；整数不得用字符串。

---

## 3. 消息清单（15 类）

### 3.1 客户端 → 服务端（6 类）

#### JOIN
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `student_id` | string | 是 | 学生学号；老师端使用固定工号（示例 `T01`） |
| `nickname` | string | 是 | 显示名，非空 |
| `role` | string | 是 | `student` 或 `teacher` |
| `client_ver` | string | 是 | 客户端版本标识，如 `classlive-1.0` |
| `token` | string | 是 | 会话凭证；重连必须携带同一 token 才能认领旧会话 |

> 同一 `student_id` 已在线：token 一致则**认领旧会话并关闭旧连接**；token 不一致且旧会话仍 `OPEN/SUSPECT` 回 `1002`。
> 例外（变更日志 R5）：旧会话已 `DEAD`（连接确认断开，如学生重启客户端拿到新 token）时，允许以新 token **接管重建**会话，`session_id` 随之更新。

#### SUBMIT
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `qid` | string | 是 | 题号，必须是 `config.json` 中存在的题 |
| `answer` | string | 是 | 单选答案，取值 `A`~`D`（以本题选项数为准） |
| `nonce` | string | 是 | 客户端生成的幂等随机串，用于日志追溯 |
| `local_ts` | int | 是 | 客户端本地提交时间（毫秒），仅日志用 |

#### RESYNC
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `qid` | string | 是 | 想同步的题号；允许空串，表示「当前正在进行/最近一题」 |

#### PING
无业务字段（仅通用字段）。建议每 10 秒一次。

#### Q_START
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `qid` | string | 是 | 要开始的题；题目内容由 `config.json` 预置，不实现在线建题 |

仅老师可用；学生发送回 `1010`。

#### Q_CLOSE
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `qid` | string | 是 | 要收卷的题；必须处于 `LIVE` |

仅老师可用；重复收卷或非 LIVE 回 `1004`。

### 3.2 服务端 → 客户端（9 类）

#### WELCOME
| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 ID（uuid hex），重连后保持不变 |
| `server_ts` | int | 服务端 UTC 毫秒 |
| `heartbeat_s` | int | 客户端 PING 间隔建议值（当前 10） |
| `room_state` | string | `IDLE` / `ACTIVE` / `CLOSED` |

#### Q_STARTED
| 字段 | 类型 | 说明 |
|------|------|------|
| `qid` | string | 题号 |
| `stem` | string | 题干（纯文本） |
| `options` | string[] | 选项文本，顺序对应 A、B、C、D |
| `deadline_ts` | int | **服务端**计算的截止时间（UTC 毫秒） |
| `server_ts` | int | 本条消息发出时的服务端时间 |

> 客户端倒计时 = `deadline_ts - server_ts`，收到后用本地单调时钟递减；不参与「是否超时」的判定。

#### SUBMIT_ACK
| 字段 | 类型 | 说明 |
|------|------|------|
| `qid` | string | 题号 |
| `accepted` | bool | 固定 `true`（成功路径） |
| `server_seq` | int | 服务端全局递增序号，用于日志排序 |

#### SUBMIT_DUP
| 字段 | 类型 | 说明 |
|------|------|------|
| `qid` | string | 题号 |
| `first_submit_ts` | int | 首次成功提交的服务端时间（毫秒） |

> **重复提交是正常业务语义，不是错误**：不回 `ERROR`、不回 `1004`。

#### STATS
| 字段 | 类型 | 说明 |
|------|------|------|
| `qid` | string | 题号；**允许空串**，表示房间尚无题目 |
| `counts` | object | `{"A": n, "B": n, "C": n, "D": n}`，缺省选项也带 0 |
| `total` | int | 各选项票数之和 |
| `submitted` | int | 已提交人数（按 `student_id` 去重） |
| `online` | int | 当前在线学生数（不含老师、不含 `DEAD` 会话） |

> 第 1 周没有独立的 `ONLINE` 消息，在线人数变化复用 `STATS`，只推送给老师端。

#### Q_CLOSED
| 字段 | 类型 | 说明 |
|------|------|------|
| `qid` | string | 题号 |
| `final_counts` | object | 冻结后的最终票数 |
| `correct` | string | 正确答案（`A`~`D`） |

#### RESYNC_STATE
| 字段 | 类型 | 说明 |
|------|------|------|
| `room_state` | string | `IDLE` / `ACTIVE` / `CLOSED` |
| `qid` | string | 当前或最近一题；允许空串 |
| `deadline_ts` | int \| null | 当前 LIVE 题的截止时间；无 LIVE 题时为 `null` |
| `my_submission` | object \| null | `{answer, submit_ts, seq}`；未提交为 `null` |

#### PONG
| 字段 | 类型 | 说明 |
|------|------|------|
| `server_ts` | int | 服务端 UTC 毫秒 |

#### ERROR
| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | 1001~1010，见第 4 节 |
| `msg` | string | 可读错误信息（中文） |
| `ref_msg_id` | string | 触发错误的客户端消息 `msg_id`；无法解析时为空串 |

---

## 4. 错误码

| 码 | 名称 | 触发场景 |
|----|------|----------|
| 1001 | `E_ROOM_NOT_FOUND` | `room_id` 与服务端不符 |
| 1002 | `E_AUTH_FAILED` | token 不匹配；未 JOIN 就发业务消息 |
| 1003 | `E_ROOM_LOCKED` | 保留位，MVP 不启用 |
| 1004 | `E_Q_NOT_LIVE` | 题目未开始 / 已结束 / 重复发题 / 重复收卷 |
| 1005 | `E_DEADLINE_PASSED` | 超过 `deadline_ts` 仍提交 |
| 1006 | `E_UNKNOWN_QID` | 题号不存在 |
| 1007 | `E_INVALID_ANSWER` | 答案不在本题选项范围内 |
| 1008 | `E_FRAME_TOO_LARGE` | 帧长度 > 64 KB |
| 1009 | `E_BAD_REQUEST` | JSON 非法、字段缺失/类型错误、未知消息类型 |
| 1010 | `E_ROLE_FORBIDDEN` | 学生发 `Q_START` / `Q_CLOSE`，老师发 `SUBMIT` |

规则：

- 错误码常量唯一定义在 `protocol/errors.py`，业务代码禁止硬编码数字。
- `ERROR` 只作为对**某条请求**的应答，必须带 `ref_msg_id`。
- `SUBMIT_DUP` 不属于错误码体系。

---

## 5. 四个状态机

### 5.1 房间（RoomManager）

```text
IDLE ──(老师 Q_START)──→ ACTIVE ──(Q_CLOSE / 截止收卷)──→ 有 DRAFT 题 ? IDLE : CLOSED
```

> 与实施方案 3.6 的差异见第 11 节变更日志 R1。

### 5.2 题目（每个 qid 一个实例）

```text
DRAFT ──(Q_START)──→ LIVE ──(Q_CLOSE 或 deadline 到达)──→ CLOSED
```

- 同时只能有一道 `LIVE` 题；重复 `Q_START` 回 `1004`。
- 收卷时冻结计数 → 广播 `Q_CLOSED` → 数据库已有记录（不额外写状态）。

### 5.3 学生（会话维度）

```text
NEW ──(JOIN)──→ JOINED ──(收到 Q_STARTED)──→ ANSWERING ──(SUBMIT_ACK)──→ ANSWERED
                    ▲                                                        │
                    └────────────(重连认领 JOIN)── RECONNECTED ←─────────────┘
```

- 重连后 `RESYNC_STATE.my_submission != null` 表示已计过票，**不得再提交**。
- 第 1 周客户端只展示题目，不下发 `SUBMIT`。

### 5.4 连接（每条 socket）

```text
CONNECTING ──(JOIN/WELCOME)──→ OPEN ──(30s 无任何消息)──→ SUSPECT ──(60s)──→ DEAD
```

- `SUSPECT` 只是标记，会话仍计入 `online`（第 1 周口径：`DEAD` 才剔除）。
- `DEAD` 时服务端**立即关闭 socket 并广播 `STATS`**（在线人数马上回落），但**会话保留 `dead_after_s`（60 s）**后才由心跳扫描真正移除。
  - 保留期内存量的是「会话凭证」，不是「在线状态」：`online` 与广播都不再包含它。
  - 保留期的唯一用途是让同号同 token 的重新 `JOIN` 能认领旧 `session_id`，见 §7 与变更日志 R5。
- 收到任意合法消息都会刷新 `last_seen`，并把 `SUSPECT` 恢复为 `OPEN`。
- 干净断开（关窗口 / 进程被杀 / `shutdown`）与心跳超时走同一套处理：只标记 `DEAD` + 起算保留期，不删除会话。

---

## 6. 幂等与去重

三层防护，任何一层单独失效都不会重复计票：

1. **客户端**：`pending.json` 记录未确认提交（第 3 周实现，当前未启用）。
2. **服务端内存**：`StatsAggregator` 持锁维护 `(qid, student_id)` 集合。
3. **数据库**：`UNIQUE(room_id, qid, student_id)` + `INSERT OR IGNORE`，`rowcount == 0` 即重复。

服务端处理 `SUBMIT` 的顺序（**数据库先行**）：

```text
校验 qid LIVE / deadline / answer
  → store.insert_submission(...)          # 唯一索引是最终裁决
  → False：回 SUBMIT_DUP(first_submit_ts)，不更新内存计数
  → True ：stats.on_submit(...)，回 SUBMIT_ACK，广播 STATS
```

> **判定与落库必须原子**：`RoomManager.submit_window(qid, now_ms)` 在房间锁内完成「LIVE + 未过 deadline」判定
> 并保持锁直到 `insert_submission` 返回；收卷（`Q_CLOSE` 与到点收卷）在同一把锁上改写题目状态。
> 于是二者严格串行——要么收卷先发生（提交回 `1004`/`1005`），要么提交先完成（收卷的最终计数已包含它），
> 不存在「题目已 `CLOSED` 却按陈旧 `LIVE` 接受提交」的窗口（变更日志 R6）。

---

## 7. 心跳与断线重连

| 项 | 值 |
|----|----|
| 客户端 PING 间隔 | 10 s |
| 服务端 PONG | 立即回，不带业务数据 |
| SUSPECT 阈值 | 30 s |
| DEAD 阈值 | 60 s |
| 客户端重连退避 | 1 / 2 / 4 / 4 / 4 s，最多 5 次 |

重连流程：

```text
recv 异常 / 心跳超时
  → 退避重连
  → JOIN(student_id, token)        # 同号同 token 认领旧 session_id
  → RESYNC(qid="")
  → RESYNC_STATE(room_state, qid, deadline_ts, my_submission)
  → 若 my_submission == null 且题目仍 LIVE：补交（第 3 周）
```

- 干净断开（关窗口 / 进程被杀）与网络分区走同一套保留期：会话 `DEAD` 后仍保留 60 s，期间同 token 的 `JOIN` 认领旧 `session_id` 并置 `RECONNECTED`（变更日志 R5）。客户端的断线补交（第 3 周）因此不依赖「TCP 恰好未探测到断开」这种偶然条件。
- 保留期到期（`scan()`）后会话才真正移除；此后同号 `JOIN` 只能新建会话、`session_id` 变化，`RESYNC_STATE.my_submission` 仍可据数据库判定是否已计票。
- 服务端不依赖老师在线：老师掉线不影响学生答题，老师重连后继续收到 `STATS`。
- 时间权威：`deadline_ts` 只由服务端产生，客户端不得本地判定超时。

---

## 8. 广播与应答规则

| 消息 | 接收方 |
|------|--------|
| `WELCOME` / `PONG` / `ERROR` / `SUBMIT_ACK` / `SUBMIT_DUP` | 仅请求方 |
| `Q_STARTED` | 全部学生 + 全部老师；**中途加入者也会补发当前 LIVE 题** |
| `STATS` | 仅老师端 |
| `Q_CLOSED` | 全部学生 + 全部老师 |

`STATS` 推送时机：有效提交后、在线人数变化（加入/断开/DEAD）、发题、收卷。

---

## 9. 冻结接口签名

### protocol

```python
framing.encode(msg: dict) -> bytes
framing.decode(buf: bytes) -> tuple[dict, bytes]
messages.make(msg_type: str, room_id: str, **fields) -> dict
messages.validate(msg: dict) -> None            # 失败抛 ProtocolError
errors.ProtocolError(code: int, msg: str | None = None, ref_msg_id: str | None = None)
errors.IncompleteFrame
```

### server

```python
Connection.send(msg: dict) -> bool
Connection.recv_loop(on_msg) -> None
SessionRegistry.bind(student_id, token, conn, *, nickname="", role="student") -> str
SessionRegistry.rebind(student_id, token, conn) -> bool
SessionRegistry.online_count(role="student") -> int
SessionRegistry.remove_conn(conn) -> Session | None   # 只标记 DEAD 并保留会话，不删除
SessionRegistry.scan(now=None) -> tuple[list[Session], list[Session], list[Session]]  # (SUSPECT, DEAD, 保留期到期移除)
RoomManager.submit_window(qid, now_ms) -> contextmanager  # 锁内判定 LIVE/deadline，锁保持到 with 结束
RoomManager.start_question(qid) -> Question
RoomManager.close_question(qid) -> Question
RoomManager.snapshot() -> dict
StatsAggregator.on_submit(qid, student_id, answer) -> tuple[bool, dict]
StatsAggregator.snapshot(qid) -> dict
Store.insert_submission(room_id, qid, student_id, answer, nonce, submit_ts, seq) -> bool
TimerScheduler.schedule_at(ts_ms, callback, key="") -> None
TimerScheduler.cancel(key) -> None
```

> 实施方案 5.2 中的 `RoomManager.join(...)` 在实现中不存在：加入房间是会话层职责，由 `SessionRegistry.bind` 处理；房间本身由服务端启动时创建。此处按实现冻结为**无 join 方法**。

### clients

```python
NetClient.connect() -> bool
NetClient.join(student_id, nickname, role, token=None) -> bool
NetClient.send(msg: dict) -> bool
NetClient.on_message(callback) -> None
NetClient.on_state(callback) -> None
NetClient.start_heartbeat() -> None
NetClient.close() -> None
```

> `PendingStore` 属第 3 周交付，不在 v1.0 冻结范围。

---

## 10. 消息样例

JOIN：

```json
{"msg_id":"6f1c...","type":"JOIN","ts":1793500000000,"room_id":"demo-room",
 "student_id":"S001","nickname":"张三","role":"student",
 "client_ver":"classlive-1.0","token":"9a2b..."}
```

WELCOME：

```json
{"msg_id":"...","type":"WELCOME","ts":1793500000123,"room_id":"demo-room",
 "session_id":"d4e5...","server_ts":1793500000123,"heartbeat_s":10,"room_state":"IDLE"}
```

STATS（无题目时的在线刷新）：

```json
{"msg_id":"...","type":"STATS","ts":1793500000456,"room_id":"demo-room",
 "qid":"","counts":{"A":0,"B":0,"C":0,"D":0},"total":0,"submitted":0,"online":3}
```

---

## 11. 变更日志

| 版本 | 时间 | 变更 | 说明 |
|------|------|------|------|
| v0.9 | 第 1 周周一 | 第 3 章原文落进本文档 | 作为实现草案 |
| v1.0-rc | 2026-11-01 | 逐条核对 15 类消息字段与代码实现 | 加入 R1~R4 澄清，字段名/类型未改 |
| v1.0 | 第 1 周周三 M0 | 三人会议拍板，正式冻结 | 冻结后字段只增不改 |
| v1.1 | 2026-11-01 | 会话保留期与提交原子性两处语义澄清 | 新增 R5、R6；**字段与错误码不变**，客户端无需改动 |

**R1 房间状态回退（实现层扩展）**
实施方案 3.6 的房间图只有 `IDLE → ACTIVE → CLOSED`，但 8.3 演示需要连续发 Q1、Q2。实现约定：收卷后若仍有 `DRAFT` 题，房间回到 `IDLE`，否则才进入 `CLOSED`；题目状态机不变。房间状态对客户端只用于展示，不参与业务判定。

**R2 STATS 允许空 qid**
房间尚无题目时仍需刷新在线人数，因此 `STATS.qid` 允许空串，此时 `counts` 全 0、`total/submitted` 为 0；`online` 始终有效。

**R3 可为 null 的字段**
`RESYNC_STATE.deadline_ts` 与 `RESYNC_STATE.my_submission` 允许 `null`（当前无 LIVE 题 / 尚未提交）。其余字段一律不得为 null。

**R4 JOIN 字段补充说明**
`client_ver` 为必填字段（实施方案 3.3 的 `role(client_ver)` 写法易被误读）；`ERROR.ref_msg_id` 允许空串（服务端无法解析请求 `msg_id` 时）。

**R5 会话保留期：干净断开也能认领（实现修正）**
原先 `SessionRegistry.remove_conn` 在 TCP 干净断开时立即删除会话，导致「同号同 token 认领旧 `session_id`」只在网络分区（TCP 尚未探测到断开）时成立——学生关窗口/进程被杀后重开就无法复用会话，直接冲击第 3 周「17→20 断线补交」。
修正为：`remove_conn` 只标记 `DEAD` 并起算保留期，会话在注册表内保留 `dead_after_s`（60 s），到期由 `scan()` 移除；`online` 与 `STATS` 从一开始就不含 `DEAD`，人数回落速度不变。另补充：旧会话 `DEAD` 期间换 token 的 `JOIN` 视为接管重建（新 `session_id`），避免学生重启客户端（新 token）被保留期挡在门外；旧会话仍 `OPEN/SUSPECT` 时换 token 依旧回 `1002`。

**R6 提交判定与落库原子化（实现修正）**
原先 `_on_submit` 在未持房间锁的情况下读取题目状态与 `deadline_ts`，与锁内执行的收卷存在极小窗口：题目已 `CLOSED` 却可能按陈旧 `LIVE` 接受提交并回 `SUBMIT_ACK`。修正为 `RoomManager.submit_window()`：在房间锁内完成判定并保持锁直到落库返回，与收卷严格串行。协议语义不变（`CLOSED` 后提交仍回 `1004`，过期回 `1005`），只是让这条保证在任何并发下都成立。

---

## 12. 冻结后的修改流程

1. 只能新增**可选字段**（客户端可忽略），且必须在当天站会通告。
2. 禁止改已有字段名、类型、必填性与错误码语义。
3. 任何变更先改本文档「变更日志」，再改代码；代码合并前需至少一人按本文档逐字段复核。
