#!/usr/bin/env bash
PID=$(pgrep -f "python3.*quick_traslate/main.py")
if [ -n "$PID" ]; then
    kill -SIGUSR1 "$PID"
fi
