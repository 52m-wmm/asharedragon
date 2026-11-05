#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then echo '请先按 README 安装项目依赖。'; exit 1; fi
for port in 8000 3000; do
  if lsof -n -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then echo "端口 $port 已占用；请复用现有服务或先停止。"; exit 1; fi
done
.venv/bin/alembic -c backend/alembic.ini upgrade head
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 & pids+=("$!")
.venv/bin/python -m app.worker & pids+=("$!")
(cd frontend && exec node node_modules/next/dist/bin/next dev --hostname 127.0.0.1) & pids+=("$!")
printf '\n看板：http://127.0.0.1:3000\nAPI：http://127.0.0.1:8000/docs\nCtrl+C 停止本次启动的进程。\n'
wait
