#!/usr/bin/env bash
# 启动学生端窗口。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
cd "$ROOT"
exec "$PY" -m clients.student_app "$@"
