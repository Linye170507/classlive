#!/usr/bin/env bash
# 启动 ClassLive 服务端；在任意目录执行均可。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
cd "$ROOT"
exec "$PY" -m server.main "$@"
