#!/usr/bin/env bash
# 启动 QQ Bot（main.py）。
#
# 用法:
#   ./start_bot.sh                    # 后台启动（日志 log/bot.log）
#   ./start_bot.sh --foreground       # 前台启动（Ctrl+C 停止）
#   ./start_bot.sh --stop             # 停止
#   ./start_bot.sh --status           # 查看状态
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

NAME="bot"
CMD=(uv run python main.py)

case "${1:-}" in
  --foreground)
    cd "$PROJECT_ROOT"
    exec "${CMD[@]}"
    ;;
  --stop)
    stop_service "$NAME"
    ;;
  --status)
    status_service "$NAME"
    ;;
  -h | --help)
    sed -n '2,8p' "$0"
    ;;
  *)
    start_service "$NAME" "$PROJECT_ROOT" "${CMD[@]}"
    ;;
esac
