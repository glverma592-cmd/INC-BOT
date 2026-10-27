#!/data/data/com.termux/files/usr/bin/bash
# Jarvis 24/7 mode band Karo
pkill -f "watchdog.sh" 2>/dev/null
pkill -f "jarvis.py --serve" 2>/dev/null
command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock
echo "🛑 Jarvis 24/7 mode OFF."
