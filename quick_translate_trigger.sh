#!/usr/bin/env bash
PID=$(pgrep -f "python3.*quick_translate/main.py")
if [ -n "$PID" ]; then
    kill -SIGUSR1 "$PID"
fi
