"""学生端最小可运行窗口（M1）：连接 + JOIN + 接收题目广播。

第 1 周不开放作答，选项以只读卡片展示；SUBMIT 与提交状态提示在 M2 接入。
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
FONT_TITLE = ("Microsoft YaHei UI", 19, "bold")
FONT_BIG = ("Consolas", 30, "bold")
FONT_OPT = ("Microsoft YaHei UI", 13)

log = logging.getLogger("classlive.student")


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


class StudentApp:
    def __init__(self, root: tk.Tk, config: dict):
        self.root = root
        self.config = config
        server_cfg = config["server"]
        room_cfg = config["room"]
        self.host = server_cfg.get("host", "127.0.0.1")
        self.port = int(server_cfg.get("port", 9000))
        self.room_id = room_cfg["room_id"]

        self.events: queue.Queue = queue.Queue()
        self.client = NetClient(
            self.host,
            self.port,
            self.room_id,
            heartbeat_s=int(config.get("timing", {}).get("heartbeat_interval_s", 10)),
            on_message=lambda msg: self.events.put(("msg", msg)),
            on_state=lambda event, detail: self.events.put(("state", (event, detail))),
        )
        self.token = uuid.uuid4().hex
        self.joined = False
        self.current_qid = ""
        self.question_closed = True
        self.remaining_ms = 0
        self.countdown_anchor = 0.0
        self.option_rows: dict[str, tuple[tk.Frame, tk.Label, tk.Label]] = {}

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_events)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.root.title("ClassLive Student · 学生端")
        self.root.configure(bg=BG)
        self.root.geometry("860x720")
        self.root.minsize(760, 640)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 10))
        tk.Label(header, text="ClassLive 学生端", bg=BG, fg=FG, font=FONT_TITLE).pack(side="left")
        self.status = tk.Label(
            header, text="未加入", bg=CARD_ALT, fg=MUTED, font=FONT_UI, padx=14, pady=7
        )
        self.status.pack(side="right")

        join_card = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        join_card.pack(fill="x", padx=24, pady=(0, 12))
        tk.Label(join_card, text="加入课堂", bg=CARD, fg=FG, font=FONT_LABEL).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=18, pady=(14, 8)
        )

        tk.Label(join_card, text="学号", bg=CARD, fg=MUTED, font=FONT_UI).grid(
            row=1, column=0, padx=(18, 6), pady=(0, 16), sticky="w"
        )
        self.student_id_entry = tk.Entry(
            join_card,
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=FONT_UI,
            width=14,
        )
        self.student_id_entry.insert(0, "S01")
        self.student_id_entry.grid(row=1, column=1, sticky="w", pady=(0, 16), ipady=6)

        tk.Label(join_card, text="昵称", bg=CARD, fg=MUTED, font=FONT_UI).grid(
            row=1, column=2, padx=(18, 6), pady=(0, 16), sticky="w"
        )
        self.nickname_entry = tk.Entry(
            join_card,
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=FONT_UI,
            width=18,
        )
        self.nickname_entry.insert(0, "学生01")
        self.nickname_entry.grid(row=1, column=3, sticky="w", pady=(0, 16), ipady=6)

        self.join_button = tk.Button(
            join_card,
            text="加入课堂",
            command=self._join_async,
            bg=ACCENT,
            fg=ACCENT_DARK,
            activebackground="#7adcf7",
            activeforeground=ACCENT_DARK,
            relief="flat",
            font=FONT_LABEL,
            padx=20,
            pady=6,
            cursor="hand2",
        )
        self.join_button.grid(row=1, column=4, padx=(20, 18), pady=(0, 16))

        question_card = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        question_card.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        top = tk.Frame(question_card, bg=CARD)
        top.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(top, text="当前题目", bg=CARD, fg=MUTED, font=FONT_UI).pack(side="left")
        self.qid_badge = tk.Label(
            top, text="等待发题", bg=CARD_ALT, fg=ACCENT, font=("Consolas", 12, "bold"), padx=10, pady=3
        )
        self.qid_badge.pack(side="left", padx=(10, 0))
        self.countdown_label = tk.Label(top, text="--:--", bg=CARD, fg=YELLOW, font=FONT_BIG)
        self.countdown_label.pack(side="right")

        self.stem_label = tk.Label(
            question_card,
            text="加入课堂后，老师发题会实时出现在这里。",
            bg=CARD,
            fg=FG,
            font=FONT_LABEL,
            justify="left",
            anchor="w",
            wraplength=770,
        )
        self.stem_label.pack(fill="x", padx=18, pady=(6, 10))

        options = tk.Frame(question_card, bg=CARD)
        options.pack(fill="x", padx=18, pady=(0, 10))
        for idx, letter in enumerate(("A", "B", "C", "D")):
            row = tk.Frame(options, bg=CARD_ALT)
            row.pack(fill="x", pady=3)
            letter_label = tk.Label(
                row, text=letter, bg=CARD_ALT, fg=ACCENT, font=("Consolas", 15, "bold"), width=3
            )
            letter_label.pack(side="left", padx=(8, 0), pady=8)
            text_label = tk.Label(row, text="—", bg=CARD_ALT, fg=MUTED, font=FONT_OPT, anchor="w")
            text_label.pack(side="left", fill="x", expand=True, padx=(4, 10), pady=8)
            self.option_rows[letter] = (row, letter_label, text_label)

        self.hint = tk.Label(
            question_card,
            text="第 1 周范围：仅接收题目；作答与提交状态提示在 M2 接入。",
            bg=CARD,
            fg=MUTED,
            font=("Microsoft YaHei UI", 11),
            anchor="w",
        )
        self.hint.pack(fill="x", padx=18, pady=(0, 14))

    # ------------------------------------------------------------------ network
    def _join_async(self) -> None:
        if self.joined:
            return
        student_id = self.student_id_entry.get().strip()
        nickname = self.nickname_entry.get().strip() or student_id
        if not student_id:
            self._set_status("请填写学号", RED, "#3d1220")
            return
        self._set_status("正在连接…", YELLOW, "#3a2f07")
        self.join_button.configure(state="disabled")
        threading.Thread(
            target=self._do_join, args=(student_id, nickname), name="student-join", daemon=True
        ).start()

    def _do_join(self, student_id: str, nickname: str) -> None:
        if not self.client.connect():
            self.events.put(("join_failed", "无法连接服务端"))
            return
        if not self.client.join(student_id, nickname, messages.ROLE_STUDENT, token=self.token):
            self.events.put(("join_failed", "JOIN 发送失败"))
            return
        self.events.put(("join_sent", student_id))

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "msg":
                self._handle_message(payload)
            elif kind == "state":
                self._handle_state(*payload)
            elif kind == "join_failed":
                self._set_status(payload, RED, "#3d1220")
                self.join_button.configure(state="normal")
            elif kind == "join_sent":
                self._set_status("已发送 JOIN，等待 WELCOME…", YELLOW, "#3a2f07")
        self._update_countdown()
        self.root.after(100, self._poll_events)

    def _handle_state(self, event: str, detail: str) -> None:
        if event == "connect_failed":
            self._set_status("连接失败：请确认服务端已启动", RED, "#3d1220")
            self.join_button.configure(state="normal")
        elif event == "disconnected":
            self._set_status("连接断开，自动重连中…", YELLOW, "#3a2f07")
        elif event == "reconnected":
            self._set_status("重连成功，会话已认领", GREEN, ACCENT_DARK)
        elif event == "reconnect_failed":
            self._set_status("重连失败，请重新加入", RED, "#3d1220")
            self.joined = False
            self.join_button.configure(state="normal")
        elif event == "reconnecting":
            self._set_status(detail or "重连中…", YELLOW, "#3a2f07")

    def _handle_message(self, msg: dict) -> None:
        msg_type = msg["type"]
        if msg_type == messages.WELCOME:
            self.joined = True
            self.join_button.configure(state="disabled", text="已加入")
            self.student_id_entry.configure(state="disabled")
            self.nickname_entry.configure(state="disabled")
            self._set_status("已加入课堂", GREEN, ACCENT_DARK)
            self.hint.configure(text=f"session={msg['session_id'][:8]}… 等待老师发题", fg=MUTED)
        elif msg_type == messages.Q_STARTED:
            self._on_q_started(msg)
        elif msg_type == messages.Q_CLOSED:
            self._on_q_closed(msg)
        elif msg_type == messages.ERROR:
            self._set_status(f"[{msg['code']}] {msg['msg']}", RED, "#3d1220")
            if msg["code"] == 1002:
                self.joined = False
                self.join_button.configure(state="normal", text="重新加入")
                self.student_id_entry.configure(state="normal")
                self.nickname_entry.configure(state="normal")
        elif msg_type == messages.RESYNC_STATE:
            state = msg.get("qid") or "未发题"
            self.hint.configure(text=f"RESYNC：{state}，已提交={bool(msg.get('my_submission'))}", fg=MUTED)
        log.info("收到 %s", msg_type)

    def _on_q_started(self, msg: dict) -> None:
        self.current_qid = msg["qid"]
        self.question_closed = False
        self.qid_badge.configure(text=msg["qid"], fg=ACCENT)
        self.stem_label.configure(text=msg["stem"], fg=FG)
        for idx, letter in enumerate(("A", "B", "C", "D")):
            row, letter_label, text_label = self.option_rows[letter]
            row.configure(bg=CARD_ALT)
            letter_label.configure(bg=CARD_ALT, fg=ACCENT)
            text_label.configure(
                bg=CARD_ALT,
                fg=FG,
                text=msg["options"][idx] if idx < len(msg["options"]) else "—",
            )
        self.remaining_ms = max(0, int(msg["deadline_ts"]) - int(msg["server_ts"]))
        self.countdown_anchor = time.monotonic()
        self._set_status(f"收到题目 {msg['qid']}", GREEN, ACCENT_DARK)
        self.hint.configure(text="题目已到达；第 1 周仅展示，M2 开放作答。", fg=MUTED)

    def _on_q_closed(self, msg: dict) -> None:
        self.question_closed = True
        self.countdown_label.configure(text="已收卷", fg=MUTED)
        self._set_status(f"{msg['qid']} 已收卷", GREEN, ACCENT_DARK)
        correct = msg["correct"]
        for letter, (row, letter_label, text_label) in self.option_rows.items():
            if letter == correct:
                row.configure(bg="#14532d")
                letter_label.configure(bg="#14532d", fg=GREEN)
                text_label.configure(bg="#14532d", fg="#dcfce7")
        self.hint.configure(text=f"正确答案 {correct}；等待老师发下一题", fg=GREEN)

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

    def _on_close(self) -> None:
        self.client.close()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClassLive 学生端")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    config = load_config(args.config)
    root = tk.Tk()
    StudentApp(root, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
