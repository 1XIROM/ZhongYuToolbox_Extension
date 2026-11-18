#!/usr/bin/env bash
# Linux / macOS 启动器（Windows 用根目录的 启动云笔记工具.bat）
#
#   ./start.sh                 起 8088 + 9222（后台）
#   ./start.sh --watch         再加常驻监听（HTML -> PDF -> 云笔记）
#   ./start.sh --status        看端口与进程
#   ./start.sh --stop          停掉本脚本起的进程
#   PORT=8090 ./start.sh       换端口
#
# 容器里跑要 --no-sandbox / --disable-dev-shm-usage，这里已经带上；
# 另外固定 --no-proxy-server：残留的 http_proxy 会让页面的所有请求 Failed to fetch。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8088}"
CDP_PORT="${CDP_PORT:-9222}"
PROFILE="${ZY_PROFILE:-$ROOT/.runtime/chrome-profile}"
RUNDIR="$ROOT/.runtime"
PIPELINE="${ZY_PIPELINE:-$ROOT/pipeline}"
LOG="$RUNDIR/logs"

mkdir -p "$RUNDIR" "$LOG"

serve_pid="$RUNDIR/serve.pid"
chrome_pid="$RUNDIR/chrome.pid"
watch_pid="$RUNDIR/watch.pid"

say() { printf '%s\n' "$*"; }

listening() { # $1 = port；三套探测，先试 node（Windows Git Bash 下 netstat 参数不通）
  node -e 'const net=require("net");const s=net.connect(Number(process.argv[1]),"127.0.0.1");s.on("connect",()=>{s.destroy();process.exit(0)});s.on("error",()=>process.exit(1));s.setTimeout(800,()=>{s.destroy();process.exit(1)});' "$1" >/dev/null 2>&1 && return 0
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -q ":$1 " && return 0
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -q ":$1 " && return 0
  fi
  return 1
}

find_chrome() {
  for c in "${CHROME:-}" google-chrome google-chrome-stable chromium chromium-browser msedge microsoft-edge; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
  done
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           "/c/Program Files/Google/Chrome/Application/chrome.exe" \
           "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" \
           "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"; do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

need_node() {
  if ! command -v node >/dev/null 2>&1; then
    say "[error] 没找到 node（需要 >= 21，html2note 用到全局 WebSocket）"; exit 1
  fi
  local v major
  v="$(node -v)"; major="${v#v}"; major="${major%%.*}"
  if [ "${major:-0}" -lt 21 ]; then
    say "[warn ] node $v < 21：页面服务没问题，但 html2note.js 会报 WebSocket is not defined"
  fi
}

start_serve() {
  if listening "$PORT"; then say "[skip ] $PORT 已在监听"; return; fi
  say "[start] static server http://127.0.0.1:$PORT/"
  nohup node "$ROOT/web-toolbox/serve.js" "$PORT" >"$LOG/serve.log" 2>&1 &
  echo $! >"$serve_pid"
  sleep 1
}

start_chrome() {
  if listening "$CDP_PORT"; then say "[skip ] $CDP_PORT 已在监听"; return; fi
  local bin; bin="$(find_chrome)" || { say "[warn ] 没找到 Chrome/Chromium，页面请手动打开 http://127.0.0.1:$PORT/"; return; }
  # 有显示器就开可见窗口（能自己登录），纯容器才 headless
  local hl=""
  if [ "$(uname -s)" != "Darwin" ] && [ -z "${DISPLAY:-}" ]; then
    hl="--headless=new --disable-gpu"
  fi
  say "[start] chrome debug $CDP_PORT (profile: $PROFILE)${hl:+  [headless]}"
  nohup "$bin" $hl \
    --no-sandbox --disable-dev-shm-usage \
    --no-proxy-server --remote-debugging-port="$CDP_PORT" \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    "http://127.0.0.1:$PORT/" >"$LOG/chrome.log" 2>&1 &
  echo $! >"$chrome_pid"
  sleep 3
  if ! listening "$CDP_PORT"; then
    say "[warn ] Chrome 没起来，看 $LOG/chrome.log；有界面环境可去掉 --headless=new"
  fi
}

start_watch() {
  if [ -f "$watch_pid" ] && kill -0 "$(cat "$watch_pid")" 2>/dev/null; then
    say "[skip ] watch 已在跑 (pid $(cat "$watch_pid"))"; return
  fi
  if [ ! -f "$PIPELINE/html2note.js" ]; then say "[warn ] 找不到 $PIPELINE/html2note.js"; return; fi
  say "[start] cloud-note watch"
  ( cd "$PIPELINE" && nohup node html2note.js --watch --check-cloud >"$LOG/watch.log" 2>&1 & echo $! >"$watch_pid" )
}

status() {
  say "serve  $PORT     $(listening "$PORT" && echo listening || echo down)"
  say "chrome $CDP_PORT  $(listening "$CDP_PORT" && echo listening || echo down)"
  if [ -f "$watch_pid" ] && kill -0 "$(cat "$watch_pid")" 2>/dev/null; then
    say "watch          running (pid $(cat "$watch_pid"))"
  else
    say "watch          down"
  fi
}

stop_all() {
  for f in "$watch_pid" "$chrome_pid" "$serve_pid"; do
    [ -f "$f" ] || continue
    pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null && say "[stop ] $(basename "$f" .pid) pid $pid"; fi
    rm -f "$f"
  done
}

case "${1:-}" in
  --status) status; exit 0 ;;
  --stop)   stop_all; exit 0 ;;
esac

need_node
say "root: $ROOT"
start_serve
start_chrome
if [ "${1:-}" = "--watch" ]; then start_watch; fi
say ""
status
say ""
say "页面: http://127.0.0.1:$PORT/   CDP: http://127.0.0.1:$CDP_PORT"
say "日志: $LOG/"
say "登录: 页面里填学校代码 + 账号密码；跨容器请把 profile 放在会保留的目录（ZY_PROFILE 可改）"
