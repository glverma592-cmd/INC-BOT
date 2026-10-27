#!/data/data/com.termux/files/usr/bin/bash
# Jarvis setup — Termux me ek baar chalao: bash setup-termux.sh
set -e
echo "== Jarvis setup =="
pkg install -y python ffmpeg git termux-api
mkdir -p /sdcard/Movies/Jarvis /sdcard/Download/Jarvis
python3 -m py_compile jarvis.py brain.py actions.py video_editor.py
echo ""
echo "Check report:"
python3 jarvis.py --self-check
echo ""
echo "Ab 2 kaam baki (chat me kabhi mat bhejna):"
echo "1) Gemini key set Karo (har naye Termux session me):"
echo "   export GEMINI_API_KEY='tumhari-key'"
echo "2) Termux:API app Play Store/F-Droid se install + permission do."
echo ""
echo "Chalao: python3 jarvis.py"
