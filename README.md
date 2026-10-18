# INC-BOT

eSports Discord + GitHub automation — INC server (guild 1559846127279407145) ke liye.

## Kya hai isme?
1. `.github/workflows/discord-notify.yml` → push/PR/issue/release pe Discord channel `1559886513423257610` me embed
2. `bot/` → Auto-DM bot (join -> DM + Verify button)
3. `setup.sh` → secret set + push ek command me

Live webhook: `1561454862871568445` (INC Bot) channel `1559886513423257610` verify HTTP 200.

> ⚠️ Webhook/Token ko file me commit mat karo — `.env` use karo.

## Quick Start
```bash
export GITHUB_TOKEN=ghp_xxx
export DISCORD_WEBHOOK='https://discord.com/api/webhooks/ID/TOKEN'
./setup.sh glverma592-cmd/INC-BOT
```
## Bot
```bash
cd bot
cp .env.example .env
pip install -r requirements.txt
python bot.py
```
