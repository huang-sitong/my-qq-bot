#!/usr/bin/env bash
# 启动全部服务：QQ Bot + 控制台后端（FastAPI）+ Web 前端（Vite dev server）。
#
# 用法:
#   ./start_all.sh                # 后台启动全部
#   ./start_all.sh --stop         # 停止全部
#   ./start_all.sh --status       # 查看全部状态
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

case "${1:-}" in
  --stop)
    stop_service bot
    stop_service console
    stop_service web
    ;;
  --status)
    status_service bot
    status_service console
    status_service web
    ;;
  -h | --help)
    sed -n '2,7p' "$0"
    ;;
  *)
    start_service bot "$PROJECT_ROOT" uv run python main.py
    start_service console "$PROJECT_ROOT" uv run python console_api.py
    start_service web "$PROJECT_ROOT/web" npm run dev
    echo
    echo "全部服务已启动:"
    echo "  Bot:        日志 log/bot.log"
    echo "  控制台后端: http://127.0.0.1:8000/api/health"
    echo "  Web 页面:   http://localhost:5173/settings"
    ;;
esac
