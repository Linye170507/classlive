#!/usr/bin/env bash
# 三窗口平铺（Linux/macOS）：依赖 xdotool。Windows 请直接运行 scripts/demo_layout.py。
set -euo pipefail

if ! command -v xdotool >/dev/null 2>&1; then
  echo "缺少 xdotool。Ubuntu: sudo apt install xdotool；macOS: brew install xdotool" >&2
  echo "Windows 请运行：python scripts/demo_layout.py" >&2
  exit 1
fi

find_window() {
  xdotool search --name "$1" 2>/dev/null | head -n1 || true
}

TEACHER="$(find_window 'ClassLive Teacher')"
STUDENT="$(find_window 'ClassLive Student')"
SERVER="$(find_window 'ClassLive Server')"

read -r SCREEN_W SCREEN_H < <(xdotool getdisplaygeometry)

move_window() {
  local win="$1" x="$2" y="$3" w="$4" h="$5"
  xdotool windowmove "$win" "$x" "$y"
  xdotool windowsize "$win" "$w" "$h"
}

LEFT_W=$((SCREEN_W * 55 / 100))
RIGHT_W=$((SCREEN_W - LEFT_W))

[ -n "$TEACHER" ] && move_window "$TEACHER" 0 0 "$LEFT_W" "$SCREEN_H" || echo "未找到老师端窗口，请先运行 scripts/run_teacher.sh"
[ -n "$STUDENT" ] && move_window "$STUDENT" "$LEFT_W" 0 "$RIGHT_W" "$((SCREEN_H * 60 / 100))" || echo "未找到学生端窗口，请先运行 scripts/run_student.sh"
[ -n "$SERVER" ] && move_window "$SERVER" "$LEFT_W" "$((SCREEN_H * 60 / 100))" "$RIGHT_W" "$((SCREEN_H - SCREEN_H * 60 / 100))" || echo "未找到服务端窗口（提示：终端标题需为 ClassLive Server）"
