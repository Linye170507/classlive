#!/usr/bin/env bash
# 启动多进程模拟学生，默认 20 个；额外参数会覆盖默认值，例如 --count 5 --hold-s 60。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
cd "$ROOT"
exec "$PY" -m clients.simulator --count 20 "$@"
