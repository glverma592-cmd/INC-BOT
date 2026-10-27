#!/data/data/com.termux/files/usr/bin/bash
# GitHub se naya code lekar Jarvis update Karo (pehle purana save bhi hota hai)
set -e
cd "$(dirname "$0")"
git add -A >/dev/null 2>&1 || true
git stash push -m "jarvis-auto-backup" >/dev/null 2>&1 || true
git pull
python3 -m py_compile jarvis.py brain.py actions.py video_editor.py
python3 jarvis.py --self-check
echo "✅ Update ho gaya. Kuch bigda ho to: git stash pop"
