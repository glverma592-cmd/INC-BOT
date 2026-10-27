#!/data/data/com.termux/files/usr/bin/bash
# Home-screen button ke liye shortcuts (Termux:Widget app chahiye, F-Droid se)
set -e
cd "$(dirname "$0")"
DIR="$PWD"
mkdir -p "$HOME/.shortcuts"
cat > "$HOME/.shortcuts/Jarvis-Voice.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "$DIR"
python jarvis.py --voice
EOF
cat > "$HOME/.shortcuts/Jarvis-Start.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "$DIR"
bash start-24x7.sh
termux-toast "Jarvis 24/7 ON"
EOF
cat > "$HOME/.shortcuts/Jarvis-Stop.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "$DIR"
bash stop-24x7.sh
EOF
chmod +x "$HOME/.shortcuts/"*.sh
echo "✅ Shortcuts ban gaye: Jarvis-Voice, Jarvis-Start, Jarvis-Stop"
echo "Home screen par Termux:Widget lagakar ye button chuno."
