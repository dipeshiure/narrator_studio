#!/bin/zsh
# Double-click to open Narrator Studio (http://localhost:8510).
cd "$(dirname "$0")"
if ! curl -s -o /dev/null http://127.0.0.1:8510; then
  nohup venv/bin/python server.py > logs/server.log 2>&1 &
  sleep 2
fi
open "http://localhost:8510"
