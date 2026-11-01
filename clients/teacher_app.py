"""老师端大屏：在线人数、倒计时、实时统计数字面板（Canvas 柱状图在 M2 接入）。

第 1 周范围：
- 自动连接服务端并以 teacher 角色 JOIN；
- 在线人数 / 提交人数实时刷新；
- 发题（Q_START）与收卷（Q_CLOSE）按钮可用；
- 柱状图区域为占位 Canvas，第 2 周替换为手绘柱状图。
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path

from protocol import messages

from .net_client import NetClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"

BG = "#0b1220"
CARD = "#142033"
CARD_ALT = "#1c2c45"
BORDER = "#24395a"
FG = "#e9f1ff"
MUTED = "#93a7c4"
ACCENT = "#4cc9f0"
ACCENT_DARK = "#062a36"
GREEN = "#4ade80"
YELLOW = "#facc15"
RED = "#fb7185"

FONT_UI = ("Microsoft YaHei UI", 12)
FONT_LABEL = ("Microsoft YaHei UI", 13)
FONT_TITLE = ("Microsoft YaHei UI", 20, "bold")
FONT_HUGE = ("Consolas", 64, "bold")
FONT_BIG = ("Consolas", 40, "bold")
FONT_MID = ("Consolas", 26, "bold")

log = logging.getLogger("classlive.teacher")


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


class TeacherApp:
    def __init__(self, root: tk.Tk, config: dict):
        self.root = root
        self.config = config
        server_cfg = config["server"]
        room_cfg = config["room"]
        self.host = server_cfg.get("host", "127.0.0.1")
        self.port = int(server_cfg.get("port", 9000))
        self.room_id = room_cfg["room_id"]
        self.questions = config["questions"]

        self.events: queue.Queue = queue.Queue()
        self.client = NetClient(
            self.host,
            self.port,
            self.room_id,
            heartbeat_s=int(config.get("timing", {}).get("heartbeat_interval_s", 10)),
            on_message=lambda msg: self.events.put(("msg", msg)),
            on_state=lambda event, detail: self.events.put(("state", (event, detail))),
        )

        self.current_qid = ""
        self.selected_qid = self.questions[0]["qid"] if self.questions else ""
        self.remaining_ms = 0
        self.countdown_anchor = 0.0
        self.question_closed = True
        self.join_token = uuid.uuid4().hex

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._connect_async)
        self.root.after(100, self._poll_events)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.root.title("ClassLive Teacher · 老师端大屏")
        self.root.configure(bg=BG)
        self.root.geometry("1180x780")
        self.root.minsize(1020, 700)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=26, pady=(22, 8))
        tk.Label(header, text="ClassLive 课堂实时答题", bg=BG, fg=FG, font=FONT_TITLE).pack(side="left")
        tk.Label(
            header,
            text=f"房间 {self.room_id}",
            bg=BG,
            fg=MUTED,
            font=FONT_LABEL,
        ).pack(side="left", padx=(16, 0), pady=(6, 0))
        self.status = tk.Label(
            header,
            text="未连接",
            bg=CARD_ALT,
            fg=MUTED,
            font=FONT_UI,
            padx=16,
            pady=8,
        )
        self.status.pack(side="right")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=26, pady=10)
        body.grid_columnconfigure(0, weight=3, uniform="cols")
        body.grid_columnconfigure(1, weight=1, uniform="cols")
        body.grid_rowconfigure(0, weight=1)

        chart_card = tk.Frame(body, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self._build_chart_card(chart_card)

        side = tk.Frame(body, bg=BG)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)
        side.grid_rowconfigure(2, weight=1)

        self._card_online(side).grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self._card_submitted(side).grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        self._card_countdown(side).grid(row=2, column=0, sticky="nsew")

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=26, pady=(6, 22))
        self._build_footer(footer)

    def _build_chart_card(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=CARD)
        top.pack(fill="x", padx=22, pady=(18, 0))
        tk.Label(top, text="本题统计", bg=CARD, fg=FG, font=FONT_LABEL).pack(side="left")
        self.qid_badge = tk.Label(
            top, text="未发题", bg=CARD_ALT, fg=ACCENT, font=("Consolas", 13, "bold"), padx=12, pady=4
        )
        self.qid_badge.pack(side="left", padx=(12, 0))
        self.submitted_label = tk.Label(top, text="submitted 0 / 0", bg=CARD, fg=MUTED, font=FONT_UI)
        self.submitted_label.pack(side="right")

        self.stem_label = tk.Label(
            parent,
            text="选择题目后点击「发题」，题目会实时广播到所有学生端。",
            bg=CARD,
            fg=MUTED,
            font=FONT_UI,
            justify="left",
            anchor="w",
            wraplength=720,
        )
        self.stem_label.pack(fill="x", padx=22, pady=(12, 6))

        self.canvas = tk.Canvas(parent, bg=CARD, highlightthickness=0, height=300)
        self.canvas.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        self.canvas.bind("<Configure>", self._draw_placeholder)

        counts = tk.Frame(parent, bg=CARD)
        counts.pack(fill="x", padx=22, pady=(0, 18))
        self.count_labels: dict[str, tk.Label] = {}
        for idx, letter in enumerate(("A", "B", "C", "D")):
            cell = tk.Frame(counts, bg=CARD_ALT)
            cell.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 10, 0))
            counts.grid_columnconfigure(idx, weight=1)
            tk.Label(cell, text=letter, bg=CARD_ALT, fg=ACCENT, font=FONT_MID).pack(pady=(8, 0))
            value = tk.Label(cell, text="0", bg=CARD_ALT, fg=FG, font=FONT_BIG)
            value.pack(pady=(0, 8))
            self.count_labels[letter] = value

    def _card_online(self, parent) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(card, text="在线学生", bg=CARD, fg=MUTED, font=FONT_UI).pack(anchor="w", padx=20, pady=(16, 0))
        self.online_label = tk.Label(card, text="0", bg=CARD, fg=ACCENT, font=FONT_HUGE)
        self.online_label.pack(anchor="w", padx=18)
        tk.Label(card, text="JOIN 后实时刷新", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 11)).pack(
            anchor="w", padx=22, pady=(0, 14)
        )
        return card

    def _card_submitted(self, parent) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(card, text="已提交 / 在线", bg=CARD, fg=MUTED, font=FONT_UI).pack(anchor="w", padx=20, pady=(16, 0))
        self.submitted_big = tk.Label(card, text="0 / 0", bg=CARD, fg=FG, font=FONT_BIG)
        self.submitted_big.pack(anchor="w", padx=18)
        return card

    def _card_countdown(self, parent) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        tk.Label(card, text="本题倒计时", bg=CARD, fg=MUTED, font=FONT_UI).pack(anchor="w", padx=20, pady=(14, 0))
        self.countdown_label = tk.Label(card, text="--:--", bg=CARD, fg=YELLOW, font=FONT_BIG)
        self.countdown_label.pack(anchor="w", padx=18)
        self.room_state_label = tk.Label(card, text="房间状态 IDLE", bg=CARD, fg=MUTED, font=FONT_UI)
        self.room_state_label.pack(anchor="w", padx=20, pady=(0, 12))
        return card

    def _build_footer(self, parent: tk.Frame) -> None:
        left = tk.Frame(parent, bg=BG)
        left.pack(side="left")
        tk.Label(left, text="题目", bg=BG, fg=MUTED, font=FONT_UI).pack(side="left", padx=(0, 10))
        self.qid_buttons: dict[str, tk.Button] = {}
        for question in self.questions:
            qid = question["qid"]
            button = tk.Button(
                left,
                text=qid,
                command=lambda q=qid: self._select_question(q),
                bg=CARD_ALT,
                fg=FG,
                activebackground=ACCENT,
                activeforeground=ACCENT_DARK,
                relief="flat",
                font=("Consolas", 12, "bold"),
                width=5,
                cursor="hand2",
            )
            button.pack(side="left", padx=3)
            self.qid_buttons[qid] = button
        self._select_question(self.selected_qid, silent=True)

        self.start_button = tk.Button(
            left,
            text="发题",
            command=self._send_start,
            bg=ACCENT,
            fg=ACCENT_DARK,
            activebackground="#7adcf7",
            activeforeground=ACCENT_DARK,
            relief="flat",
            font=FONT_LABEL,
            padx=22,
            pady=6,
            cursor="hand2",
        )
        self.start_button.pack(side="left", padx=(18, 6))
        self.close_button = tk.Button(
            left,
            text="收卷",
            command=self._send_close,
            bg=CARD_ALT,
            fg=FG,
            activebackground="#2b4264",
            activeforeground=FG,
            relief="flat",
            font=FONT_LABEL,
            padx=18,
            pady=6,
            state="disabled",
            disabledforeground="#5b718f",
            cursor="hand2",
        )
        self.close_button.pack(side="left", padx=6)
        tk.Button(
            left,
            text="重连服务器",
            command=self._connect_async,
            bg=BG,
            fg=MUTED,
            activebackground=CARD_ALT,
            activeforeground=FG,
            relief="flat",
            font=FONT_UI,
            padx=10,
            pady=6,
            cursor="hand2",
        ).pack(side="left", padx=(14, 0))

        self.hint = tk.Label(parent, text="", bg=BG, fg=MUTED, font=FONT_UI, anchor="e")
        self.hint.pack(side="right")

    def _draw_placeholder(self, event=None) -> None:
        canvas = self.canvas
        canvas.delete("all")
        w = max(canvas.winfo_width(), 100)
        h = max(canvas.winfo_height(), 100)
        for i in range(1, 5):
            y = h * i / 5
            canvas.create_line(28, y, w - 28, y, fill="#21344f", width=1)
        base = h - 34
        canvas.create_line(28, base, w - 28, base, fill="#35547d", width=2)
        canvas.create_text(
            w / 2,
            h / 2 - 14,
            text="实时柱状图 · 占位区",
            fill=FG,
            font=("Microsoft YaHei UI", 24, "bold"),
        )
        canvas.create_text(
            w / 2,
            h / 2 + 24,
            text="第 2 周接入 Canvas 手绘柱状图；当前先以数字面板验证链路",
            fill=MUTED,
            font=("Microsoft YaHei UI", 12),
        )

    # ------------------------------------------------------------------ network
    def _connect_async(self) -> None:
        if self.client.is_connected:
            self._set_status("已连接", GREEN, ACCENT_DARK)
            return
        self._set_status("正在连接服务端…", YELLOW, "#3a2f07")
        threading.Thread(target=self._do_connect, name="teacher-connect", daemon=True).start()

    def _do_connect(self) -> None:
        if not self.client.connect():
            return
        if not self.client.join("T01", "老师", messages.ROLE_TEACHER, token=self.join_token):
            log.warning("JOIN 发送失败")

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "msg":
                self._handle_message(payload)
            else:
                self._handle_state(*payload)
        self._update_countdown()
        self.root.after(100, self._poll_events)

    def _handle_state(self, event: str, detail: str) -> None:
        if event == "connected":
            self._set_status("已连接，正在加入房间…", YELLOW, "#3a2f07")
        elif event == "connect_failed":
            self._set_status("连接失败：请先启动服务端", RED, "#3d1220")
        elif event == "disconnected":
            self._set_status("连接断开，自动重连中…", YELLOW, "#3a2f07")
        elif event == "reconnecting":
            self._set_status(detail or "重连中…", YELLOW, "#3a2f07")
        elif event == "reconnected":
            self._set_status("重连成功", GREEN, ACCENT_DARK)
        elif event == "reconnect_failed":
            self._set_status("重连失败，请检查服务端", RED, "#3d1220")
        elif event == "protocol_error":
            self._set_status("协议错误，连接已断开", RED, "#3d1220")

    def _handle_message(self, msg: dict) -> None:
        msg_type = msg["type"]
        if msg_type == messages.WELCOME:
            self._set_status("已加入房间", GREEN, ACCENT_DARK)
            self._set_room_state(msg["room_state"])
        elif msg_type == messages.Q_STARTED:
            self._on_q_started(msg)
        elif msg_type == messages.STATS:
            self._on_stats(msg)
        elif msg_type == messages.Q_CLOSED:
            self._on_q_closed(msg)
        elif msg_type == messages.ERROR:
            self._set_status(f"[{msg['code']}] {msg['msg']}", RED, "#3d1220")
            self.hint.configure(text=f"{msg['code']} · {msg['msg']}", fg=RED)
        log.info("收到 %s", msg_type)

    def _on_q_started(self, msg: dict) -> None:
        self.current_qid = msg["qid"]
        self.question_closed = False
        self.qid_badge.configure(text=msg["qid"], fg=ACCENT)
        options_text = "　".join(
            f"{chr(ord('A') + i)}. {text}" for i, text in enumerate(msg["options"])
        )
        self.stem_label.configure(text=f"{msg['stem']}\n{options_text}", fg=FG)
        self.remaining_ms = max(0, int(msg["deadline_ts"]) - int(msg["server_ts"]))
        self.countdown_anchor = time.monotonic()
        self.close_button.configure(state="normal", bg=CARD_ALT)
        self._set_status(f"已发题 {msg['qid']}", GREEN, ACCENT_DARK)
        self.hint.configure(text=f"题目 {msg['qid']} 已广播，等待学生提交", fg=GREEN)

    def _on_stats(self, msg: dict) -> None:
        counts = msg["counts"]
        for letter, label in self.count_labels.items():
            label.configure(text=str(counts.get(letter, 0)))
        online = int(msg["online"])
        submitted = int(msg["submitted"])
        self.online_label.configure(text=str(online))
        self.submitted_big.configure(text=f"{submitted} / {online}")
        self.submitted_label.configure(text=f"submitted {submitted} / {online}")
        if msg.get("qid") and not self.current_qid:
            self.qid_badge.configure(text=msg["qid"], fg=ACCENT)

    def _on_q_closed(self, msg: dict) -> None:
        self.current_qid = msg["qid"]
        self.question_closed = True
        self.countdown_label.configure(text="已收卷", fg=MUTED)
        self.close_button.configure(state="disabled", bg=CARD_ALT)
        counts = msg["final_counts"]
        for letter, label in self.count_labels.items():
            label.configure(text=str(counts.get(letter, 0)))
        self._set_status(f"{msg['qid']} 已收卷", GREEN, ACCENT_DARK)
        self.hint.configure(text=f"正确答案：{msg['correct']}；此后提交会被拒绝", fg=GREEN)

    def _set_room_state(self, state: str) -> None:
        labels = {"IDLE": "空闲（可发题）", "ACTIVE": "答题中", "CLOSED": "已结束"}
        self.room_state_label.configure(text=f"房间状态 {state} · {labels.get(state, state)}")

    def _set_status(self, text: str, fg: str, bg: str) -> None:
        self.status.configure(text=text, fg=fg, bg=bg)

    def _update_countdown(self) -> None:
        if not self.current_qid or self.question_closed:
            return
        elapsed_ms = int((time.monotonic() - self.countdown_anchor) * 1000)
        remaining = self.remaining_ms - elapsed_ms
        if remaining <= 0:
            self.countdown_label.configure(text="已截止", fg=MUTED)
            return
        minutes, seconds = divmod(remaining // 1000, 60)
        color = YELLOW if remaining > 10_000 else RED
        self.countdown_label.configure(text=f"{minutes:02d}:{seconds:02d}", fg=color)

    # ------------------------------------------------------------------ actions
    def _select_question(self, qid: str, silent: bool = False) -> None:
        self.selected_qid = qid
        for item, button in self.qid_buttons.items():
            selected = item == qid
            button.configure(bg=ACCENT if selected else CARD_ALT, fg=ACCENT_DARK if selected else FG)
        if not silent:
            self.hint.configure(text=f"已选择 {qid}，点击「发题」广播", fg=MUTED)

    def _send_start(self) -> None:
        if not self.client.is_connected:
            self._set_status("未连接服务端", RED, "#3d1220")
            return
        msg = messages.make(messages.Q_START, self.room_id, qid=self.selected_qid)
        if self.client.send(msg):
            self.hint.configure(text=f"已发送 Q_START {self.selected_qid}，等待服务端广播", fg=MUTED)
        else:
            self._set_status("发题发送失败", RED, "#3d1220")

    def _send_close(self) -> None:
        if not self.current_qid:
            return
        if self.client.send(messages.make(messages.Q_CLOSE, self.room_id, qid=self.current_qid)):
            self.hint.configure(text=f"已发送 Q_CLOSE {self.current_qid}", fg=MUTED)

    def _on_close(self) -> None:
        self.client.close()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClassLive 老师端")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    config = load_config(args.config)
    root = tk.Tk()
    TeacherApp(root, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
