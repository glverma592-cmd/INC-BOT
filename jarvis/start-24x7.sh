#!/data/data/com.termux/files/usr/bin/bash
# Jarvis ko 24/7 mode me chalu Karo (wake-lock + watchdog)
cd "$(dirname "$0")" || exit 1
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
if pgrep -f "watchdog.sh" >/dev/null 2>&1; then
  echo "✅ Jarvis 24/7 pehle se chalu hai."
else
  nohup bash watchdog.sh >/dev/null 2>&1 &
  echo "🤖 Jarvis 24/7 mode ON — server + watchdog chalu."
fi
echo "Check: python jarvis.py --self-check"
