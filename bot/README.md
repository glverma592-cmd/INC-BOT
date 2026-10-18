# Auto-DM + GitHub bridge bot

Hinglish quick setup — pura tested, koi file me token save nahi hota.

## Ye kya hai?
- **Naya member join → auto DM** (welcome + rules + Verify button)
- **Verify → role** (button click par role milega, bot ka role usse upar hona chahiye)
- **DM fail → fallback** same message `#welcome-channel` me (user ne DMs band kiye ho to)
- **GitHub → Discord** `POST /github` par embed bhejta hai (push / PR / release etc.)
- **`/dmtest @user`** admin command — kisi ko bhi test DM

## 1) Bot banana (2 min)
1. https://discord.com/developers/applications → New Application
2. **Bot** → Reset Token → copy karo (ye `DISCORD_BOT_TOKEN` hai — **webhook URL nahi**)
3. **Bot → Privileged Gateway Intents** → `SERVER MEMBERS INTENT` = ON, `MESSAGE CONTENT INTENT` = ON
4. **OAuth2 → URL Generator** → scopes: `bot`, `applications.commands` + permissions: `Send Messages`, `Manage Roles`, `Create Instant Invite` → URL se server me invite karo
5. Server me bot ka role **Verified** (ya jo bhi verify role hai) se **upar** drag karo

> ⚠️ Tune jo link bheja `https://discord.com/api/webhooks/...` — ye **Incoming Webhook** hai (`type=1`), bot **nahi**. Isse sirf channel me message jata hai, DM kabhi nahi jayega. DM ke liye upar wala `Bot Token` chahiye.

## 2) Chalana
```bash
cd autodm-bot
cp .env.example .env
# .env me ye bharo:
# DISCORD_BOT_TOKEN=...
# VERIFY_ROLE_ID=123...      (Developer Mode ON → role pe right click → Copy ID)
# WELCOME_CHANNEL_ID=1560004874580533338  (tera channel)
pip install -r requirements.txt
python bot.py
```

Log me `logged in as ...` aaye to samjho live hai.

## 3) GitHub se connect (optional)
GitHub webhook / Action se is bot ke HTTP par POST karo:
```bash
curl -X POST https://YOUR_SERVER:8080/github \
  -H "Content-Type: application/json" \
  -H "X-Hub-Secret: wahi_jo_env_me_hai" \
  -d '{
    "title": "Push: 3 commits on main",
    "text": "a1b2c3d Fix login crash — @you",
    "url": "https://github.com/OWNER/REPO/commit/abc",
    "color": 3066993,
    "event": "push",
    "channel_id": 1560004874580533338
  }'
```
`color` = embed side color (decimal), `channel_id` = jahan post karna hai.

## Tera current webhook status (verified live ✅)
- Purana: `1561363846147350688` → channel `1560004874580533338` → **abhi bhi zinda (HTTP 200)** — turant Discord me regenerate kar, warna koi bhi post kar sakta hai
- Naya jo tune abhi bheja: `1561388086745899058` (`Captain Hook`, type=1 webhook) → **same channel, same guild (`1309551158196572280`)**, test message deliver hua ✅
- Dono **channel-only** hain, DM nahi kar sakte — ye Discord ka limit hai, bot hi DM kar sakta hai

## Files
- `bot.py` — pura bot + HTTP server
- `requirements.txt` — `discord.py` + `aiohttp`
- `.env.example` — kaunse env vars bharne hain
- `test_bot.py` — 7 tests jo bina login ke saare code paths check karte hain (`python test_bot.py`)

## Tests (is sandbox me run hue)
```
7 tests — OK
- import + intents (members ON)
- VerifyButton persistent custom_id=autodm:verify
- DM_TEXT formatting
- main() bina token ke SystemExit
- GET /health 200, POST /github 401/400/404
- on_member_join DM blocked → fallback
- on_member_join success → DM sent
```
