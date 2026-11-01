"""多进程模拟学生：每个进程一条独立 TCP 连接、独立 JOIN、独立心跳。

第一周（W1）目标：20 个进程能同时 JOIN 成功（不要求提交）。
用法：
    python -m clients.simulator --count 20 --hold-s 30
    python -m clients.simulator --count 5 --hold-s 600 --verbose

说明：
- Windows 的 multiprocessing 使用 spawn，子进程会重新导入本模块，因此入口有 __main__ 保护；
- 父进程用 mp.Event 做启动栅栏，让 20 个子进程尽量同时发出 JOIN；
- 子进程把 JOIN 结果放进 mp.Queue，父进程汇总后以退出码表示成功与否（0=全部成功）。
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import queue
import socket
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"
RECV_CHUNK = 4096

if __package__ in (None, ""):  # 允许 python clients/simulator.py 直接运行
    sys.path.insert(0, str(PROJECT_ROOT))

from protocol import framing, messages  # noqa: E402
from protocol.errors import IncompleteFrame, ProtocolError  # noqa: E402


def _close(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def student_process(
    index: int,
    host: str,
    port: int,
    room_id: str,
    heartbeat_s: float,
    hold_s: float,
    start_event,
    result_queue,
    verbose: bool = False,
) -> None:
    """子进程主体：JOIN → 保持在线（定时 PING）→ 到时退出。"""
    student_id = f"sim{index:02d}"
    token = uuid.uuid4().hex
    start_event.wait(timeout=10.0)

    try:
        sock = socket.create_connection((host, port), timeout=3.0)
        sock.settimeout(0.5)
    except OSError as exc:
        result_queue.put((index, False, f"连接失败: {exc}"))
        return

    joined = False
    reported = False

    def report(ok: bool, detail: str) -> None:
        nonlocal reported
        if reported:
            return
        reported = True
        result_queue.put((index, ok, detail))

    def send(msg: dict) -> None:
        sock.sendall(framing.encode(msg))

    def handle(msg: dict) -> None:
        nonlocal joined
        msg_type = msg.get("type")
        if msg_type == messages.WELCOME:
            if not joined:
                joined = True
                session_id = str(msg.get("session_id", ""))
                report(True, session_id)
                print(f"[{student_id}] JOIN ok session={session_id[:8]}", flush=True)
        elif msg_type == messages.Q_STARTED:
            if verbose:
                print(f"[{student_id}] Q_STARTED qid={msg.get('qid')}", flush=True)
        elif msg_type == messages.ERROR:
            print(
                f"[{student_id}] ERROR code={msg.get('code')} msg={msg.get('msg')}",
                flush=True,
            )

    try:
        send(
            messages.make(
                messages.JOIN,
                room_id,
                student_id=student_id,
                nickname=f"模拟学生{index:02d}",
                role=messages.ROLE_STUDENT,
                client_ver="classlive-sim-1.0",
                token=token,
            )
        )
        now = time.monotonic()
        stop_at = now + hold_s
        join_deadline = now + 5.0
        next_ping = now + heartbeat_s
        buffer = b""

        while time.monotonic() < stop_at:
            try:
                chunk = sock.recv(RECV_CHUNK)
                if not chunk:
                    # 服务端关闭了连接（recv 返回空字节，与超时必须区分）
                    report(joined, "服务端关闭连接" if not joined else "")
                    return
            except socket.timeout:
                chunk = b""
            except OSError as exc:
                report(joined, f"接收失败: {exc}" if not joined else "")
                return

            if chunk:
                buffer += chunk
                while True:
                    try:
                        msg, buffer = framing.decode(buffer)
                    except IncompleteFrame:
                        break
                    except ProtocolError as exc:
                        print(f"[{student_id}] 协议错误: {exc}", flush=True)
                        report(False, f"协议错误: {exc}")
                        return
                    handle(msg)

            now = time.monotonic()
            if not joined and now >= join_deadline:
                report(False, "5 秒内未收到 WELCOME")
                return
            if now >= next_ping:
                try:
                    send(messages.make(messages.PING, room_id))
                except OSError:
                    return
                next_ping = now + heartbeat_s

        report(joined, "未成功 JOIN" if not joined else "")
    finally:
        _close(sock)
        if verbose:
            print(f"[{student_id}] 退出", flush=True)


def _load_defaults(config_path: Path) -> tuple[str, int, str]:
    with open(config_path, encoding="utf-8") as fp:
        config = json.load(fp)
    server = config.get("server", {})
    room = config.get("room", {})
    return (
        server.get("host", "127.0.0.1"),
        int(server.get("port", 9000)),
        room.get("room_id", "demo-room"),
    )


def main(argv: list[str] | None = None) -> int:
    default_host, default_port, default_room = _load_defaults(DEFAULT_CONFIG)

    parser = argparse.ArgumentParser(description="ClassLive 多进程模拟学生")
    parser.add_argument("--host", default=default_host, help="服务端地址")
    parser.add_argument("--port", type=int, default=default_port, help="服务端端口")
    parser.add_argument("--room", default=default_room, help="房间标识")
    parser.add_argument("--count", type=int, default=20, help="模拟学生进程数（默认 20）")
    parser.add_argument("--hold-s", type=float, default=30.0, help="每个进程保持在线的秒数")
    parser.add_argument("--heartbeat-s", type=float, default=10.0, help="PING 间隔（秒）")
    parser.add_argument("--verbose", action="store_true", help="打印每个进程收到的事件")
    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error("--count 必须 >= 1")

    ctx = mp.get_context("spawn")
    start_event = ctx.Event()
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=student_process,
            args=(
                index,
                args.host,
                args.port,
                args.room,
                args.heartbeat_s,
                args.hold_s,
                start_event,
                result_queue,
                args.verbose,
            ),
            name=f"sim-{index:02d}",
        )
        for index in range(1, args.count + 1)
    ]

    print(
        f"启动 {len(processes)} 个模拟学生进程 -> {args.host}:{args.port} room={args.room}",
        flush=True,
    )
    started_at = time.monotonic()
    for process in processes:
        process.start()
    time.sleep(0.3)
    start_event.set()

    results: dict[int, tuple[bool, str]] = {}
    collect_deadline = started_at + args.hold_s + 15.0
    try:
        while len(results) < len(processes) and time.monotonic() < collect_deadline:
            try:
                index, ok, detail = result_queue.get(timeout=0.5)
            except queue.Empty:
                if all(not process.is_alive() for process in processes):
                    break
                continue
            results[index] = (ok, detail)
    except KeyboardInterrupt:
        print("收到中断，正在结束模拟进程...", flush=True)
        start_event.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
        return 130

    for process in processes:
        process.join(timeout=2.0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)

    ok_count = sum(1 for ok, _ in results.values() if ok)
    failures = [(index, detail) for index, (ok, detail) in results.items() if not ok]
    elapsed = time.monotonic() - started_at
    print(f"JOIN 成功: {ok_count}/{len(processes)}（用时 {elapsed:.1f}s）", flush=True)
    for index, detail in sorted(failures):
        print(f"  sim{index:02d} 失败: {detail}", flush=True)
    if ok_count != len(processes):
        print("提示：请先启动服务端 python -m server.main", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
