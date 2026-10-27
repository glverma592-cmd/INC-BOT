# 🤖 Jarvis — ARISE ka Personal Phone Assistant

Termux me chalne wala Jarvis: bolna/type karna → kaam. Gemini AI soch ke liye,
rule-engine turant kaam ke liye, ffmpeg video editing ke liye.

## Kya-kya kar sakta hai

| Kaam | Bolna/Type karna hai |
|---|---|
| Torch | `torch on` / `light band karo` |
| Battery | `battery batao` |
| WiFi | `wifi batao` |
| Location | `main kahan hu` |
| App kholna | `YouTube kholo`, `WhatsApp kholo` |
| Website | `google.com kholo` |
| Brightness | `brightness 70 karo` |
| Photo | `photo lo` |
| Reminder | `10 minute baad yaad dilana ki match hai` |
| Yaad rakhna | `yaad rakho kal mera match 5 baje hai` |
| Time/Date | `time kya hai`, `aaj date kya hai` |
| Sawal-jawab | Gemini key ho to kuch bhi pucho |
| Video Shorts | `video /sdcard/Download/game.mp4 shorts bana do` |
| Video tools | `python jarvis.py video in.mp4 --shorts --duration 30` |

## Termux me setup (ek baar)

```bash
pkg install -y git
```

```bash
git clone <tumhara-github-repo> jarvis
```

```bash
cd jarvis
```

```bash
bash setup-termux.sh
```

Saath me Play Store se **Termux:API** app install Karo aur usko permissions do
(battery/location/camera). **MacroDroid** bhi install rakho — phone-wide
trigger ke liye neeche dekho.

Gemini key (chat me kabhi mat bhejna, sirf Termux me):

```bash
export GEMINI_API_KEY='tumhari-key'
```

## Chalana

```bash
python jarvis.py
```

Ek hi command (MacroDroid/automation ke liye):

```bash
python jarvis.py --once "torch on" --speak
```

System check:

```bash
python jarvis.py --self-check
```

## hamesha active (background)

```bash
nohup python jarvis.py --serve > ~/jarvis-server.log 2>&1 &
```

Server sirf isi phone par chalega: `http://127.0.0.1:8765/cmd?text=torch%20on`
Saath me header hoga: `X-Jarvis-Token` = `config.json` wali `local_token`
(pehle use badal lo!). Phone restart par auto-start ke liye **Termux:Boot**
app lagakar `~/.termux/boot/` me ye line wali file rakho:

```bash
cd ~/jarvis && nohup python jarvis.py --serve > ~/jarvis-server.log 2>&1 &
```

## MacroDroid se phone-wide control

1. MacroDroid → Naya Macro → Trigger: **Button / Widget / Voice / Time** (jo chaho)
2. Action: **HTTP GET** →
   `http://127.0.0.1:8765/cmd?text=YouTube%20kholo`
   Header: `X-Jarvis-Token: tumhari-local-token`
3. Save. Ab phone me kahin se bhi ye trigger chalega.

App kholna MacroDroid ka **Launch App** action aur pakka karta hai — Termux
se na khule to MacroDroid me Launch App + upar wali HTTP action dono rakho.

## Khud edit / bug fix

- Code **Acode** app se isi folder me edit Karo (`jarvis.py`, `brain.py`,
  `actions.py`, `video_editor.py` — alag-alag file isiliye hai).
- Galti aaye to `jarvis.log` dekho; `--self-check` report bhi banata hai.
- Naya version GitHub se: `bash update.sh` (purana apne-aap stash backup me).
- Kuch bigde to purana wapas: `git stash pop`

## Safety

- Password/OTP/payment/delete/message-bhejna — Jarvis khud nahi karega,
  pehle tumse confirm karega.
- API key code/GitHub me kabhi nahi — sirf `export` se Termux me.
- Server sirf `127.0.0.1` par hai — bahar se koi nahi khol sakta.

## Video editing (ffmpeg)

```bash
python jarvis.py video /sdcard/Download/game.mp4 --shorts --duration 30
python jarvis.py video in.mp4 --compress
python jarvis.py video in.mp4 --audio
python jarvis.py video in.mp4 --speed 1.5
python jarvis.py video in.mp4 --thumb
```

Output: `/sdcard/Movies/Jarvis/`
