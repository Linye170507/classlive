# ClassLive 数据库说明（SQLite）

> 维护人：成员 B（业务与数据层）
> 代码实现：`server/store.py`
> 适用版本：第 1 周（W1）表结构 v1

---

## 1. 设计目标与边界

| 目标 | 落实方式 |
|------|----------|
| 提交记录可追溯 | 每次首次提交写入 `submissions` 一行 |
| 重复提交不重复计票 | `UNIQUE(room_id, qid, student_id)` + `INSERT OR IGNORE` |
| 不因并发写产生 `database is locked` | 全进程仅 **1 个 DB 写线程**，独占连接、串行执行 |
| 统计实时 | 计数以内存 `StatsAggregator` 为准，DB 不参与实时统计 |

**明确不做**（第 1 周）：服务端重启恢复、审计事件表、历史数据清理、多教室分库。重启恢复是实施方案 9.5 的冲刺项。

---

## 2. 文件与连接参数

| 项 | 值 |
|----|----|
| 数据库路径 | `config.json` → `storage.path`，默认 `data/classlive.db` |
| 路径解析 | 相对路径按项目根目录（`classlive/`）解析 |
| 日志模式 | `PRAGMA journal_mode=WAL`（产生 `-wal` / `-shm` 旁文件） |
| 同步级别 | `PRAGMA synchronous=NORMAL` |
| 锁等待 | `timeout=5.0` |
| 事务模式 | `isolation_level=None`，写语句后显式 `commit()` |

> WAL 允许「1 写多读」。本项目只有写线程访问连接，因此不存在多写者竞争。

---

## 3. 表结构

建表 SQL 常量：`server/store.py` 中的 `SCHEMA_SQL`，启动时由 DB 写线程执行 `executescript`（全部 `CREATE ... IF NOT EXISTS`，可重复执行）。

### 3.1 rooms

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `room_id` | TEXT | PRIMARY KEY | 房间标识，MVP 固定 `demo-room` |
| `title` | TEXT | NOT NULL | 房间标题 |
| `state` | TEXT | NOT NULL | `IDLE` / `ACTIVE` / `CLOSED` |
| `created_at` | INTEGER | NOT NULL | 创建时间，UTC 毫秒 |

> 第 1 周服务端不在运行期写入该表（房间状态由内存 `RoomManager` 维护）；表为后续恢复/展示预留。

### 3.2 questions

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `room_id` | TEXT | PK 组成 | 房间标识 |
| `qid` | TEXT | PK 组成 | 题号，如 `Q1` |
| `stem` | TEXT | NOT NULL | 题干 |
| `options_json` | TEXT | NOT NULL | 选项数组的 JSON 字符串 |
| `correct` | TEXT | NOT NULL | 正确答案 `A`~`D` |
| `duration_s` | INTEGER | NOT NULL | 答题时长（秒） |
| `state` | TEXT | NOT NULL | `DRAFT` / `LIVE` / `CLOSED` |
| `started_at` | INTEGER | 可空 | 发题时间（UTC 毫秒） |
| `closed_at` | INTEGER | 可空 | 收卷时间（UTC 毫秒） |

主键：`PRIMARY KEY (room_id, qid)`。

### 3.3 submissions（核心表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `room_id` | TEXT | NOT NULL，UNIQUE 组成 | 房间标识 |
| `qid` | TEXT | NOT NULL，UNIQUE 组成 | 题号 |
| `student_id` | TEXT | NOT NULL，UNIQUE 组成 | 学号 |
| `answer` | TEXT | NOT NULL | 提交的选项 |
| `nonce` | TEXT | NOT NULL | 客户端幂等随机串（日志追溯） |
| `submit_ts` | INTEGER | NOT NULL | 服务端接收时间（UTC 毫秒） |
| `seq` | INTEGER | NOT NULL | 服务端全局递增序号 |

**唯一索引**：`UNIQUE (room_id, qid, student_id)` —— 幂等的数据库级兜底。

**普通索引**：`idx_submissions_qid ON submissions (room_id, qid)` —— 按题统计与按题查重。

### 3.4 students

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `room_id` | TEXT | PK 组成 | 房间标识 |
| `student_id` | TEXT | PK 组成 | 学号 |
| `nickname` | TEXT | NOT NULL | 显示名 |
| `first_seen` | INTEGER | NOT NULL | 首次加入时间 |
| `last_seen` | INTEGER | NOT NULL | 最近一次活动时间 |

主键：`PRIMARY KEY (room_id, student_id)`。

> 第 1 周 `students` 表仅建表，不做运行期写入（在线状态由 `SessionRegistry` 维护）。

---

## 4. 写线程模型

```text
连接线程 / 消息处理线程
        │  (sql, params) + Event
        ▼
   queue.Queue
        │
        ▼
  db-writer 单线程  ── 独占 sqlite3.Connection ──→ classlive.db
        │
        └── 写结果写入 box，Event.set() 唤醒调用方
```

- 线程名：`db-writer`，daemon。
- 初始化：线程创建后先建连接、设 PRAGMA、执行建表；`_ready` 事件保证 `Store.__init__` 返回时表已存在。
- 每个写任务携带 `threading.Event` 与结果字典；`insert_submission` 同步等待（上限 5 s），保证「写库成功」先于「回 ACK」。
- 关闭：向队列投递 `None` 哨兵，join 线程，关闭连接。

---

## 5. 幂等写入语义

```sql
INSERT OR IGNORE INTO submissions
  (room_id, qid, student_id, answer, nonce, submit_ts, seq)
VALUES (?, ?, ?, ?, ?, ?, ?);
```

| 返回值 | 判定 | 服务端动作 |
|--------|------|-----------|
| `rowcount > 0` → `True` | 首次写入 | 更新内存计数 → 回 `SUBMIT_ACK` → 广播 `STATS` |
| `rowcount == 0` → `False` | 命中唯一索引，重复 | 查 `first_submit_ts` → 回 `SUBMIT_DUP`，**不更新计数** |

**顺序原则**：数据库先裁决，内存后计数。即使 `StatsAggregator` 的集合判断失效，唯一索引仍能兜住；反之不成立，所以 DB 在前。

---

## 6. 内存与数据库的一致性

| 数据 | 权威来源 | 落库时机 |
|------|----------|----------|
| 各选项票数 / 已提交人数 | 内存 `StatsAggregator` | 每次有效提交写 1 行 `submissions`，票数可由该题行数重算 |
| 在线人数 | `SessionRegistry` | 不落库 |
| 房间/题目状态 | `RoomManager` | 第 1 周不落库（恢复功能为冲刺项） |

对账方式：`SELECT COUNT(*) FROM submissions WHERE room_id=? AND qid=?` 应等于 `STATS.submitted`（`test_dedup.py` 覆盖）。

---

## 7. 常用查询

```sql
-- 某题总提交数
SELECT COUNT(*) FROM submissions WHERE room_id = 'demo-room' AND qid = 'Q1';

-- 某题各选项分布
SELECT answer, COUNT(*) FROM submissions
WHERE room_id = 'demo-room' AND qid = 'Q1' GROUP BY answer;

-- 某学生是否已提交
SELECT answer, submit_ts, seq FROM submissions
WHERE room_id = 'demo-room' AND qid = 'Q1' AND student_id = 'S001';

-- 检查是否有重复（应恒为 0 行）
SELECT room_id, qid, student_id, COUNT(*) c FROM submissions
GROUP BY room_id, qid, student_id HAVING c > 1;
```

---

## 8. 验证与重置

| 场景 | 操作 |
|------|------|
| 单元测试 | `python -m unittest discover -s tests -v`（`test_dedup.py` 覆盖重复插入与并发计数） |
| 清空演示数据 | 停止服务端后删除 `data/classlive.db`、`classlive.db-wal`、`classlive.db-shm` |
| 确认表存在 | `sqlite3 data/classlive.db ".tables"` 应输出 `questions rooms students submissions` |

---

## 9. 变更记录

| 版本 | 时间 | 变更 |
|------|------|------|
| v1 | 第 1 周（W1） | 建立 4 张表、`UNIQUE(room_id,qid,student_id)`、单写线程；不含 `events` 表与重启恢复 |
