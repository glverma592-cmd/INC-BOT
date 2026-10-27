#!/data/data/com.termux/files/usr/bin/bash
# Phone on hote hi Jarvis auto-start (Termux:Boot app chahiye)
set -e
cd "$(dirname "$0")"
BOOT_DIR="$HOME/.termux/boot"
mkdir -p "$BOOT_DIR"
cat > "$BOOT_DIR/start-jarvis" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
sleep 10
cd "$PWD"
bash start-24x7.sh
EOF
chmod +x "$BOOT_DIR/start-jarvis"
echo "✅ Boot auto-start lag gaya: $BOOT_DIR/start-jarvis"
echo "Shart: Termux:Boot app install ho aur Termux ki battery-optimization OFF ho."
