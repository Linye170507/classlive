# ClassLive · 课堂实时答题系统

纯标准库（`socket` / `threading` / `sqlite3` / `tkinter` / `multiprocessing`）实现的课堂答题系统：老师发题，学生实时收到，服务端以内存计数与 SQLite 唯一索引保证「同一学号同一题只计一次」。

当前进度：**第 1 周（W1）** —— 链路打通、协议 v1.0 定稿、20 进程 JOIN。

---

## 1. 环境要求

| 项 | 要求 |
|----|------|
| Python | 3.10+（开发环境 3.14.5） |
| 第三方依赖 | **无**，见 `cache/requirements.txt` |
| 图形界面 | 需要桌面环境（Tkinter 随 Python 安装） |
| 操作系统 | Windows 10/11（演示环境）；Linux/macOS 可运行客户端与服务端 |

> Windows 无 bash 时，请使用 PowerShell 命令；`scripts/*.sh` 为跨平台交付物，Windows 下请用 `scripts/demo_windows.ps1`。

---

## 2. 快速开始（Windows PowerShell）

```powershell
cd classlive

# 终端 1：服务端（控制台标题会变成 ClassLive Server）
python -m server.main

# 终端 2：老师端大屏
python -m clients.teacher_app

# 终端 3：学生端
python -m clients.student_app

# 终端 4：20 个模拟学生进程，保持在线 30 秒
python -m clients.simulator --count 20 --hold-s 30
```

一键启动三窗口并平铺：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\demo_windows.ps1
# 同时拉起 20 个模拟学生：
powershell -ExecutionPolicy Bypass -File scripts\demo_windows.ps1 -Simulators 20
```

### Linux / macOS

```bash
cd classlive
scripts/run_server.sh
scripts/run_teacher.sh
scripts/run_student.sh
scripts/run_simulator.sh --count 20 --hold-s 30
scripts/demo_layout.sh     # 需要 xdotool
```

| 脚本 | 作用 |
|------|------|
| `scripts/run_server.sh` | 启动服务端 |
| `scripts/run_teacher.sh` | 启动老师端 |
| `scripts/run_student.sh` | 启动学生端 |
| `scripts/run_simulator.sh` | 启动模拟学生（默认 20 个） |
| `scripts/demo_layout.sh` | Linux/macOS 三窗口平铺 |
| `scripts/demo_layout.py` | Windows 三窗口平铺（按窗口标题移动） |
| `scripts/demo_windows.ps1` | Windows 一键启动 + 平铺 |

---

## 3. 目录结构

```text
classlive/
├── config.json              # 端口、心跳、题目、数据库路径
├── cache/requirements.txt   # 仅标准库声明
├── protocol/                # 帧编解码、消息构造/校验、错误码
├── server/                  # accept、连接线程、会话、房间、统计、SQLite、定时器
├── clients/                 # 老师端、学生端、共用网络层、多进程模拟器
├── demo/                    # 演示控制面板（W2/W3）
├── tests/                   # 帧编解码 / 状态机 / 幂等
├── docs/                    # protocol.md（A 维护）、schema.md（B 维护）
├── scripts/                 # 启动与窗口平铺脚本
└── data/                    # 运行期生成的 classlive.db（不提交）
```

---

## 4. 配置（`config.json`）

| 键 | 默认 | 说明 |
|----|------|------|
| `server.host` / `server.port` | `127.0.0.1` / `9000` | 监听地址与端口 |
| （帧上限） | 64 KB | **协议常量**，定义在 `protocol/framing.py:MAX_FRAME_BYTES`，不通过 `config.json` 配置 |
| `timing.heartbeat_interval_s` | `10` | 客户端 PING 间隔 |
| `timing.suspect_after_s` / `dead_after_s` | `30` / `60` | 连接 SUSPECT / DEAD 阈值 |
| `room.room_id` | `demo-room` | 房间标识（MVP 只跑一个房间） |
| `storage.path` | `data/classlive.db` | SQLite 路径（相对项目根目录） |
| `questions[]` | 3 道单选 | `qid/stem/options/correct/duration_s`，题目预置，不做在线建题 |

> 修改端口后，客户端也需要带同样的 `--port`（老师端/学生端/模拟器均支持）。

---

## 5. 测试

```powershell
cd classlive
python -m unittest discover -s tests -v
```

| 文件 | 覆盖 |
|------|------|
| `tests/test_framing.py` | 半包、粘包、空 body、恰好 64KB、超限、非法 JSON、版本不符 |
| `tests/test_state_machine.py` | 未发题提交、重复 START、重复收卷、已收卷再提交、未知题号、越界选项、超时提交、角色越权、广播与房间回退、**20 线程并发「收卷 vs 提交」不变量** |
| `tests/test_dedup.py` | 同人同题多次只计 1、同人不同题独立、50 线程并发计数精确、唯一索引存在 |
| `tests/test_registry.py` | 干净断开保留会话、同 token 认领 `session_id`、存活时换 token 拒绝、DEAD 后换 token 接管、60 s 保留期到期移除、SUSPECT/DEAD 扫描与唤醒 |

集成验收（自动）：

```powershell
python -m server.main                # 终端 1
python -m clients.simulator --count 20 --hold-s 10   # 终端 2，期望退出码 0 且输出 JOIN 成功: 20/20
```

---

## 6. 第 1 周范围与验收

### 已完成（W1）

- A：帧编解码 + 单测、每连接一线程、会话注册与心跳扫描、服务端权威定时收卷、`docs/protocol.md` v1.0。
- B：四张表 + 唯一索引、单 DB 写线程、`insert_submission` 幂等、纯内存 `StatsAggregator`、房间/题目状态机、`docs/schema.md`。
- C：`net_client`（心跳 + 退避重连）、老师端大屏骨架、学生端只读窗口、20 进程模拟器、启动脚本与窗口平铺。

### M0（周三）验收方式

| 项 | 操作 | 期望 |
|----|------|------|
| 三进程 JOIN/WELCOME | 启动服务端 → 老师端 → 学生端 | 两端显示「已连接」；老师端在线人数 `0 → 1` |
| 15 类消息 + 错误码冻结 | 审阅 `docs/protocol.md` | 字段表与 `protocol/messages.py` 一致 |

> 实测（2026-11-01，Windows / Python 3.14）：三个 Tk 窗口（1180×780 / 860×720）正常创建，老师端启动后自动 JOIN，服务端日志出现 `JOIN student_id=T01 role=teacher`。

### M1（周五）验收方式

| 项 | 操作 | 期望 |
|----|------|------|
| 5 学生稳定在线 | 开 5 个学生端/模拟器 | 老师端在线人数稳定为 5 |
| 断开后回落 | 关闭学生端 | 老师端在线人数下降（服务端识别断开后立即刷新，最迟 60 s 由 DEAD 扫描兜底） |
| 发题广播 | 老师端点「发题」 | 全部学生端显示题干与倒计时 |
| 20 进程 JOIN | `python -m clients.simulator --count 20` | 输出 `JOIN 成功: 20/20`，退出码 0 |

> 实测（2026-11-01，Windows / Python 3.14，本机回环）：`clients.simulator --count 20 --hold-s 8` 输出 `JOIN 成功: 20/20（用时 8.9s）`、退出码 0；同一轮串联验证了在线人数 `0 → 2`、发题广播到全部学生、中途加入老师补发 `Q_STARTED`、首答 `SUBMIT_ACK` / 重答 `SUBMIT_DUP`、`Q_CLOSED` 最终计数、关闭一端后在线人数回落、SQLite 只落 2 条记录（重复提交未入库）。单元测试 36 项全部通过。

---

## 7. 已知问题（W1 结束时）

1. **学生端只读**：第 1 周学生端只展示题干/选项与正确答案，不能提交；答题与提交状态提示在 W2 实现。
2. **柱状图占位**：老师端 Canvas 仅显示「实时柱状图·占位区」，A~D 以大字号数字面板展示；Canvas 手绘柱状图在 W2。
3. **控制面板未实现**：`demo/control_panel.py` 属 W2/W3，当前只有 CLI 模拟器。
4. **pending 持久化未实现**：`NetClient` 只做心跳与重连，`pending.json` 补交在 W3。
5. **房间状态回退**：收卷后若仍有 `DRAFT` 题，房间回 `IDLE` 而不是 `CLOSED`，以支持连续发题；与实施方案 3.6 图的差异已记录在 `docs/protocol.md` 变更日志 R1。
6. **服务端重启不恢复**：重启后房间/题状态从 `config.json` 重新开始，提交记录仍在 SQLite 中；恢复逻辑是冲刺项。
7. **在线人数口径**：`SUSPECT`（30~60 s 无消息）仍计入在线，`DEAD` 才剔除；断网但不关进程时，数字要到 60 s 才回落。
8. **Windows 无 bash**：`.sh` 脚本用于 Linux/macOS；Windows 一键启动请用 `scripts/demo_windows.ps1`。
9. **会话保留期 60 s（2026-11-01 修复）**：干净断开后会话不再立即删除，而是保留 `dead_after_s`（60 s）供同 token 认领；`online` 与 `STATS` 不受影响，人数仍即时回落。学生进程重启（换了新 token）时，旧会话只要已 `DEAD` 就允许接管重建（`session_id` 变化）。详见 `docs/protocol.md` 变更日志 R5。
10. **提交判定已原子化（2026-11-01 修复）**：`Q_CLOSE` 与 `SUBMIT` 在同一把房间锁上串行，收卷后不可能再出现「被接受的提交」。详见 R6。

---

## 8. 协议与文档

- 协议唯一契约：[docs/protocol.md](docs/protocol.md)（v1.0，帧格式、15 类消息、错误码 1001~1010、四个状态机、幂等与重连、冻结接口签名）。
- 数据库说明：[docs/schema.md](docs/schema.md)（表结构、唯一索引、单写线程模型、对账查询）。
- 实施方案：[../ClassLive_实施方案.md](../ClassLive_实施方案.md)。

---

## 9. 常见问题

| 现象 | 处理 |
|------|------|
| 服务端启动报 `WinError 10048` | 端口 9000 被占用：换 `config.json` 的端口，或结束旧进程 |
| 客户端一直显示「连接失败」 | 确认服务端已启动、端口一致；防火墙允许本机 9000 |
| 模拟器输出 `JOIN 成功: 0/20` | 服务端未启动或 `room_id` 不一致 |
| 老师端在线人数为 0 | 学生/模拟器尚未 JOIN，或 `room_id` 不一致 |
| 想清空演示数据 | 停止服务端后删除 `data/classlive.db*` |
