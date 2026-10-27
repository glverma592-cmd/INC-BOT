#!/data/data/com.termux/files/usr/bin/bash
# Jarvis permissions setup — ek-ek karke permission mangega/check karega.
# Jo fail ho, use Settings > Apps > Termux:API > Permissions me Allow karo.
cd "$(dirname "$0")" || exit 1
pkg install -y termux-api >/dev/null 2>&1
echo "== 1) Battery (koi permission nahi chahiye) =="
termux-battery-status >/dev/null 2>&1 && echo "OK" || echo "FAIL - Termux:API app check karo"
echo "== 2) Location (permission mangega) =="
termux-location -p gps -r once >/dev/null 2>&1 && echo "OK" || echo "FAIL - Location Allow karo"
echo "== 3) Torch =="
termux-torch on 2>/dev/null; sleep 1; termux-torch off 2>/dev/null; echo "Torch chamka to OK"
echo "== 4) Notification (message aayega) =="
termux-notification -t "Jarvis" -c "Permissions test OK" 2>/dev/null && echo "OK"
echo "== 5) Camera info (photo nahi lega) =="
termux-camera-info >/dev/null 2>&1 && echo "OK" || echo "FAIL - Camera Allow karo"
echo ""
echo "Haath se Allow karne wale (Settings > Apps > Termux:API > Permissions):"
echo " - Files/Media: video editing ke liye Storage Allow"
echo " - Microphone: voice recording ke liye"
echo " - Phone/Contacts/SMS: abhi Jarvis ko NAHI diya — khatarnak kaam confirm par hi hoga"
echo ""
echo "Sabse zaroori: Settings > Apps > Termux > Battery = Unrestricted (24/7 ke liye)"
