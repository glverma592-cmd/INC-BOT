#!/data/data/com.termux/files/usr/bin/bash
# Watchdog: Jarvis server band ho jaye to khud wapas chalu karta hai.
cd "$(dirname "$0")" || exit 1
while true; do
  if ! pgrep -f "jarvis.py --serve" >/dev/null 2>&1; then
    echo "[$(date '+%F %T')] Jarvis server chalu kar raha hu" >> watchdog.log
    python jarvis.py --serve >> jarvis-server.log 2>&1 &
  fi
  sleep 15
done
