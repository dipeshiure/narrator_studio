#!/bin/zsh
# Double-click to open Narrator Studio (http://localhost:8510) and follow its log.
# Closing this window stops the log view only. The server keeps running.
cd "$(dirname "$0")"
mkdir -p logs
LOG="$PWD/logs/server.log"
URL="http://127.0.0.1:8510/"

server_pid() {
  lsof -nP -tiTCP:8510 -sTCP:LISTEN 2>/dev/null | head -1
}

server_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | awk '/^n/ { sub(/^n/, ""); print; exit }'
}

server_stdout() {
  local path
  path=$(lsof -nP -a -p "$1" -d 1 -Fn 2>/dev/null | awk '/^n/ { sub(/^n/, ""); print; exit }')
  [[ -f "$path" ]] && print -r -- "$path"
}

pid=$(server_pid || true)
if [[ -z "$pid" ]]; then
  echo "Narrator Studio を起動しています..."
  printf '\n===== %s 起動 =====\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
  nohup venv/bin/python -u server.py >> "$LOG" 2>&1 &
  pid=$!
  ok=0
  for _ in {1..40}; do
    if curl -sf -o /dev/null --max-time 1 "$URL"; then
      ok=1
      break
    fi
    if ! kill -0 $pid 2>/dev/null; then
      echo "起動に失敗しました。ログの末尾:"
      tail -n 40 "$LOG"
      echo "Enter で閉じます。"
      read
      exit 1
    fi
    sleep 0.25
  done
  if [[ $ok -eq 0 ]]; then
    echo "起動を確認できませんでした。ログを表示します。"
  fi
  follow="$LOG"
else
  cwd=$(server_cwd "$pid")
  follow=$(server_stdout "$pid" || true)
  follow=${follow:-$LOG}
  if [[ "$cwd" == "$PWD" ]]; then
    echo "すでに起動しています。"
  else
    echo "別のコピーが動いています: ${cwd:-不明}"
    echo "そのログを表示します。"
  fi
fi

open "http://localhost:8510"
echo ""
echo "ログ: $follow"
echo "イラスト生成（何枚目か）や動画作成の進行がここに流れます。"
echo "このウィンドウを閉じてもサーバーは動き続けます。"
echo "止めるとき: kill \$(lsof -tiTCP:8510 -sTCP:LISTEN)"
echo "----------------------------------------"
touch "$follow"
tail -n 80 -F "$follow"
