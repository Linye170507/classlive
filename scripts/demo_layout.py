"""Windows 三窗口平铺：按标题查找 ClassLive 窗口并移动到目标位置。

用法：
    python scripts/demo_layout.py

布局（1920x1080 下）：
    老师端大屏：左 55%，全高
    学生端    ：右上 45%，高 60%
    服务端终端：右下 45%，高 40%
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
SW_RESTORE = 9
SPI_GETWORKAREA = 0x0030
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def work_area() -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def list_classlive_windows() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if "ClassLive" in title:
            found.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return found


def pick(windows: list[tuple[int, str]], keyword: str) -> int | None:
    for hwnd, title in windows:
        if keyword.lower() in title.lower():
            return hwnd
    return None


def place(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.MoveWindow(hwnd, x, y, w, h, True)


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    windows = list_classlive_windows()
    if not windows:
        print("未找到任何 ClassLive 窗口，请先启动服务端 / 老师端 / 学生端。", file=sys.stderr)
        return 1

    teacher = pick(windows, "Teacher")
    student = pick(windows, "Student")
    server = pick(windows, "Server")

    left, top, right, bottom = work_area()
    width = right - left
    height = bottom - top
    left_width = int(width * 0.55)
    right_width = width - left_width
    top_height = int(height * 0.60)

    if teacher is not None:
        place(teacher, left, top, left_width, height)
    else:
        print("未找到老师端窗口（标题应包含 ClassLive Teacher）")

    if student is not None:
        place(student, left + left_width, top, right_width, top_height)
    else:
        print("未找到学生端窗口（标题应包含 ClassLive Student）")

    if server is not None:
        place(server, left + left_width, top + top_height, right_width, height - top_height)
    else:
        print("未找到服务端窗口（标题应包含 ClassLive Server，Windows 控制台标题由服务端设置）")

    for _hwnd, title in windows:
        print(f"已识别窗口：{title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
