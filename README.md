# ClassLive · 课堂实时答题系统

纯标准库（`socket` / `threading` / `sqlite3` / `tkinter` / `multiprocessing`）实现的课堂答题系统：老师发题，学生实时收到，服务端以内存计数与 SQLite 唯一索引保证「同一学号同一题只计一次」。

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

## 5. 协议与文档

- 协议唯一契约：[docs/protocol.md](docs/protocol.md)（v1.0，帧格式、15 类消息、错误码 1001~1010、四个状态机、幂等与重连、冻结接口签名）。
- 数据库说明：[docs/schema.md](docs/schema.md)（表结构、唯一索引、单写线程模型、对账查询）。
- 实施方案：[../ClassLive_实施方案.md](../ClassLive_实施方案.md)。

---

## 6. 常见问题

| 现象 | 处理 |
|------|------|
| 服务端启动报 `WinError 10048` | 端口 9000 被占用：换 `config.json` 的端口，或结束旧进程 |
| 客户端一直显示「连接失败」 | 确认服务端已启动、端口一致；防火墙允许本机 9000 |
| 模拟器输出 `JOIN 成功: 0/20` | 服务端未启动或 `room_id` 不一致 |
| 老师端在线人数为 0 | 学生/模拟器尚未 JOIN，或 `room_id` 不一致 |
| 想清空演示数据 | 停止服务端后删除 `data/classlive.db*` |
