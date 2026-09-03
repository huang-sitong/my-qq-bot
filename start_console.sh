#!/usr/bin/env bash
# 启动控制台：FastAPI 后端（console_api.py）+ Web 前端（Vite dev server）。
#
# 用法:
#   ./start_console.sh                # 后台启动后端与前端
#   ./start_console.sh --stop         # 停止后端与前端
#   ./start_console.sh --status       # 查看状态
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

BACKEND_NAME="console"
WEB_NAME="web"
BACKEND_CMD=(uv run python console_api.py)
WEB_CMD=(npm run dev)

case "${1:-}" in
  --stop)
    stop_service "$BACKEND_NAME"
    stop_service "$WEB_NAME"
    ;;
  --status)
    status_service "$BACKEND_NAME"
    status_service "$WEB_NAME"
    ;;
  -h | --help)
    sed -n '2,7p' "$0"
    ;;
  *)
    start_service "$BACKEND_NAME" "$PROJECT_ROOT" "${BACKEND_CMD[@]}"
    start_service "$WEB_NAME" "$PROJECT_ROOT/web" "${WEB_CMD[@]}"
    echo
    echo "控制台已启动:"
    echo "  后端 API:  http://127.0.0.1:8000/api/health"
    echo "  Web 页面:  http://localhost:5173/settings"
    echo "  日志页:     http://localhost:5173/logs"
    ;;
esac
