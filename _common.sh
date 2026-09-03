#!/usr/bin/env bash
# 内部共享库：服务 启动/停止/状态 管理。
# 供 scripts/start_*.sh 通过 source 使用，勿直接执行。
#
# 每个服务用 setsid 放入独立进程组，停止时按进程组整体 kill，
# 确保 npm/uv 等包装进程派生的子进程（vite、uvicorn 等）也能被清理。
set -euo pipefail

# 项目根目录（本文件位于项目根目录下）
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$PROJECT_ROOT/log"

# 返回服务的 pid 文件路径
pid_file() { printf '%s\n' "$PROJECT_ROOT/log/$1.pid"; }
# 返回服务的日志文件路径
log_file() { printf '%s\n' "$PROJECT_ROOT/log/$1.log"; }

# 判断服务是否在运行（依据 pid 文件 + kill -0）
_is_running() {
  local pf
  pf="$(pid_file "$1")"
  [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null
}

# 返回服务对应的命令行特征（用于识别 pid 文件丢失/手动启动的残留进程）
_service_pattern() {
  case "$1" in
    bot)     printf '%s\n' '[m]ain.py' ;;
    console) printf '%s\n' '[c]onsole_api.py' ;;
    web)     printf '%s\n' 'my-qq-bot/web.*[v]ite' ;;
    *)       printf '%s\n' '' ;;
  esac
}

# 是否存在匹配该服务命令行的进程（不依赖 pid 文件）
_service_has_process() {
  local pattern
  pattern="$(_service_pattern "$1")"
  [[ -n "$pattern" ]] && pgrep -f "$pattern" >/dev/null 2>&1
}

# 查询服务状态
status_service() {
  local name="$1"
  if _is_running "$name"; then
    printf '%s: running (pid %s)\n' "$name" "$(cat "$(pid_file "$name")")"
  elif _service_has_process "$name"; then
    printf '%s: running (untracked, no pid file)\n' "$name"
  else
    rm -f "$(pid_file "$name")"
    printf '%s: stopped\n' "$name"
  fi
}

# 停止服务：先按进程组整体 kill，失败再回退到单 PID；
# 再按命令行特征清理未登记 pid 的残留进程（手动启动 / pid 文件丢失）
stop_service() {
  local name="$1"
  local pf pid pattern
  pf="$(pid_file "$name")"
  if _is_running "$name"; then
    pid="$(cat "$pf")"
    # 负 PID = 进程组（setsid 启动后 PID 即 PGID）
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    sleep 0.3
    # 进程组没清干净时，逐一补杀
    if kill -0 "$pid" 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pf"
    printf '%s: stopped\n' "$name"
  else
    rm -f "$pf"
    printf '%s: not running\n' "$name"
  fi

  # 清理未登记 pid 的残留进程（例如手动启动、pid 文件丢失）
  pattern="$(_service_pattern "$name")"
  if [[ -n "$pattern" ]] && pgrep -f "$pattern" >/dev/null 2>&1; then
    pkill -TERM -f "$pattern" 2>/dev/null || true
    sleep 0.3
    pkill -KILL -f "$pattern" 2>/dev/null || true
  fi
}

# 后台启动一个服务（setsid 新会话/新进程组）
# 用法: start_service <name> <workdir> <cmd...>
start_service() {
  local name="$1"; shift
  local workdir="$1"; shift
  local pf lf
  pf="$(pid_file "$name")"
  lf="$(log_file "$name")"
  if _is_running "$name"; then
    printf '%s: already running (pid %s)\n' "$name" "$(cat "$pf")"
    return 1
  fi
  if _service_has_process "$name"; then
    printf '%s: already running (untracked, no pid file); run --stop first\n' "$name" >&2
    return 1
  fi
  (
    cd "$workdir"
    setsid "$@" >> "$lf" 2>&1 &
    echo $! > "$pf"
  )
  sleep 0.5
  if _is_running "$name"; then
    printf '%s: started (pid %s), log: %s\n' "$name" "$(cat "$pf")" "$lf"
  else
    printf '%s: failed to start, see %s\n' "$name" "$lf" >&2
    return 1
  fi
}
