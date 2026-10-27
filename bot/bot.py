"""
INC Bot — Auto-DM + Guild management
Naya member -> DM + Verify button, /dmtest, /ping, /say, /announce, /clear + GitHub -> Discord bridge
"""
from __future__ import annotations
import ast, asyncio, calendar, datetime, io, json, logging, operator, os, random, re, string, time, traceback, urllib.parse, ctypes.util as _ctypes_util
from pathlib import Path
import aiohttp, discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands
try:
    from discord.ext import voice_recv as _voice_recv
except Exception:
    _voice_recv=None
# Yeh library har sekand "unexpected rtcp packet" likhti hai — band Karo nahi to log ghul jayega
try:
    logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.CRITICAL)
    logging.getLogger("discord.ext.voice_recv.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.ext.voice_recv.router").setLevel(logging.WARNING)
except Exception:
    pass

def _load_dotenv(path=None):
    # cwd/.env, bot/.env, repo-root/.env teeno jagah dhundho (hosting ke liye)
    candidates=[]
    if path: candidates.append(Path(path))
    candidates += [Path(".env"), Path("bot/.env"), Path(__file__).resolve().parent/".env", Path(__file__).resolve().parent.parent/".env"]
    p=next((c for c in candidates if c.exists()), None)
    if p is None: return
    for line in p.read_text(encoding="utf-8").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k,v=line.split("=",1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

_HTTP_SESSION=None
class _SharedSession:
    """Ek hi HTTP connection baar-baar use Karo — har sawal par naya
    connection banane ka time bachao (chatbot/VC fast response jugaad)."""
    async def __aenter__(self):
        global _HTTP_SESSION
        if _HTTP_SESSION is None or _HTTP_SESSION.closed:
            _HTTP_SESSION=aiohttp.ClientSession()
        return _HTTP_SESSION
    async def __aexit__(self, *exc):
        return False
def _sess():
    return _SharedSession()

TOKEN=os.environ.get("DISCORD_BOT_TOKEN","")
GITHUB_HOOK_SECRET=os.environ.get("GITHUB_HOOK_SECRET","")
HTTP_PORT=int(os.environ.get("HTTP_PORT","8080"))
VERIFY_ROLE_ID=int(os.environ.get("VERIFY_ROLE_ID","0") or 0)
WELCOME_CHANNEL_ID=int(os.environ.get("WELCOME_CHANNEL_ID","0") or 0)
DM_TEXT=os.environ.get("DM_TEXT","Hey {user} 👋 Welcome to **{guild}**! 🎉\n\nWe're happy to have you here.\n1. Please read the server rules\n2. If a **Verify** button appears below, tap it\n3. Then all the channels will open for you\n\nHave fun and enjoy your stay! 💖")
REQUIRE_VERIFY=os.environ.get("REQUIRE_VERIFY","1")=="1"
GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY","")
GEMINI_MODEL=os.environ.get("GEMINI_MODEL","gemini-3.5-flash-lite")
BOT_OWNER_ID=int(os.environ.get("BOT_OWNER_ID","0") or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log=logging.getLogger("autodm")

class _SecretFilter(logging.Filter):
    """Suraksha jaal: token/API-key jaisi cheez galti se bhi log me aa jaye to taare (****) ban jayegi."""
    _PATTERNS=(
        r"AIza[0-9A-Za-z_\-]{10,}",
        r"ghp_[0-9A-Za-z]{10,}",
        r"[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{15,}",
        r"(?i)(api[_\-]?key|token|secret|authorization)[\"']?\s*[=:]\s*[\"']?\S+",
    )
    def filter(self, record):
        try:
            _msg=record.getMessage()
            _saaf=_msg
            for _p in self._PATTERNS:
                _saaf=re.sub(_p, "****", _saaf)
            if _saaf != _msg:
                record.msg=_saaf; record.args=()
        except Exception: pass
        return True
logging.getLogger().addFilter(_SecretFilter())
# Library ka faltu shor band — log me sirf kaam ki baat dikhegi
for _noisy in ("discord.gateway", "discord.voice_state", "discord.client", "discord.http"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_startup_dm_sent=False
intents=discord.Intents.default()
intents.members=True
intents.message_content=True
bot=commands.Bot(command_prefix="!", intents=intents)

class VerifyButton(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="autodm:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not VERIFY_ROLE_ID:
            await interaction.response.send_message("⚠️ `VERIFY_ROLE_ID` is not set.", ephemeral=True); return
        role=interaction.guild.get_role(VERIFY_ROLE_ID) if interaction.guild else None
        if role is None:
            await interaction.response.send_message("⚠️ Role not found.", ephemeral=True); return
        try: await interaction.user.add_roles(role, reason="verify button")
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ I do not have permission to give that role. Move my role above the Verified role.", ephemeral=True); return
        try: await interaction.message.edit(view=None)
        except: pass
        await interaction.response.send_message(f"✅ Verified! You received the **{role.name}** role.", ephemeral=True)

@bot.event
async def setup_hook():
    bot.add_view(VerifyButton())
    bot.add_view(GiveawayView())
    bot.add_view(MusicPanelView())
    bot.add_view(TicketPanelView())
    # SIRF global sync — guild copies banaoge to Discord me har command 2-2 dikhega
    try:
        await bot.tree.sync()
        log.info("slash commands synced (global only)")
    except Exception as e: log.warning("sync fail: %s", e)

_PROFILE_NAME=os.environ.get("BOT_PROFILE_NAME", "Nezu")  # dusre bot (Kai) ke liye .env me khaali rakho to naam nahi badlega
_PROFILE_PIC=Path(__file__).resolve().parent / "nezu.jpg"
_PROFILE_MARK=Path(__file__).resolve().parent / "profile_done.json"
async def _apply_profile_once():
    """Bot ka naam 'Nezu' aur profile pic (nezu.jpg) ek baar set karo.
    Discord naam badalne par rate-limit lagata hai, isliye marker file
    me nishan laga dete hai — har restart par try nahi hoga."""
    try:
        if not _PROFILE_NAME: return  # .env me BOT_PROFILE_NAME khaali = apna purana naam hi rakho (Kai ke liye)
        if bot.user is None or not _PROFILE_PIC.exists(): return
        import hashlib as _hl
        pic_hash=_hl.sha256(_PROFILE_PIC.read_bytes()).hexdigest()[:16]
        mark={}
        try: mark=json.loads(_PROFILE_MARK.read_text(encoding="utf-8"))
        except Exception: mark={}
        naam_badla = bot.user.name != _PROFILE_NAME
        pic_badli = mark.get("pic") != pic_hash
        if not naam_badla and not pic_badli: return
        kwargs={}
        if naam_badla: kwargs["username"]=_PROFILE_NAME
        if pic_badli: kwargs["avatar"]=_PROFILE_PIC.read_bytes()
        await bot.user.edit(**kwargs)
        mark["pic"]=pic_hash; mark["name"]=_PROFILE_NAME
        try: _PROFILE_MARK.write_text(json.dumps(mark), encoding="utf-8")
        except Exception: pass
        log.info("profile set: naam=%s pic-nayi=%s", _PROFILE_NAME, pic_badli)
    except Exception as e:
        # naam liya hua (taken) ya rate-limit — bot chalta rahega, purana naam chalega
        log.warning("profile set nahi ho paya (naam taken/rate-limit ho sakta hai): %s", e)

@bot.event
async def on_ready():
    log.info("logged in as %s (id=%s) — %s guilds", bot.user, bot.user.id, len(bot.guilds))
    await _apply_profile_once()
    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers | /help"))
    except Exception:
        pass
    log.info("verify=%s port=%s", REQUIRE_VERIFY, HTTP_PORT)
    # Purani guild-wise command copies saaf kar do — global 60 commands hi rahenge,
    # warna Discord har command 2 baar dikhata hai (ek global + ek server copy)
    if TOKEN and bot.user:
        try:
            async with aiohttp.ClientSession() as sess:
                for g in bot.guilds:
                    try:
                        url=f"https://discord.com/api/v10/applications/{bot.user.id}/guilds/{g.id}/commands"
                        async with sess.put(url, headers={"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}, json=[]) as resp:
                            if resp.status not in (200, 201, 204):
                                log.warning("guild %s duplicate-cleanup HTTP %s", g.id, resp.status)
                    except Exception:
                        pass
            log.info("purane guild duplicate commands saaf kar diye")
        except Exception:
            log.warning("duplicate-cleanup fail\n%s", traceback.format_exc())
    _restore_giveaways()
    _restore_reminders()
    # Owner ko ek baar startup DM — bot online hua to owner ko Discord me khud khabar
    global _startup_dm_sent
    if not _startup_dm_sent:
        _startup_dm_sent=True
        try:
            info=await bot.application_info()
            owner=info.owner
            if owner is not None:
                total_cmds=len(bot.tree.get_commands())
                await owner.send(f"🟢 **Nezu is online!**\nServers: **{len(bot.guilds)}** | Commands: **{total_cmds}**\nThe bot is ready. Use `/help` to see all commands.")
        except Exception:
            pass


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.warning("command error (%s): %s", getattr(interaction.command, "name", "?"), error)
    msg="⚠️ Something went wrong — please try again. If it keeps happening, tell an admin."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

_WELCOME_OFF_FILE = Path(__file__).resolve().parent / "welcome_off.json"
def _load_welcome_off() -> set:
    try:
        return {int(x) for x in json.loads(_WELCOME_OFF_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return set()
_welcome_off: set = _load_welcome_off()
def _save_welcome_off():
    try:
        _WELCOME_OFF_FILE.write_text(json.dumps(sorted(_welcome_off)), encoding="utf-8")
    except Exception:
        log.warning("welcome_off save fail\n%s", traceback.format_exc())
def _welcome_enabled(guild_id) -> bool:
    """Auto-welcome har server me bina command ke DEFAULT ON hai.
    Sirf wahi band hai jaha admin ne /server welcome Off kiya ho."""
    try:
        return int(guild_id) not in _welcome_off
    except Exception:
        return True
def _set_welcome(guild_id, on: bool):
    try:
        gid=int(guild_id)
    except Exception:
        return
    if on: _welcome_off.discard(gid)
    else: _welcome_off.add(gid)
    _save_welcome_off()

def _pick_welcome_channel(guild):
    """Koi setup nahi — system channel, fir welcome/general naam wala,
    fir pehla text channel jaha bot bol sakta hai."""
    try:
        me=guild.me
        if me is None: return None
        def bol_sakta(ch):
            try:
                if not isinstance(ch, discord.TextChannel): return False
                p=ch.permissions_for(me)
                return bool(p.view_channel and p.send_messages)
            except Exception:
                return False
        sysch=getattr(guild, "system_channel", None)
        if sysch is not None and bol_sakta(sysch): return sysch
        channels=sorted(guild.text_channels, key=lambda c: c.position)
        for shabd in ("welcome", "swagat", "general", "main", "chat", "lobby", "announcement"):
            for ch in channels:
                if shabd in ch.name.lower() and bol_sakta(ch): return ch
        for ch in channels:
            if bol_sakta(ch): return ch
    except Exception:
        pass
    return None

async def _send_auto_welcome(member) -> bool:
    """Naya member -> server channel me apne aap welcome (zero setup)."""
    try:
        guild=member.guild
        if guild is None or member.bot is True: return False
        if not _welcome_enabled(guild.id): return False
        ch=_pick_welcome_channel(guild)
        if ch is None: return False
        rules_ch=next((c for c in guild.text_channels if "rule" in c.name.lower()), None)
        embed=discord.Embed(
            title=f"🌸 Welcome to {guild.name}!",
            description=(f"Hey {member.mention}, a very warm welcome to the **{guild.name}** family! 💜\n\n"
                         f"👥 You are member **#{guild.member_count}** of this server.\n"
                         + (f"📜 Please take a moment to read {rules_ch.mention} first.\n" if rules_ch is not None else "")
                         + "💬 Need any help? Just ask in chat — and type `/help` to see everything I can do!"),
            color=0xff6b9d)
        try: embed.set_thumbnail(url=member.display_avatar.url)
        except Exception: pass
        if guild.icon:
            try: embed.set_author(name=guild.name, icon_url=guild.icon.url)
            except Exception: pass
        embed.set_footer(text="Nezu • Auto-Welcome (runs automatically, no setup needed)")
        await ch.send(content=f"🎉 Welcome {member.mention}!", embed=embed)
        log.info("auto-welcome -> %s in %s (#%s)", member, guild.name, ch.name)
        return True
    except Exception:
        log.warning("auto-welcome fail\n%s", traceback.format_exc())
        return False

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot and await _antinuke_check_bot_add(member):
        return
    await _check_antiraid(member)
    # Auto-role: set kiya hua role naye member ko khud mil jayega
    rid=_autoroles.get(member.guild.id)
    if rid:
        role=member.guild.get_role(rid)
        if role is None:
            log.warning("autorole: role %s nahi mila guild %s me", rid, member.guild.id)
        else:
            try:
                await member.add_roles(role, reason="auto-role on join")
                log.info("autorole: %s ko %s diya (%s)", member, role.name, member.guild.name)
            except discord.Forbidden:
                log.warning("autorole: %s ko %s nahi de paya — bot ka role upar rakho / Manage Roles chahiye", member, role.name)
            except Exception:
                log.error("autorole fail\n%s", traceback.format_exc())
    # Server channel me auto-welcome (koi command/setup nahi chahiye)
    channel_welcome=False
    if member.bot is not True:
        channel_welcome=await _send_auto_welcome(member)
    text=DM_TEXT.format(user=member.mention, guild=member.guild.name, count=member.guild.member_count)
    # Verify button sirf tab lagao jab us server me verify-role sach me set/mila ho,
    # warna member button dabata aur sirf error dikhta tha (zero-setup kharab hota tha)
    _vrole=member.guild.get_role(VERIFY_ROLE_ID) if (VERIFY_ROLE_ID and member.guild) else None
    view=VerifyButton() if (REQUIRE_VERIFY and _vrole is not None) else None
    try:
        await member.send(text, view=view)
        log.info("DM sent -> %s (%s)", member, member.id)
    except discord.Forbidden:
        log.warning("DM blocked by %s", member)
        if not channel_welcome:
            await _fallback_post(member, "DM could not be sent (the user has DMs blocked)")
    except Exception:
        log.error("DM fail %s\n%s", member, traceback.format_exc())
        if not channel_welcome:
            await _fallback_post(member, "DM could not be sent")

async def _fallback_post(member: discord.Member, reason: str = ""):
    if not WELCOME_CHANNEL_ID: return
    ch=member.guild.get_channel(WELCOME_CHANNEL_ID)
    if ch is None: return
    text=DM_TEXT.format(user=member.mention, guild=member.guild.name, count=member.guild.member_count)
    try: await ch.send(text, view=VerifyButton() if REQUIRE_VERIFY else None)
    except Exception: pass

_fallback = _fallback_post

# ---- SLASH COMMANDS ----
START_TIME = time.time()
_WARNINGS_FILE = Path(__file__).resolve().parent / "warnings.json"
def _load_warnings() -> dict[int, dict[int, list[str]]]:
    try:
        data=json.loads(_WARNINGS_FILE.read_text(encoding="utf-8"))
        return {int(g): {int(u): [str(x) for x in v] for u, v in members.items()} for g, members in data.items()}
    except Exception:
        return {}
_warnings: dict[int, dict[int, list[str]]] = _load_warnings()
def _save_warnings():
    try:
        _WARNINGS_FILE.write_text(json.dumps({str(g): {str(u): v for u, v in members.items()} for g, members in _warnings.items()}), encoding="utf-8")
    except Exception:
        log.warning("warnings save fail\n%s", traceback.format_exc())

_AUTOROLE_FILE = Path(__file__).resolve().parent / "autoroles.json"
def _load_autoroles() -> dict[int, int]:
    try:
        return {int(k): int(v) for k, v in json.loads(_AUTOROLE_FILE.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}
_autoroles: dict[int, int] = _load_autoroles()
def _save_autoroles():
    try:
        _AUTOROLE_FILE.write_text(json.dumps({str(k): v for k, v in _autoroles.items()}), encoding="utf-8")
    except Exception:
        log.warning("autoroles save fail\n%s", traceback.format_exc())

_NOPREFIX_FILE = Path(__file__).resolve().parent / "noprefix.json"
def _load_noprefix() -> dict[int, float]:
    try:
        raw={int(k): float(v) for k, v in json.loads(_NOPREFIX_FILE.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}
    now_ts=datetime.datetime.now(datetime.timezone.utc).timestamp()
    return {uid: exp for uid, exp in raw.items() if exp > now_ts}
_noprefix: dict[int, float] = _load_noprefix()
def _save_noprefix():
    try:
        _NOPREFIX_FILE.write_text(json.dumps({str(k): v for k, v in _noprefix.items()}), encoding="utf-8")
    except Exception:
        log.warning("noprefix save fail\n%s", traceback.format_exc())
def _add_months(dt: datetime.datetime, months: int) -> datetime.datetime:
    month_index=dt.month - 1 + months
    year=dt.year + month_index // 12
    month=month_index % 12 + 1
    day=min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)
def _noprefix_expiry(user_id: int):
    exp=_noprefix.get(user_id)
    if exp is None: return None
    expiry=datetime.datetime.fromtimestamp(exp, datetime.timezone.utc)
    if expiry <= datetime.datetime.now(datetime.timezone.utc):
        _noprefix.pop(user_id, None); _save_noprefix(); return None
    return expiry
def _noprefix_active(user_id: int) -> bool:
    return _noprefix_expiry(user_id) is not None
async def _get_bot_owner_id() -> int:
    if BOT_OWNER_ID: return BOT_OWNER_ID
    if getattr(bot, "owner_id", None): return int(bot.owner_id)
    try:
        info=await bot.application_info()
        bot.owner_id=info.owner.id
        return int(info.owner.id)
    except Exception:
        log.error("bot owner check fail\n%s", traceback.format_exc())
        return 0


def _is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator) if interaction.guild else False

def _is_mod(interaction: discord.Interaction) -> bool:
    p = interaction.user.guild_permissions if interaction.guild else None
    return bool(p and (p.administrator or p.manage_guild or p.manage_messages or p.kick_members or p.ban_members))

async def _deny(interaction: discord.Interaction, msg="❌ This command is only for admins/mods."):
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`")

def _help_command_total() -> int:
    total=0
    for cmd in bot.tree.get_commands():
        subs=getattr(cmd, "commands", None)
        total += len(subs) if subs else 1
    return total

_HELP_CATEGORIES={
    "utility": ("⚡", "Utility", "⚡ Utility Commands",
        "`/ping` `/botinfo` `/uptime` `/serverinfo` `/userinfo` `/avatar` `/membercount` `/roleinfo` `/roles` `/banner` `/channelinfo` `/emojiinfo`\n\n"
        "**/tools**: timestamp, snowflake, uuid, random, b64encode, b64decode, sha256, binary, hex, unit, coinamount, health, ticketpanel, youtube, instagram, embed\n"
        "**/text**: reverse, upper, lower, title, count, repeat, spoiler, mock, vowels, slug\n\n"
        "`/calc` `/qr` `/color` `/password` `/invite` `/support`"),
    "fun": ("😄", "Fun", "😄 Fun Commands",
        "**/actions** (with GIFs): hug, pat, slap, wave, highfive, dance, cry, laugh, angry, sleep, eat, thumbsup\n\n"
        "**/games**: trivia, trivia_answer, riddle, riddle_answer, scramble, scramble_guess, mathquiz, math_answer, numberguess, guess, slots, **royale**, **idp**\n\n"
        "`/coinflip` `/dice` `/choose` `/eightball` `/rps` `/joke` `/fact` `/quote` `/afk` `/snipe` `/timer`"),
    "mod": ("🛡️", "Mod & Protect", "🛡️ Mod & Protect Commands",
        "`/kick` `/ban` `/unban` `/timeout` `/untimeout` `/warn` `/warnings` `/clearwarn` `/clear` `/slowmode` `/lock` `/unlock`\n\n"
        "`/role_add` `/role_remove` `/nickname` `/autorole_bot_add` `/autorole_bot_remove` `/autorole_bot_status` `/setmodlog`\n\n"
        "**Manage everything in one command:** `/server settings` — Welcome, AntiLink, AntiSpam, BadWords, AntiRaid and AntiNuke with Status / On / Off\n**Reports:** `/report` for members, `/server reports` for moderators\n**Scrims:** `/server scrims` — create scrims, reserve slots and post slot lists\n**Protection:** `/antilink` `/antispam` `/badwords` `/antiraid` `/antinuke` `/nuke` `/lockdown` `/quarantine` `/unquarantine` `/purge` `/allnuke` (owner only)"),
    "economy": ("💰", "Economy", "💰 Economy Commands",
        "`/balance` `/daily` `/pay` `/gamble` `/rank` `/leaderboard` `/server badge`\n\n"
        "**/games slots** — bet Nezu Coins on the slot machine\n"
        "`/giveaway` — admin/mod giveaway\n"
        "`/remind` `/reminders` `/remind_cancel` — reminders"),
    "anime": ("🌸", "Anime", "🌸 Anime, AI & Voice Commands",
        "`/ask` — ask Nezu AI anything\n"
        "`/chatbot` — conversational chat mode (On / Off / Status); Nezu chats when tagged\n"
        "`/image` — generate an image\n"
        "`/summarize` — summarize text\n\n"
        "**Anime actions:** `/actions hug`, `/actions pat`, `/actions dance`, `/actions wave` and more — all with GIFs\n\n"
        "**Music & Voice:** `/play` `/p` `/join` `/skip` `/stop` `/pause` `/resume` `/queue` `/nowplaying` `/autoplay` `/vcchat`"),
    "mystic": ("🎭", "Mystic", "🎭 Mystic & Luck Commands",
        "`/eightball` — ask the magic 8-ball\n"
        "`/choose` — Nezu chooses for you\n"
        "`/coinflip` `/dice` `/rps` — luck games\n"
        "`/fact` `/quote` `/joke` — something new every time\n"
        "`/color` `/password` — random color and password\n"
        "**/games**: numberguess, guess, mathquiz, scramble, trivia, **royale**"),
    "welcome": ("📝", "Welcome", "📝 Welcome, Tickets & Server Commands",
        "🌸 **Auto-Welcome is ON by default** — new members are welcomed automatically, no setup needed. Control it with `/server welcome`.\n\n"
        "**Tickets:** `/ticket` `/close` `/tools ticketpanel` — post the HELP / BUG REPORT / IDEA panel\n\n"
        "`/say` `/announce` `/dm` `/poll` `/report` `/rules` `/suggest`\n\n"
        "**/server**: stats, icon, serverbanner, emojis, rolescount, channels, boosts, owner, created, afk, verification, welcome, features"),
}

def _help_home_embed() -> discord.Embed:
    total=_help_command_total()
    embed=discord.Embed(title="📚 Command Panel", color=0xff6b9d)
    embed.description=(
        "```\n"
        "╔══════════════════════════════════╗\n"
        "║        NEZU COMMAND PANEL        ║\n"
        f"║      {total} COMMANDS • 7 CATEGORIES      ║\n"
        "╚══════════════════════════════════╝\n"
        "```\n"
        "🔘 **Pick a category below to browse every command!**\n"
        "💡 Tip: type `/` in chat and search instantly."
    )
    if bot.user is not None:
        try: embed.set_thumbnail(url=bot.user.display_avatar.url)
        except Exception: pass
    embed.set_footer(text=f"NEZU • {total} commands and counting")
    return embed

def _help_category_embed(key: str) -> discord.Embed:
    _emoji, _label, title, body=_HELP_CATEGORIES[key]
    total=_help_command_total()
    embed=discord.Embed(title=title, color=0xff6b9d, description=body)
    if bot.user is not None:
        try: embed.set_thumbnail(url=bot.user.display_avatar.url)
        except Exception: pass
    embed.set_footer(text=f"NEZU • {total} commands and counting • Press Home for all categories")
    return embed

class HelpPanelView(discord.ui.View):
    """Screenshot jaisa /help Command Panel — 7 category buttons, sirf chalane wala hi ghumayega."""
    def __init__(self, user_id: int):
        super().__init__(timeout=600)
        self.user_id=int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This is not your help panel. Open your own with `/help`.", ephemeral=True)
            return False
        return True

    async def _show(self, interaction: discord.Interaction, key: str):
        await interaction.response.edit_message(embed=_help_category_embed(key), view=self)

    @discord.ui.button(label="Utility", style=discord.ButtonStyle.primary, emoji="⚡", row=0)
    async def btn_utility(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "utility")

    @discord.ui.button(label="Fun", style=discord.ButtonStyle.primary, emoji="😄", row=0)
    async def btn_fun(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "fun")

    @discord.ui.button(label="Mod & Protect", style=discord.ButtonStyle.primary, emoji="🛡️", row=0)
    async def btn_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "mod")

    @discord.ui.button(label="Economy", style=discord.ButtonStyle.primary, emoji="💰", row=0)
    async def btn_economy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "economy")

    @discord.ui.button(label="Anime", style=discord.ButtonStyle.success, emoji="🌸", row=1)
    async def btn_anime(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "anime")

    @discord.ui.button(label="Mystic", style=discord.ButtonStyle.success, emoji="🎭", row=1)
    async def btn_mystic(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "mystic")

    @discord.ui.button(label="Welcome", style=discord.ButtonStyle.success, emoji="📝", row=1)
    async def btn_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, "welcome")

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, emoji="🏠", row=2)
    async def btn_home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_help_home_embed(), view=self)

@bot.tree.command(name="help", description="Show the Nezu command panel — browse commands by category")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_help_home_embed(), view=HelpPanelView(interaction.user.id))

@bot.tree.command(name="botinfo", description="Show bot information")
async def botinfo(interaction: discord.Interaction):
    embed=discord.Embed(title="🤖 Bot Info", color=0x3498db)
    embed.add_field(name="Name", value=str(bot.user), inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)}ms", inline=True)
    embed.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="uptime", description="Show how long the bot has been online")
async def uptime(interaction: discord.Interaction):
    sec=int(time.time()-START_TIME); d,sec=divmod(sec,86400); h,sec=divmod(sec,3600); m,sec=divmod(sec,60)
    await interaction.response.send_message(f"⏱️ Bot has been online for: **{d}d {h}h {m}m {sec}s** se", ephemeral=True)

@bot.tree.command(name="serverinfo", description="Show server information")
async def serverinfo(interaction: discord.Interaction):
    g=interaction.guild
    if g is None: await interaction.response.send_message("This only works inside a server.", ephemeral=True); return
    embed=discord.Embed(title=f"🏰 {g.name}", color=0x2ecc71)
    embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
    embed.add_field(name="Members", value=str(g.member_count), inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Created", value=g.created_at.strftime("%d %b %Y"), inline=True)
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="Show a member's information")
@app_commands.describe(member="Which member do you want info about?")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    embed=discord.Embed(title=f"👤 {member.display_name}", color=0xf39c12)
    embed.add_field(name="Username", value=str(member), inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Server join", value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "-", inline=True)
    embed.add_field(name="Account bana", value=member.created_at.strftime("%d %b %Y"), inline=True)
    roles=[r.mention for r in member.roles if r.name!="@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles)[:1000] or "No roles", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Show a member's avatar (large)")
@app_commands.describe(member="Whose avatar do you want?")
async def avatar(interaction: discord.Interaction, member: discord.Member):
    embed=discord.Embed(title=f"🖼️ {member.display_name}'s avatar", color=0x9b59b6)
    embed.set_image(url=member.display_avatar.with_size(1024).url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="membercount", description="Show total member count")
async def membercount(interaction: discord.Interaction):
    if interaction.guild is None: await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    await interaction.response.send_message(f"👥 **{interaction.guild.name}** has **{interaction.guild.member_count}** members.", ephemeral=True)

@bot.tree.command(name="roleinfo", description="Show role information")
@app_commands.describe(role="Which role do you want info about?")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed=discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="Members", value=str(len(role.members)), inline=True)
    embed.add_field(name="Position", value=str(role.position), inline=True)
    embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
    embed.add_field(name="Is admin?", value="Yes" if role.permissions.administrator else "No", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="say", description="Make the bot send a message (admin)")
@app_commands.describe(text="What should I send?", channel="Which channel? (blank = this channel)")
async def say(interaction: discord.Interaction, text: str, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.send(text)
    await interaction.response.send_message(f"✅ Sent to {ch.mention}", ephemeral=True)

@bot.tree.command(name="announce", description="Send an embed announcement (admin)")
@app_commands.describe(title="Title", description="Announcement", channel="Which channel? (blank = this channel)")
async def announce(interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    embed=discord.Embed(title=title, description=description, color=0x9b59b6)
    embed.set_footer(text=f"Announcement by {interaction.user.display_name}")
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ Announcement sent to {ch.mention}", ephemeral=True)

@bot.tree.command(name="dm", description="Send a DM to a member via the bot (admin)")
@app_commands.describe(member="Who should receive the DM?", message="What should the DM say?")
async def dm_cmd(interaction: discord.Interaction, member: discord.Member, message: str):
    if not _is_mod(interaction): await _deny(interaction); return
    try:
        await member.send(f"📩 **{interaction.guild.name}** se message:\n\n{message}")
        await interaction.response.send_message(f"✅ DM sent to {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ {member.mention} has DMs closed.", ephemeral=True)

@bot.tree.command(name="clear", description="Delete channel messages (admin)")
@app_commands.describe(amount="How many messages should be deleted? (1-100)")
async def clear(interaction: discord.Interaction, amount: int):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 1 <= amount <= 100: await interaction.response.send_message("❌ Choose a number between 1 and 100.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try: deleted=await interaction.channel.purge(limit=amount)
    except Exception: await interaction.followup.send("❌ I could not delete messages — I need the **Manage Messages + Read Message History** permissions.", ephemeral=True); return
    await interaction.followup.send(f"🗑️ {len(deleted)} messages delete kiye.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set channel slowmode (admin)")
@app_commands.describe(seconds="Slowmode in seconds (0 = off)", channel="Which channel? (blank = this one)")
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 0 <= seconds <= 21600: await interaction.response.send_message("❌ Choose between 0 and 21600.", ephemeral=True); return
    ch=channel or interaction.channel
    try: await ch.edit(slowmode_delay=seconds)
    except Exception: await interaction.response.send_message("❌ I could not set slowmode — I need the **Manage Channels** permission.", ephemeral=True); return
    await interaction.response.send_message(f"🐌 Slowmode in {ch.mention} is now **{seconds}s**.", ephemeral=True)

@bot.tree.command(name="lock", description="Lock this channel (admin)")
@app_commands.describe(channel="Which channel should be locked? (blank = this one)")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    try: await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    except Exception: await interaction.response.send_message("❌ I could not lock the channel — I need the **Manage Channels/Roles** permission.", ephemeral=True); return
    await ch.send("🔒 This channel has been locked.")
    await interaction.response.send_message(f"🔒 {ch.mention} is now locked.", ephemeral=True)

@bot.tree.command(name="unlock", description="Unlock this channel (admin)")
@app_commands.describe(channel="Which channel should be unlocked? (blank = this one)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    try: await ch.set_permissions(interaction.guild.default_role, send_messages=None)
    except Exception: await interaction.response.send_message("❌ I could not unlock the channel — I need the **Manage Channels/Roles** permission.", ephemeral=True); return
    await interaction.response.send_message(f"🔓 {ch.mention} is now unlocked.", ephemeral=True)

@bot.tree.command(name="kick", description="Kick a member (admin)")
@app_commands.describe(member="Who should be kicked?", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    if not interaction.user.guild_permissions.kick_members: await _deny(interaction); return
    try: await member.kick(reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ I could not do that — my role is below that member.", ephemeral=True); return
    await interaction.response.send_message(f"👢 Kicked {member.mention}. Reason: {reason}")

@bot.tree.command(name="ban", description="Ban a member (admin)")
@app_commands.describe(member="Who should be banned?", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    if not interaction.user.guild_permissions.ban_members: await _deny(interaction); return
    try: await member.ban(reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ I could not do that — my role is below that member.", ephemeral=True); return
    await interaction.response.send_message(f"🔨 Banned {member.mention}. Reason: {reason}")

@bot.tree.command(name="unban", description="Unban by user ID (admin)")
@app_commands.describe(user_id="Discord user ID to unban")
async def unban(interaction: discord.Interaction, user_id: str):
    if not interaction.user.guild_permissions.ban_members: await _deny(interaction); return
    try: uid=int(user_id)
    except ValueError: await interaction.response.send_message("❌ Give a valid user ID (numbers only).", ephemeral=True); return
    try:
        await interaction.guild.unban(discord.Object(id=uid))
        await interaction.response.send_message(f"✅ Removed the ban for user `{uid}`.", ephemeral=True)
    except discord.NotFound: await interaction.response.send_message("❌ No ban was found for this ID.", ephemeral=True)
    except discord.Forbidden: await interaction.response.send_message("❌ I do not have permission.", ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout (mute) a member (admin)")
@app_commands.describe(member="Who should be timed out?", minutes="How many minutes? (1-40320)", reason="Reason")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason given"):
    if not interaction.user.guild_permissions.moderate_members: await _deny(interaction); return
    if not 1 <= minutes <= 40320: await interaction.response.send_message("❌ Choose between 1 and 40320 minutes.", ephemeral=True); return
    try: await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ I could not do that — my role is too low.", ephemeral=True); return
    await interaction.response.send_message(f"🔇 {member.mention} was timed out for **{minutes} minute(s)**. Reason: {reason}")

@bot.tree.command(name="untimeout", description="Remove a member's timeout (admin)")
@app_commands.describe(member="Whose timeout should be removed?")
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members: await _deny(interaction); return
    try: await member.timeout(None)
    except discord.Forbidden: await interaction.response.send_message("❌ I could not do that — my role is below that member.", ephemeral=True); return
    await interaction.response.send_message(f"🔊 Removed the timeout for {member.mention}.", ephemeral=True)

@bot.tree.command(name="warn", description="Warn a member (admin)")
@app_commands.describe(member="Who should be warned?", reason="Reason")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.setdefault(interaction.guild.id, {}).setdefault(member.id, []).append(reason)
    _save_warnings()
    total=len(_warnings[interaction.guild.id][member.id])
    try: await member.send(f"⚠️ You were warned in **{interaction.guild.name}**: {reason} (total warnings: {total})")
    except discord.Forbidden: pass
    await interaction.response.send_message(f"⚠️ Warned {member.mention} (**total {total}**). Reason: {reason}")

@bot.tree.command(name="warnings", description="View a member's warnings")
@app_commands.describe(member="Which member's warnings do you want to see?")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    lst=_warnings.get(interaction.guild.id, {}).get(member.id, [])
    if not lst: await interaction.response.send_message(f"✅ {member.mention} has no warnings.", ephemeral=True); return
    txt="\n".join(f"{i+1}. {r}" for i,r in enumerate(lst))
    await interaction.response.send_message(f"⚠️ **{member.display_name}'s** warnings ({len(lst)}):\n{txt}", ephemeral=True)

@bot.tree.command(name="clearwarn", description="Clear all warnings of a member (admin)")
@app_commands.describe(member="Which member's warnings should be cleared?")
async def clearwarn(interaction: discord.Interaction, member: discord.Member):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.get(interaction.guild.id, {}).pop(member.id, None); _save_warnings()
    await interaction.response.send_message(f"✅ Cleared all warnings for {member.mention}.", ephemeral=True)

@bot.tree.command(name="role_add", description="Give a role to a member (admin)")
@app_commands.describe(member="Who should receive the role?", role="Which role should be given?")
async def role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles: await _deny(interaction); return
    try: await member.add_roles(role, reason=f"role_add by {interaction.user}")
    except discord.Forbidden: await interaction.response.send_message("❌ That role is above my role — move my role above it and try again.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} received the **{role.name}** role.")

@bot.tree.command(name="role_remove", description="Remove a role from a member (admin)")
@app_commands.describe(member="Who should lose the role?", role="Which role should be removed?")
async def role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles: await _deny(interaction); return
    try: await member.remove_roles(role, reason=f"role_remove by {interaction.user}")
    except discord.Forbidden: await interaction.response.send_message("❌ That role is above my role.", ephemeral=True); return
    await interaction.response.send_message(f"✅ Removed the **{role.name}** role from {member.mention}.")

@bot.tree.command(name="nickname", description="Change a member's nickname (admin)")
@app_commands.describe(member="Whose nickname should be changed?", name="New nickname (blank = remove the old one)")
async def nickname_cmd(interaction: discord.Interaction, member: discord.Member, name: str = None):
    if not interaction.user.guild_permissions.manage_nicknames: await _deny(interaction); return
    try: await member.edit(nick=name)
    except discord.Forbidden: await interaction.response.send_message("❌ I could not do that — my role is too low.", ephemeral=True); return
    await interaction.response.send_message(f"✅ Changed the nickname of {member.mention}.", ephemeral=True)

@bot.tree.command(name="autorole_bot_add", description="Set the role every new member gets automatically on join (admin)")
@app_commands.describe(role="Which role should new members get automatically?")
async def autorole_bot_add(interaction: discord.Interaction, role: discord.Role):
    if not _is_mod(interaction): await _deny(interaction); return
    if interaction.guild is None: await interaction.response.send_message("❌ This works only inside a server.", ephemeral=True); return
    if role.id == interaction.guild.id: await interaction.response.send_message("❌ The @everyone role cannot be used as auto-role.", ephemeral=True); return
    if role.managed: await interaction.response.send_message("❌ This is a managed/integration role — please pick a normal role.", ephemeral=True); return
    me=interaction.guild.me
    if me is None or not me.guild_permissions.manage_roles: await interaction.response.send_message("❌ I need the **Manage Roles** permission for auto-role.", ephemeral=True); return
    if role.position >= me.top_role.position: await interaction.response.send_message("❌ That role is above my role. Move my role above it, then try again.", ephemeral=True); return
    _autoroles[interaction.guild.id]=role.id; _save_autoroles()
    await interaction.response.send_message(f"✅ Auto-role set! Every new member joining **{interaction.guild.name}** will now automatically get {role.mention}.")

@bot.tree.command(name="autorole_bot_remove", description="Turn off auto-role for this server (admin)")
async def autorole_bot_remove(interaction: discord.Interaction):
    if not _is_mod(interaction): await _deny(interaction); return
    if interaction.guild is not None and _autoroles.pop(interaction.guild.id, None) is not None:
        _save_autoroles()
        await interaction.response.send_message("✅ Auto-role is now **off**. New members will no longer get a role automatically.", ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ Auto-role is already off in this server.", ephemeral=True)

@bot.tree.command(name="autorole_bot_status", description="Show the current auto-role setting")
async def autorole_bot_status(interaction: discord.Interaction):
    if interaction.guild is None: await interaction.response.send_message("❌ This works only inside a server.", ephemeral=True); return
    rid=_autoroles.get(interaction.guild.id)
    role=interaction.guild.get_role(rid) if rid else None
    if role: await interaction.response.send_message(f"🎭 Auto-role is **ON** — new members automatically get {role.mention}.", ephemeral=True)
    elif rid: await interaction.response.send_message("⚠️ Auto-role is set, but that role no longer exists. Please set it again with `/autorole_bot_add`.", ephemeral=True)
    else: await interaction.response.send_message("🎭 Auto-role is **OFF**. An admin can set it with `/autorole_bot_add`.", ephemeral=True)

@bot.tree.command(name="poll", description="Create a poll (vote with reactions)")
@app_commands.describe(question="Poll question", options="Separate options with commas (e.g. Yes, No, Later)")
async def poll(interaction: discord.Interaction, question: str, options: str):
    opts=[o.strip() for o in options.split(",") if o.strip()][:5]
    if len(opts) < 2: await interaction.response.send_message("❌ Give at least 2 options, separated by commas.", ephemeral=True); return
    letters=["🇦","🇧","🇨","🇩","🇪"]
    desc="\n".join(f"{letters[i]} {o}" for i,o in enumerate(opts))
    embed=discord.Embed(title=f"📊 {question}", description=desc, color=0xe67e22)
    embed.set_footer(text=f"Poll by {interaction.user.display_name} — vote by reacting")
    await interaction.response.send_message(embed=embed)
    msg=await interaction.original_response()
    for i in range(len(opts)): await msg.add_reaction(letters[i])

# ---------------- PERSISTENT REMINDERS ----------------
_REMINDERS_FILE = Path(__file__).resolve().parent / "reminders.json"
def _load_reminders() -> list[dict]:
    try:
        data=json.loads(_REMINDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []
_reminders: list[dict] = _load_reminders()
_reminder_tasks: dict[int, asyncio.Task] = {}
def _save_reminders():
    try:
        _REMINDERS_FILE.write_text(json.dumps(_reminders), encoding="utf-8")
    except Exception:
        log.warning("reminders save fail\n%s", traceback.format_exc())
def _next_reminder_id() -> int:
    return max([int(r.get("id", 0)) for r in _reminders], default=0) + 1
async def _wait_reminder(reminder_id: int):
    try:
        while True:
            rec=next((r for r in _reminders if int(r.get("id", 0)) == reminder_id), None)
            if rec is None: return
            delay=float(rec.get("due", 0)) - datetime.datetime.now(datetime.timezone.utc).timestamp()
            if delay > 0:
                await asyncio.sleep(delay)
            rec=next((r for r in _reminders if int(r.get("id", 0)) == reminder_id), None)
            if rec is None: return
            _reminders.remove(rec); _save_reminders()
            try:
                channel=bot.get_channel(int(rec.get("channel_id", 0)))
                if channel is None:
                    channel=await bot.fetch_channel(int(rec.get("channel_id", 0)))
                await channel.send(f"⏰ <@{rec.get('user_id')}> **Reminder:** {rec.get('text', '')}")
            except Exception:
                log.warning("reminder %s bhejne me fail", reminder_id)
            return
    except asyncio.CancelledError:
        raise
    except Exception:
        log.error("reminder wait fail\n%s", traceback.format_exc())
    finally:
        task=_reminder_tasks.get(reminder_id)
        if task is asyncio.current_task():
            _reminder_tasks.pop(reminder_id, None)
def _schedule_reminder(rec: dict):
    rid=int(rec.get("id", 0))
    task=_reminder_tasks.get(rid)
    if task and not task.done(): return
    try: _reminder_tasks[rid]=asyncio.create_task(_wait_reminder(rid))
    except RuntimeError: pass
def _restore_reminders():
    for rec in list(_reminders):
        _schedule_reminder(rec)

@bot.tree.command(name="remind", description="Set a reminder (it survives bot restarts)")
@app_commands.describe(minutes="After how many minutes? (1-10080)", text="What should I remind you about?")
async def remind(interaction: discord.Interaction, minutes: int, text: str):
    if not 1 <= minutes <= 10080: await interaction.response.send_message("❌ Please choose between 1 and 10080 minutes.", ephemeral=True); return
    text=text.strip()[:500]
    if not text: await interaction.response.send_message("❌ Please write what I should remind you about.", ephemeral=True); return
    due=int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + minutes*60
    rec={"id": _next_reminder_id(), "guild_id": interaction.guild.id if interaction.guild else 0, "channel_id": interaction.channel.id if interaction.channel else 0, "user_id": interaction.user.id, "text": text, "due": due}
    _reminders.append(rec); _save_reminders(); _schedule_reminder(rec)
    await interaction.response.send_message(f"⏰ Done {interaction.user.mention} — I will remind you <t:{due}:R>: **{text}**\n🆔 Reminder ID: `{rec['id']}` (cancel with `/remind_cancel`)", ephemeral=True)

@bot.tree.command(name="reminders", description="See your pending reminders")
async def reminders_cmd(interaction: discord.Interaction):
    gid=interaction.guild.id if interaction.guild else 0
    mine=sorted([r for r in _reminders if r.get("user_id") == interaction.user.id and r.get("guild_id") == gid], key=lambda r: r.get("due", 0))
    if not mine:
        await interaction.response.send_message("ℹ️ You have no pending reminders in this server.", ephemeral=True); return
    embed=discord.Embed(title="⏰ Your Reminders", color=0xf39c12)
    for rec in mine[:10]:
        embed.add_field(name=f"ID {rec.get('id')} • <t:{int(rec.get('due', 0))}:R>", value=str(rec.get("text", ""))[:200], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remind_cancel", description="Cancel one of your reminders")
@app_commands.describe(reminder_id="Reminder ID shown by /reminders")
async def remind_cancel_cmd(interaction: discord.Interaction, reminder_id: int):
    rec=next((r for r in _reminders if int(r.get("id", 0)) == reminder_id), None)
    if rec is None:
        await interaction.response.send_message("ℹ️ No reminder found with that ID.", ephemeral=True); return
    if rec.get("user_id") != interaction.user.id and not _is_mod(interaction):
        await interaction.response.send_message("❌ You can cancel only your own reminders.", ephemeral=True); return
    _reminders.remove(rec); _save_reminders()
    task=_reminder_tasks.pop(reminder_id, None)
    if task: task.cancel()
    await interaction.response.send_message(f"✅ Reminder `{reminder_id}` cancelled.", ephemeral=True)


_ask_cooldown: dict[int, float] = {}

_GEMINI_FALLBACK_MODELS=["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3-flash-preview"]
_NEZUKO_FRIEND = (
"You are Nezu, a friendly female AI companion. Behave like a real, close female friend talking naturally "
"— NOT like a robot or an AI assistant. Be warm, caring, a little playful and expressive: react naturally, "
"remember the context of the conversation and continue it like a human would, and sometimes ask a "
"small follow-up question to keep the conversation going. Do NOT repeatedly say you are an AI/bot; only mention it if "
"directly asked. Avoid robot-like template phrases and avoid repeating the same line again and again. "
"LANGUAGE: Always reply in English only, no matter which language the user writes or speaks in. "
"Never romantic/flirty. Use at most 1-2 cute emojis (🌸 ✨) sometimes, not in every line. "
"IMPORTANT: You are a GIRL. Always use feminine grammar for yourself where the language has gendered forms; "
"never use masculine self-reference. "
"For voice (VC) replies: keep it very short and spoken-natural, 1-2 small sentences. For text chat: friendly and concise."
)
_NEZU_QA_SYSTEM = (
"You are Nezu's question-and-answer assistant. Answer the user's question directly, correctly and clearly. "
"This is NOT a conversation: do not act like a friend, do not roleplay, do not ask follow-up questions, "
"do not refer to yourself personally, and do not use conversation memory. Give only the answer to this one question. "
"Use simple, clean English. Use short paragraphs or bullets only when they help. If you are not sure, say so honestly. "
"Stay under 1800 characters."
)
def _ai_system(mode: str = "chat") -> str:
    return _NEZU_QA_SYSTEM if mode == "qa" else _NEZUKO_FRIEND

async def _refresh_ke_sath(placeholder, label: str, coro):
    """Jawab/image bante time placeholder message me har 1 second me timer update Karo."""
    if placeholder is None:
        return await coro
    shuru=time.time()
    async def _ticker():
        while True:
            await asyncio.sleep(1)
            elapsed=int(time.time() - shuru)
            try:
                await placeholder.edit(content=f"{label} ⏱️ {elapsed}s")
            except Exception:
                pass
    tick=asyncio.create_task(_ticker())
    try:
        return await coro
    finally:
        tick.cancel()

async def _ask_pollinations(prompt: str, mode: str = "chat") -> str | None:
    """Last backup AI (Pollinations, bina key ke chalta hai). Gemini fail ho tab use Karo."""
    full=(_ai_system(mode) + "\n\nUser: " + prompt[:3000])
    url="https://text.pollinations.ai/" + urllib.parse.quote(full, safe="")
    try:
        async with _sess() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=40)) as resp:
                if resp.status != 200:
                    log.warning("Pollinations text HTTP %s", resp.status)
                    return None
                text=(await resp.text()).strip()
                return text[:1900] if text else None
    except Exception:
        log.warning("Pollinations text fail\n%s", traceback.format_exc())
        return None

async def _ask_gemini(prompt: str, history=None, mode: str = "chat") -> str:
    if mode == "qa": history=None
    if not GEMINI_API_KEY:
        backup=await _ask_pollinations(prompt, mode)
        if backup: return backup
        return "⚠️ AI is not configured yet — the `GEMINI_API_KEY` is missing."
    payload={
        "system_instruction": {"parts": [{"text": _ai_system(mode) + " Keep answers helpful and under 1800 characters."}]},
        "contents": (history or []) + [{"role": "user", "parts": [{"text": prompt[:4000]}]}],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.3 if mode == "qa" else 0.7},
    }
    models=[]
    for m in [GEMINI_MODEL] + _GEMINI_FALLBACK_MODELS:
        if m and m not in models: models.append(m)
    last_status=0
    try:
        async with _sess() as session:
            for model in models:
                url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                try:
                    async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                        data=await resp.json()
                        if resp.status == 200:
                            return data["candidates"][0]["content"]["parts"][0]["text"][:1900]
                        last_status=resp.status
                        log.warning("Gemini HTTP %s (model %s): %s", resp.status, model, str(data)[:200])
                        if resp.status in (400, 401, 403):
                            break  # key/request galat hai — dusra model bhi nahi chalega
                        # 429/404/5xx par agla backup model try karo
                except Exception:
                    log.warning("Gemini model %s fail\n%s", model, traceback.format_exc())
                    continue
    except Exception:
        log.error("Gemini fail\n%s", traceback.format_exc())
        backup=await _ask_pollinations(prompt, mode)
        if backup: return backup
        return "⚠️ Could not reach the AI — please try again in a little while."
    # Gemini ke saare models fail — last backup (bina key wala AI) try Karo
    backup=await _ask_pollinations(prompt, mode)
    if backup: return backup
    if last_status == 429: return "⚠️ The AI free limit is over for now — please try again in a little while."
    if last_status == 503: return "⚠️ The AI is busy right now — please try again in a minute."
    return "⚠️ The AI could not answer right now — please try again in a little while."


async def _ask_gemini_once(prompt: str, history=None, timeout: float = 8.0, mode: str = "chat") -> str | None:
    if mode == "qa": history=None
    """Ek hi tez Gemini model se ek try — race ke liye."""
    if not GEMINI_API_KEY: return None
    payload={
        "system_instruction": {"parts": [{"text": _ai_system(mode) + " Keep answers helpful and under 1800 characters."}]},
        "contents": (history or []) + [{"role": "user", "parts": [{"text": prompt[:4000]}]}],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.3 if mode == "qa" else 0.7},
    }
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    try:
        async with _sess() as session:
            async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    log.warning("Gemini-fast HTTP %s", resp.status)
                    return None
                data=await resp.json()
                t=(data["candidates"][0]["content"]["parts"][0]["text"] or "").strip()
                return t[:1900] if t else None
    except Exception:
        log.warning("Gemini-fast fail\n%s", traceback.format_exc())
        return None

async def _ask_ai_fast(prompt: str, history=None, timeout: float = 16.0, mode: str = "chat") -> str:
    if mode == "qa": history=None
    """Sabse tez acha jawab: Gemini aur backup ko ek saath daudao —
    jo pehle sahi jawab de, wahi lo, dusre ko wahi rok do. Isiliye jawab jaldi aata hai."""
    # backup (Pollinations) ko purani baatein bhi samjhao taaki dost jaisi continuity rahe
    pol_prompt=prompt
    if history:
        lines=[]
        for h in history[-6:]:
            try:
                txt=h["parts"][0]["text"]
                lines.append(("User: " if h.get("role")=="user" else "Nezu: ") + txt[:400])
            except Exception: pass
        if lines:
            pol_prompt="Our conversation so far:\n" + "\n".join(lines) + "\n\nUser (just said): " + prompt
    tasks={}
    if GEMINI_API_KEY:
        tasks[asyncio.create_task(_ask_gemini_once(prompt, history, timeout=min(9.0, timeout), mode=mode))]="gemini"
    tasks[asyncio.create_task(_ask_pollinations(pol_prompt, mode))]="backup"
    deadline=time.time()+timeout
    try:
        pending=set(tasks)
        while pending and time.time() < deadline:
            done, pending = await asyncio.wait(pending, timeout=max(0.1, deadline-time.time()), return_when=asyncio.FIRST_COMPLETED)
            if not done: break
            for t in done:
                try: r=t.result()
                except Exception: r=None
                if r and len(r.strip())>=2 and not r.startswith("⚠️"):
                    for bachi in pending: bachi.cancel()
                    log.info("AI jawab source: %s", tasks[t])
                    return r
        for t in pending: t.cancel()
    except Exception:
        log.warning("ai-fast fail\n%s", traceback.format_exc())
    # dono fail — purana pura Gemini try aakhri ummeed
    try:
        return await _ask_gemini(prompt, history, mode=mode)
    except Exception:
        return "⚠️ Could not reach the AI — please try again in a little while."

# ---------------- GIVEAWAY SYSTEM ----------------
_GIVEAWAY_FILE = Path(__file__).resolve().parent / "giveaways.json"
def _load_giveaways() -> dict[int, dict]:
    try:
        return {int(k): v for k, v in json.loads(_GIVEAWAY_FILE.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}
_giveaways: dict[int, dict] = _load_giveaways()
_giveaway_tasks: dict[int, asyncio.Task] = {}
def _save_giveaways():
    try:
        _GIVEAWAY_FILE.write_text(json.dumps({str(k): v for k, v in _giveaways.items()}), encoding="utf-8")
    except Exception:
        log.warning("giveaways save fail\n%s", traceback.format_exc())

def _parse_duration(raw: str) -> int:
    """30m, 2h, 1d, 1d12h, 1w ya sirf 30 (=30 minutes) ko seconds me badlo."""
    text=str(raw or "").strip().lower().replace(" ", "")
    if not text:
        raise ValueError("Duration is empty")
    if text.isdigit():
        seconds=int(text) * 60
    else:
        units={"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
        seconds=0
        number=""
        used=False
        for ch in text:
            if ch.isdigit():
                number += ch
            elif ch in units and number:
                seconds += int(number) * units[ch]
                number=""
                used=True
            else:
                raise ValueError("Invalid duration")
        if number or not used:
            raise ValueError("Invalid duration")
    if seconds < 60:
        raise ValueError("Giveaway must be at least 1 minute long")
    if seconds > 30 * 86400:
        raise ValueError("Giveaway cannot be longer than 30 days")
    return seconds

def _format_duration(seconds: int) -> str:
    parts=[]
    for label, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        qty, seconds=divmod(int(seconds), size)
        if qty:
            parts.append(f"{qty} {label}{'s' if qty != 1 else ''}")
    return ", ".join(parts[:2]) or "0 seconds"

def _giveaway_embed(data: dict, ended: bool = False, winners: list[int] | None = None) -> discord.Embed:
    ends_at=int(data.get("ends_at", 0))
    entries=data.get("entries", [])
    prize=str(data.get("prize", "Amazing Prize"))
    if ended:
        embed=discord.Embed(
            title="🎉✦ GIVEAWAY ENDED ✦🎉",
            description=f"## 🎁 {prize}\n\n━━━━━━━━━━━━━━━━━━━━",
            color=0x95a5a6,
        )
        winner_text="\n".join(f"🏆 <@{uid}>" for uid in (winners or [])) or "😢 No valid entries"
        embed.add_field(name="🏆 Winner(s)", value=winner_text, inline=False)
        embed.add_field(name="👥 Total Entries", value=str(len(entries)), inline=True)
        embed.add_field(name="⏰ Ended", value=f"<t:{ends_at}:R>", inline=True)
    else:
        embed=discord.Embed(
            title="🎉✦ GIVEAWAY ✦🎉",
            description=(
                f"## 🎁 {prize}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🎉 **Press the green `Enter Giveaway` button below to participate!**\n"
                "🍀 One click to enter — click again to leave.\n"
                "━━━━━━━━━━━━━━━━━━━━"
            ),
            color=0xf1c40f,
        )
        embed.add_field(name="🏆 Prize", value=f"**{prize}**", inline=True)
        embed.add_field(name="👑 Winners", value=str(data.get("winners_count", 1)), inline=True)
        embed.add_field(name="👥 Entries", value=str(len(entries)), inline=True)
        embed.add_field(name="⏰ Ends In", value=f"<t:{ends_at}:R>\n<t:{ends_at}:F>", inline=True)
        embed.add_field(name="🎤 Hosted By", value=f"<@{data.get('host_id')}>", inline=True)
        embed.add_field(name="⌛ Duration", value=_format_duration(max(0, ends_at-int(datetime.datetime.now(datetime.timezone.utc).timestamp()))), inline=True)
    embed.set_footer(text="Nezu Giveaways • Good luck everyone! 🍀")
    if bot.user:
        try: embed.set_thumbnail(url=bot.user.display_avatar.url)
        except Exception: pass
    return embed

class GiveawayView(discord.ui.View):
    def __init__(self, entries: int = 0):
        super().__init__(timeout=None)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.label=f"Enter Giveaway • {entries}"

    @discord.ui.button(label="Enter Giveaway • 0", style=discord.ButtonStyle.success, emoji="🎉", custom_id="diana:giveaway_enter")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message is None:
            await interaction.response.send_message("⚠️ This giveaway message could not be found.", ephemeral=True); return
        data=_giveaways.get(interaction.message.id)
        if not data or data.get("ended"):
            await interaction.response.send_message("⚠️ This giveaway has already ended.", ephemeral=True); return
        if interaction.user.bot:
            await interaction.response.send_message("🤖 Bot accounts cannot enter giveaways.", ephemeral=True); return
        if interaction.user.id == data.get("host_id"):
            await interaction.response.send_message("🎤 The giveaway host cannot enter their own giveaway.", ephemeral=True); return
        entries=data.setdefault("entries", [])
        if interaction.user.id in entries:
            entries.remove(interaction.user.id)
            _save_giveaways()
            button.label=f"Enter Giveaway • {len(entries)}"
            await interaction.response.edit_message(embed=_giveaway_embed(data), view=self)
            await interaction.followup.send("↩️ You have left this giveaway.", ephemeral=True)
        else:
            entries.append(interaction.user.id)
            _save_giveaways()
            button.label=f"Enter Giveaway • {len(entries)}"
            await interaction.response.edit_message(embed=_giveaway_embed(data), view=self)
            await interaction.followup.send("🎉 You are in! Good luck! 🍀", ephemeral=True)

async def _end_giveaway(message_id: int):
    data=_giveaways.get(message_id)
    if not data or data.get("ended"):
        return
    _giveaway_tasks.pop(message_id, None)
    try:
        channel=bot.get_channel(int(data.get("channel_id", 0)))
        if channel is None:
            channel=await bot.fetch_channel(int(data.get("channel_id", 0)))
        message=await channel.fetch_message(message_id)
    except Exception:
        log.warning("giveaway %s ka channel/message nahi mila; record hata diya", message_id)
        _giveaways.pop(message_id, None); _save_giveaways(); return

    entries=[]
    seen=set()
    for uid in data.get("entries", []):
        try: uid=int(uid)
        except Exception: continue
        if uid in seen or uid == data.get("host_id") or (bot.user and uid == bot.user.id):
            continue
        seen.add(uid); entries.append(uid)
    data["entries"]=entries
    winner_ids=random.sample(entries, min(int(data.get("winners_count", 1)), len(entries))) if entries else []
    try:
        await message.edit(embed=_giveaway_embed(data, ended=True, winners=winner_ids), view=None)
    except Exception:
        log.error("giveaway result embed edit fail\n%s", traceback.format_exc())
    try:
        if winner_ids:
            mentions=" ".join(f"<@{uid}>" for uid in winner_ids)
            await channel.send(
                f"🎉 **GIVEAWAY RESULT!** 🎉\n\nCongratulations {mentions}! 🏆\nYou won **{data.get('prize')}**! 🎁\n\nHost: <@{data.get('host_id')}> — please contact the winner(s).",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        else:
            await channel.send(f"😢 **Giveaway ended!**\n\nNo valid entries were received for **{data.get('prize')}**, so there is no winner this time.")
    except Exception:
        log.error("giveaway result announcement fail\n%s", traceback.format_exc())
    _giveaways.pop(message_id, None); _save_giveaways()
    log.info("giveaway %s ended; winners=%s entries=%s", message_id, winner_ids, len(entries))

async def _giveaway_wait(message_id: int):
    try:
        data=_giveaways.get(message_id)
        if not data: return
        delay=max(0.0, float(data.get("ends_at", 0)) - datetime.datetime.now(datetime.timezone.utc).timestamp())
        if delay:
            await asyncio.sleep(delay)
        await _end_giveaway(message_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.error("giveaway wait fail\n%s", traceback.format_exc())
    finally:
        task=_giveaway_tasks.get(message_id)
        if task is asyncio.current_task():
            _giveaway_tasks.pop(message_id, None)

def _schedule_giveaway(message_id: int):
    task=_giveaway_tasks.get(message_id)
    if task and not task.done(): return
    try:
        _giveaway_tasks[message_id]=asyncio.create_task(_giveaway_wait(message_id))
    except RuntimeError:
        pass

def _restore_giveaways():
    for message_id, data in list(_giveaways.items()):
        if data.get("ended"):
            _giveaways.pop(message_id, None)
        else:
            _schedule_giveaway(message_id)
    _save_giveaways()

@bot.tree.command(name="giveaway", description="Start a stylish giveaway — set the prize, duration, and number of winners")
@app_commands.describe(prize="What are you giving away? (example: BGMI Royal Pass)", duration="How long? Examples: 30m, 2h, 1d, 1d12h — a plain number means minutes", winners="How many winners? (1-20)", channel="Where should the giveaway appear? Leave blank for this channel")
async def giveaway_cmd(interaction: discord.Interaction, prize: str, duration: str, winners: app_commands.Range[int, 1, 20] = 1, channel: discord.TextChannel = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Giveaways can only be started inside a server.", ephemeral=True); return
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ Only server admins/moderators can start a giveaway.", ephemeral=True); return
    prize=prize.strip()
    if not prize or len(prize) > 200:
        await interaction.response.send_message("❌ Please enter a prize name between 1 and 200 characters.", ephemeral=True); return
    try:
        seconds=_parse_duration(duration)
    except ValueError:
        await interaction.response.send_message("❌ Invalid duration. Use examples: `30m`, `2h`, `1d`, `1d12h`, `1w` — or just `30` for 30 minutes. Minimum 1 minute, maximum 30 days.", ephemeral=True); return
    target=channel or interaction.channel
    if target is None:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True); return
    me=interaction.guild.me
    perms=target.permissions_for(me) if me else None
    if perms is None or not (perms.view_channel and perms.send_messages and perms.embed_links):
        await interaction.response.send_message(f"❌ I do not have permission to post an embed giveaway in {target.mention}.", ephemeral=True); return

    await interaction.response.defer(ephemeral=True)
    ends_at=int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + seconds
    data={
        "guild_id": interaction.guild.id,
        "channel_id": target.id,
        "host_id": interaction.user.id,
        "prize": prize,
        "winners_count": int(winners),
        "ends_at": ends_at,
        "entries": [],
        "ended": False,
    }
    try:
        message=await target.send(embed=_giveaway_embed(data), view=GiveawayView(0))
    except Exception:
        log.error("giveaway start fail\n%s", traceback.format_exc())
        await interaction.followup.send("⚠️ Giveaway could not be started. Please check my channel permissions and try again.", ephemeral=True); return
    data["message_id"]=message.id
    _giveaways[message.id]=data; _save_giveaways(); _schedule_giveaway(message.id)
    await interaction.followup.send(f"✅ **Giveaway started!** 🎉\n🎁 Prize: **{prize}**\n⏰ Duration: **{_format_duration(seconds)}**\n🏆 Winners: **{winners}**\n📍 {message.jump_url}", ephemeral=True)

@bot.tree.command(name="ask", description="Ask one question and get a direct AI answer (no chat mode)")
@app_commands.describe(question="Your question")
async def ask_cmd(interaction: discord.Interaction, question: str):
    now=time.time()
    if now - _ask_cooldown.get(interaction.user.id, 0) < 5:
        await interaction.response.send_message("⏳ Wait 5 seconds, then ask again.", ephemeral=True); return
    _ask_cooldown[interaction.user.id]=now
    await interaction.response.defer()
    soch=await interaction.followup.send("🌸 **Nezu is thinking...** ✨ _(preparing your answer)_")
    # QA mode: purani baat-cheet yaad nahi rakhti — sirf is ek sawaal ka seedha jawab.
    jawab=await _refresh_ke_sath(soch, "🌸 **Nezu is thinking...** ✨", _ask_ai_fast(question, None, mode="qa"))
    try:
        await soch.edit(content=f"**{interaction.user.display_name}:** {question}\n\n{jawab}")
    except Exception:
        await interaction.followup.send(f"**{interaction.user.display_name}:** {question}\n\n{jawab}")

# ---------------- VOICE CHAT (VC me Nezuko tumse baat karegi) ----------------
def _pick_edge_voice(text: str) -> str:
    """Jawab ki bhasha/script pehchan ke usi bhasha ki ladki-awaaz chuno —
    Nezuko duniya ki lagbhag har bhasha me bolegi."""
    for ch in text:
        o=ord(ch)
        if 0x0900 <= o <= 0x097F: return "hi-IN-SwaraNeural"      # Hindi/Devanagari
        if 0x0980 <= o <= 0x09FF: return "bn-IN-TanishaaNeural"   # Bengali
        if 0x0A00 <= o <= 0x0A7F: return "pa-IN-Default"           # Punjabi (Gurmukhi)
        if 0x0A80 <= o <= 0x0AFF: return "gu-IN-DhwaniNeural"     # Gujarati
        if 0x0B80 <= o <= 0x0BFF: return "ta-IN-PallaviNeural"    # Tamil
        if 0x0C00 <= o <= 0x0C7F: return "te-IN-ShrutiNeural"     # Telugu
        if 0x0C80 <= o <= 0x0CFF: return "kn-IN-SapnaNeural"      # Kannada
        if 0x0D00 <= o <= 0x0D7F: return "ml-IN-SobhanaNeural"    # Malayalam
        if 0x0600 <= o <= 0x06FF: return "ur-IN-GulNeural"        # Urdu/Arabic script
        if 0x3040 <= o <= 0x30FF: return "ja-JP-NanamiNeural"     # Japanese
        if 0xAC00 <= o <= 0xD7AF: return "ko-KR-SunHiNeural"      # Korean
        if 0x4E00 <= o <= 0x9FFF: return "zh-CN-XiaoxiaoNeural"  # Chinese
        if 0x0400 <= o <= 0x04FF: return "ru-RU-SvetlanaNeural"   # Russian/Cyrillic
        if 0x0E00 <= o <= 0x0E7F: return "th-TH-PremwadeeNeural" # Thai
    return "en-IN-NeerjaNeural"

async def _tts_bytes(text: str) -> bytes | None:
    """Nezuko ki soft girl voice me text ko speech banao.
    edge-tts (free) bolti hai — jawab jis bhi bhasha me ho, usi ki awaaz."""
    saaf=re.sub(r"[*_`~#>\[\]()@]", "", text or "").strip()
    saaf=re.sub(r"https?://\S+", "", saaf).strip()
    if not saaf: return None
    saaf=saaf[:600]
    awaazen=[]
    for a in (_pick_edge_voice(saaf), "en-IN-NeerjaNeural", "hi-IN-SwaraNeural", "en-US-AnaNeural"):
        if a not in awaazen: awaazen.append(a)
    for awaaz in awaazen:
        if awaaz.endswith("Default"):
            continue  # pa-IN me ladki awaaz nahi — neeche wali chalegi
        try:
            import io as _io
            bio=_io.BytesIO()
            def _edge_sync(awaaz=awaaz, bio=bio):
                import edge_tts, asyncio as _aio
                async def _run():
                    comm=edge_tts.Communicate(saaf, voice=awaaz, rate="+4%", pitch="+14Hz")
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio": bio.write(chunk["data"])
                _aio.run(_run())
            await asyncio.get_event_loop().run_in_executor(None, _edge_sync)
            data=bio.getvalue()
            if data: return data
        except Exception:
            log.warning("TTS edge fail (%s)\n%s", awaaz, traceback.format_exc())
            continue
    return None

def _tts_file(mp3_bytes: bytes) -> str:
    """TTS ki mp3 file bot wale folder me banao — Termux me /tmp par likhne ki permission nahi milti."""
    import tempfile as _tf
    base=Path(__file__).resolve().parent / "tts_tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
        fd, path=_tf.mkstemp(prefix="Nezuko_tts_", suffix=".mp3", dir=str(base))
    except Exception:
        fd, path=_tf.mkstemp(prefix="Nezuko_tts_", suffix=".mp3")
    try:
        with os.fdopen(fd, "wb") as f: f.write(mp3_bytes)
    except Exception:
        try: os.close(fd)
        except Exception: pass
        raise
    return path

async def _vc_reply(message, jawab: str):
    """VC mode: bot VC me hai aur sawal wala usi VC me hai — to bolkar jawab do."""
    try:
        guild=message.guild
        if guild is None: return False
        vc=guild.voice_client
        if vc is None or not vc.is_connected(): return False
        user_voice=getattr(message.author, "voice", None)
        if not user_voice or not user_voice.channel or user_voice.channel != vc.channel: return False
        if not jawab or jawab.startswith("\u26a0\ufe0f"): return False
        if not _ensure_opus(): return False
        try:
            await message.reply(f"🎙️ {jawab}", mention_author=False)
        except Exception:
            pass
        # awaaz sirf _speak_in_vc bolegi (gaana rok ke bol, fir queue) — ek hi baar bolegi
        return await _speak_in_vc(guild, jawab)
    except Exception:
        log.warning("vc_reply fail\n%s", traceback.format_exc())
        return False

_CHATBOT_FILE = Path(__file__).resolve().parent / "chatbot_channels.json"
def _load_chatbot_state():
    """Purana format: [channel_id...] | Naya format: {channels: [...], master_on: true/false}"""
    try:
        data=json.loads(_CHATBOT_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            channels={int(x) for x in data}
            return channels, bool(channels)
        if isinstance(data, dict):
            channels={int(x) for x in data.get("channels", [])}
            return channels, bool(data.get("master_on", bool(channels)))
    except Exception:
        pass
    return set(), False
_tag_chatbot_channels, _tag_chatbot_master_on = _load_chatbot_state()
def _save_chatbot_channels():
    try:
        _CHATBOT_FILE.write_text(json.dumps({"channels": sorted(_tag_chatbot_channels), "master_on": _tag_chatbot_master_on}), encoding="utf-8")
    except Exception:
        log.warning("chatbot channels save fail\n%s", traceback.format_exc())
_memory: dict[tuple[int, int], list] = {}
_chat_memory: dict[tuple[int, int], list] = {}

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if await _handle_yt_natural(message): return
    await _process_afk_message(message)
    if await _check_antilink(message):
        return
    if await _check_antispam(message):
        return
    if await _check_badwords(message):
        return
    await _award_xp(message)
    is_mention = bool(bot.user and bot.user in message.mentions)
    is_tag_chatbot = _tag_chatbot_master_on and message.channel.id in _tag_chatbot_channels
    is_noprefix = _noprefix_active(message.author.id)
    if (is_mention and is_tag_chatbot) or is_noprefix:
        if is_mention:
            sawal=message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        else:
            sawal=message.content.strip()
            if sawal.startswith(("!", "/")): sawal=""
        if sawal:
            now=time.time()
            if now - _ask_cooldown.get(message.author.id, 0) >= 5:
                _ask_cooldown[message.author.id]=now
                hist=_chat_memory.setdefault((message.guild.id if message.guild else 0, message.author.id), [])
                soch=None
                try:
                    soch=await message.reply("🌸 **Nezu is thinking...** ✨ _(preparing your answer)_", mention_author=False)
                except Exception:
                    pass
                async with message.channel.typing():
                    jawab=await _refresh_ke_sath(soch, "🌸 **Nezu is thinking...** ✨", _ask_ai_fast(sawal, hist, mode="chat"))
                hist += [{"role":"user","parts":[{"text":sawal[:2000]}]}, {"role":"model","parts":[{"text":jawab[:2000]}]}]
                del hist[:-6]
                boolegi=False
                try:
                    boolegi=await _vc_reply(message, jawab)
                except Exception:
                    boolegi=False
                if soch is not None:
                    try:
                        await soch.edit(content=jawab)
                    except Exception:
                        if not boolegi: await message.reply(jawab)
                elif not boolegi:
                    await message.reply(jawab)
    await bot.process_commands(message)

@bot.tree.command(name="chatbot", description="Conversational chat mode for this channel: Nezu chats when mentioned (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — conversational chat when Nezu is tagged", value="on"),
    app_commands.Choice(name="Off — stop tag replies everywhere", value="off"),
    app_commands.Choice(name="Status — check whether tag chat is on", value="status"),
])
async def chatbot_cmd(interaction: discord.Interaction, action: str = "on"):
    global _tag_chatbot_master_on
    if interaction.channel is None:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    if action == "on":
        if _tag_chatbot_master_on and interaction.channel.id in _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Tag chat is already **ON** in this channel. Mention Nezu with your question and she will reply.", ephemeral=True); return
        _tag_chatbot_master_on=True
        _tag_chatbot_channels.add(interaction.channel.id); _save_chatbot_channels()
        log.info("chatbot: tag chat ON in channel %s by %s", interaction.channel.id, interaction.user)
        await interaction.response.send_message("🤖 **Chatbot ON — chat mode!** ✅\nFrom now on, Nezu will hold a friendly conversation in this channel **whenever someone tags/mentions her**, and she will remember the recent conversation.\nExample: `@Nezu hey, how are you?`\n\nFor a single direct answer instead, use `/ask`. Use `/chatbot` → **Off** to stop chat mode everywhere.")
    elif action == "off":
        if not _tag_chatbot_master_on and not _tag_chatbot_channels:
            await interaction.response.send_message("ℹ️ Chatbot is already OFF everywhere.", ephemeral=True); return
        _tag_chatbot_master_on=False
        _tag_chatbot_channels.clear(); _save_chatbot_channels()
        log.info("chatbot: tag chat OFF everywhere by %s", interaction.user)
        await interaction.response.send_message("🤖 **Chatbot is OFF everywhere.** ✅\nNezu will no longer reply when tagged in any channel. To turn it back on, run `/chatbot` in a channel and choose **On**.", ephemeral=True)
    else:
        if _tag_chatbot_master_on and interaction.channel.id in _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Chatbot chat mode is **ON** in this channel — Nezu chats and remembers the conversation when she is tagged/mentioned. For one direct answer, use `/ask`.", ephemeral=True)
        elif _tag_chatbot_master_on and _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Tag chat is ON in another channel, but **OFF in this channel**.", ephemeral=True)
        else:
            await interaction.response.send_message("🤖 Chatbot is **OFF everywhere**. An admin can start it with `/chatbot`.", ephemeral=True)

@bot.tree.command(name="noprefix", description="Owner only: grant, remove, or check No Prefix power (lasts 2 months)")
@app_commands.describe(action="Add, remove, or check No Prefix power", member="Select the member")
@app_commands.choices(action=[
    app_commands.Choice(name="Add / renew (2 months)", value="add"),
    app_commands.Choice(name="Remove", value="remove"),
    app_commands.Choice(name="Status", value="status"),
])
async def noprefix_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], member: discord.Member):
    owner_id=await _get_bot_owner_id()
    if interaction.user.id != owner_id:
        await interaction.response.send_message("❌ Only the **bot owner** can use `/noprefix`.", ephemeral=True); return
    op=action.value
    if op == "add":
        if member.bot:
            await interaction.response.send_message("❌ No Prefix power cannot be given to a bot account.", ephemeral=True); return
        expiry=_add_months(datetime.datetime.now(datetime.timezone.utc), 2)
        _noprefix[member.id]=expiry.timestamp(); _save_noprefix()
        await interaction.response.send_message(f"👑 No Prefix power given to {member.mention}.\n⏳ It will expire automatically on **{expiry.strftime('%d %b %Y')}**.\nNow they can simply type a message/question — no `/ask`, mention, or command prefix is needed.", ephemeral=True)
    elif op == "remove":
        if _noprefix.pop(member.id, None) is not None:
            _save_noprefix()
            await interaction.response.send_message(f"✅ No Prefix power removed from {member.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {member.mention} does not currently have No Prefix power.", ephemeral=True)
    else:
        expiry=_noprefix_expiry(member.id)
        if expiry:
            days=max(0, (expiry - datetime.datetime.now(datetime.timezone.utc)).days)
            await interaction.response.send_message(f"👑 {member.mention} has No Prefix power until **{expiry.strftime('%d %b %Y')}** ({days} days left).", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {member.mention} does not currently have No Prefix power.", ephemeral=True)

_image_cooldown: dict[int, float] = {}

async def _make_image(prompt: str) -> bytes:
    """Free image generation (Pollinations) — koi key nahi chahiye. Free limit: ~1 image/45s, isliye 402 pe ek baar ruk ke dobara."""
    url="https://image.pollinations.ai/prompt/"+urllib.parse.quote(prompt)+f"?width=1024&height=1024&nologo=true&seed={random.randint(1,999999)}"
    async with _sess() as session:
        for attempt in range(2):
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status == 200:
                    data=await resp.read()
                    if len(data) < 5000:
                        raise RuntimeError("image bohot chhoti aayi")
                    return data
                body=(await resp.text())[:200]
                log.warning("image HTTP %s attempt %s: %s", resp.status, attempt, body)
                if resp.status != 402 or attempt == 1:
                    raise RuntimeError(f"image HTTP {resp.status}")
            await asyncio.sleep(46)

@bot.tree.command(name="image", description="Generate an AI image — describe it")
@app_commands.describe(vivaran="What should appear in the image? (e.g. a BGMI soldier at sunset)")
async def image_cmd(interaction: discord.Interaction, vivaran: str):
    now=time.time()
    if now - _image_cooldown.get(interaction.user.id, 0) < 15:
        await interaction.response.send_message("⏳ Images take time — wait 15 seconds, then try again.", ephemeral=True); return
    _image_cooldown[interaction.user.id]=now
    await interaction.response.defer()
    ban_rahi=await interaction.followup.send("🎨 **Nezu is creating your image...** 🌸 _(this may take a little while)_")
    try:
        data=await _refresh_ke_sath(ban_rahi, "🎨 **Nezu is creating your image...** 🌸", _make_image(vivaran+", high quality, detailed, vibrant"))
    except Exception:
        log.error("image fail\n%s", traceback.format_exc())
        try: await ban_rahi.edit(content="⚠️ I could not create the image — please try again in a little while.")
        except Exception: await interaction.followup.send("⚠️ I could not create the image — please try again in a little while.")
        return
    try:
        await ban_rahi.edit(content=f"🎨 For **{interaction.user.display_name}**: {vivaran}", attachments=[discord.File(io.BytesIO(data), filename="ai-image.jpg")])
    except Exception:
        await interaction.followup.send(content=f"🎨 For **{interaction.user.display_name}**: {vivaran}", file=discord.File(io.BytesIO(data), filename="ai-image.jpg"))

@bot.tree.command(name="report", description="Report a member to the admins")
@app_commands.describe(member="Who are you reporting?", reason="What did they do wrong?")
async def report(interaction: discord.Interaction, member: discord.Member, reason: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Reports work only inside a server.", ephemeral=True); return
    rid=0
    try:
        bucket=_reports.setdefault(str(interaction.guild.id), {"next_id": 1, "items": []})
        rid=int(bucket.get("next_id", 1)); bucket["next_id"]=rid+1
        bucket.setdefault("items", []).append({"id": rid, "member_id": member.id, "reporter_id": interaction.user.id, "reason": str(reason)[:800], "resolved": False, "created_at": datetime.datetime.now(datetime.timezone.utc).timestamp()})
        _save_reports()
    except Exception:
        log.warning("report save fail\n%s", traceback.format_exc())
    embed=discord.Embed(title="🚨 New Report", color=0xe74c3c)
    embed.add_field(name="Report ID", value=f"#{rid}", inline=True)
    embed.add_field(name="Reported member", value=member.mention, inline=True)
    embed.add_field(name="Reported by", value=interaction.user.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    # Mod-log set hai to report waha jayegi; nahi hai to saved rahegi aur /server reports se milegi.
    try:
        ch_id=_automod.get(str(interaction.guild.id), {}).get("modlog_channel")
        ch=interaction.guild.get_channel(int(ch_id)) if ch_id else None
        if ch is not None: await ch.send(embed=embed)
    except Exception: pass
    await interaction.response.send_message(f"✅ Report saved. Report ID: **#{rid}** — admins can review it with `/server reports`.", ephemeral=True)

_summarize_cooldown: dict[int, float] = {}

@bot.tree.command(name="summarize", description="Summarize recent chat in English bullet points (AI)")
@app_commands.describe(count="How many recent messages to summarize (5-50, default 20)")
async def summarize_cmd(interaction: discord.Interaction, count: int = 20):
    now=time.time()
    if now - _summarize_cooldown.get(interaction.user.id, 0) < 20:
        await interaction.response.send_message("⏳ Summaries take time — wait 20 seconds, then try again.", ephemeral=True); return
    _summarize_cooldown[interaction.user.id]=now
    if interaction.channel is None:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True); return
    count=max(5, min(50, count))
    await interaction.response.defer()
    lines=[]
    try:
        async for m in interaction.channel.history(limit=count):
            if m.author.bot or not (m.content or "").strip(): continue
            lines.append(f"{m.author.display_name}: {m.content[:200]}")
    except Exception:
        await interaction.followup.send("⚠️ I could not read the chat history — I need the Read Message History permission."); return
    lines.reverse()
    if len(lines) < 3:
        await interaction.followup.send("ℹ️ There are not enough messages in this channel to summarize."); return
    jawab=await _ask_gemini("Summarize this Discord chat in clear English bullet points (max 6 points, short):\n\n" + "\n".join(lines, mode="qa"))
    await interaction.followup.send(f"📝 **Summary of the last {len(lines)} messages:**\n\n{jawab}")

_JOKES=[
    "Why do gamers never get lost? Because they always follow the mini-map! 🎮",
    "Why did the computer go to the doctor? It had a virus! 💻",
    "Why don't scientists trust atoms? Because they make up everything! ⚛️",
    "What do you call a fish without eyes? A fsh! 🐟",
    "Why did the scarecrow win an award? Because he was outstanding in his field! 🌾",
    "Why do programmers prefer dark mode? Because light attracts bugs! 🐛",
    "What did one wall say to the other? I'll meet you at the corner! 🧱",
    "Why did the bicycle fall over? It was two-tired! 🚲",
    "How does a Discord mod sleep? They ban-ish their worries first! 😴",
    "Why was the math book sad? It had too many problems! 📚",
]
_FACTS=[
    "Honey never spoils — 3,000-year-old honey has been found still edible! 🍯",
    "An octopus has 3 hearts! 🐙",
    "Studies suggest playing video games can improve decision-making speed. 🎮",
    "Lightning is 5 times hotter than the surface of the sun! ⚡",
    "A person spends about 6 months of their life waiting at traffic lights. 🚦",
    "Sea otters hold hands while sleeping so they do not drift apart. 🦦",
    "The first computer bug was actually a moth found trapped inside a machine! 🐛",
    "Bananas and humans share about 60% of their DNA. 🍌",
    "Discord launched in 2015 and was first made for gamers. 💬",
    "Your heart beats about 100,000 times in one day. ❤️",
]
_QUOTES=[
    "\"Success is not final, failure is not fatal: it is the courage to continue that counts.\" — Winston Churchill 💪",
    "\"The grind never lies. Get a little better every day.\" 🔥",
    "\"Discipline is choosing what you want most over what you want now.\" 🎯",
    "\"You don't have to be extreme, just consistent.\" 📈",
    "\"Hard days make strong players.\" 🎮",
    "\"Dream big, start small, act now.\" 🚀",
    "\"Winners are ordinary people with extraordinary determination.\" 🏆",
    "\"Focus on progress, not perfection.\" ✨",
]

@bot.tree.command(name="joke", description="Get a random funny joke")
async def joke_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"😂 {random.choice(_JOKES)}")

@bot.tree.command(name="fact", description="Get a random interesting fact")
async def fact_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"🧠 {random.choice(_FACTS)}")

@bot.tree.command(name="quote", description="Get a motivational quote")
async def quote_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"💡 {random.choice(_QUOTES)}")

# ---------------- 20 COMMON UTILITY & FUN COMMANDS ----------------
_AFK_FILE = Path(__file__).resolve().parent / "afk.json"
def _load_afk() -> dict[str, dict]:
    try:
        data=json.loads(_AFK_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
_afk_data: dict[str, dict] = _load_afk()
_snipes: dict[int, dict] = {}
def _save_afk():
    try:
        _AFK_FILE.write_text(json.dumps(_afk_data), encoding="utf-8")
    except Exception:
        log.warning("afk save fail\n%s", traceback.format_exc())
def _afk_key(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"

async def _process_afk_message(message: discord.Message):
    """Bolne par khud ka AFK hataye, aur AFK member ko mention karne par bataye."""
    if message.guild is None:
        return
    author_key=_afk_key(message.guild.id, message.author.id)
    if author_key in _afk_data:
        _afk_data.pop(author_key, {})
        _save_afk()
        try:
            await message.channel.send(f"👋 Welcome back, {message.author.mention}! Your AFK has been removed.", delete_after=6)
        except Exception:
            pass
    seen=set()
    for member in message.mentions:
        if member.id == message.author.id or member.id in seen:
            continue
        seen.add(member.id)
        info=_afk_data.get(_afk_key(message.guild.id, member.id))
        if info:
            try:
                await message.channel.send(f"💤 {member.mention} is AFK: **{info.get('reason', 'AFK')}**", delete_after=8)
            except Exception:
                pass

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author and message.author.bot:
        return
    content=(message.content or "").strip()
    attachments=[a.url for a in getattr(message, "attachments", [])]
    if not content and attachments:
        content="📎 " + "\n".join(attachments)
    if not content:
        return
    _snipes[message.channel.id]={
        "author_id": message.author.id if message.author else 0,
        "author_name": str(message.author) if message.author else "Unknown",
        "content": content[:1800],
        "created_at": datetime.datetime.now(datetime.timezone.utc).timestamp(),
    }

_CALC_OPS={ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
def _calc_node(node):
    if isinstance(node, ast.Expression):
        return _calc_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value=_calc_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
        left=_calc_node(node.left); right=_calc_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(left) > 1000 or abs(right) > 20):
            raise ValueError("Power is too large")
        result=_CALC_OPS[type(node.op)](left, right)
        if isinstance(result, complex) or abs(result) > 10**100:
            raise ValueError("Result is too large")
        return result
    raise ValueError("Only numbers and + - * / // % ** are allowed")
def _safe_calc(expression: str):
    if len(expression) > 120:
        raise ValueError("Expression is too long")
    return _calc_node(ast.parse(expression, mode="eval"))

def _parse_hex_color(raw: str):
    text=str(raw or "").strip().lower().lstrip("#")
    if len(text) == 3 and all(ch in "0123456789abcdef" for ch in text):
        text="".join(ch*2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError("Invalid hex color")
    rgb=tuple(int(text[i:i+2], 16) for i in (0, 2, 4))
    return text, int(text, 16), rgb

@bot.tree.command(name="invite", description="Get Nezu's invite link and this server's invite")
async def invite_cmd(interaction: discord.Interaction):
    app_id=bot.application_id or (bot.user.id if bot.user else 0)
    bot_invite=f"https://discord.com/oauth2/authorize?client_id={app_id}&permissions=8&scope=bot%20applications.commands"
    embed=discord.Embed(title="💌 Invite Nezu", description="Use the links below:", color=0x9b59b6)
    embed.add_field(name="🤖 Add Nezu to a server", value=f"[Click here to invite Nezu]({bot_invite})", inline=False)
    if interaction.guild and interaction.channel:
        try:
            inv=await interaction.channel.create_invite(max_age=0, max_uses=0, reason="Invite requested from Nezu")
            embed.add_field(name="🏠 This server's invite", value=inv.url, inline=False)
        except Exception:
            embed.add_field(name="🏠 This server's invite", value="I could not create a server invite in this channel.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="support", description="Get help and support information for Nezu")
async def support_cmd(interaction: discord.Interaction):
    owner_text="the bot owner"
    try:
        info=await bot.application_info()
        owner_text=info.owner.mention
    except Exception:
        pass
    embed=discord.Embed(title="🆘 Nezu Support", color=0x3498db)
    embed.add_field(name="📋 Commands", value="Use `/help` to see every available command.", inline=False)
    embed.add_field(name="👑 Bot Owner", value=owner_text, inline=False)
    embed.add_field(name="🐞 Found a problem?", value="Use `/report` inside the server or contact the bot owner.", inline=False)
    embed.set_footer(text="Please do not share passwords, OTPs, or bot tokens with anyone.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="rules", description="Show the standard server rules")
async def rules_cmd(interaction: discord.Interaction):
    embed=discord.Embed(title="📜 Server Rules", color=0xe74c3c)
    embed.description=(
        "1️⃣ Be respectful with everyone.\n"
        "2️⃣ No spam, advertising, or self-promotion without permission.\n"
        "3️⃣ No abusive language, harassment, or hate speech.\n"
        "4️⃣ Keep content safe and appropriate for the channel.\n"
        "5️⃣ Do not share anyone's private information.\n"
        "6️⃣ Follow Discord's Terms of Service and Community Guidelines.\n"
        "7️⃣ Listen to admins and moderators."
    )
    embed.set_footer(text="Breaking the rules can lead to a warning, timeout, kick, or ban.")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="suggest", description="Send a suggestion for the server")
@app_commands.describe(idea="Write your suggestion")
async def suggest_cmd(interaction: discord.Interaction, idea: str):
    idea=idea.strip()
    if not idea or len(idea) > 1000:
        await interaction.response.send_message("❌ Please write a suggestion between 1 and 1000 characters.", ephemeral=True); return
    embed=discord.Embed(title="💡 New Suggestion", description=idea, color=0xf1c40f)
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    embed.set_footer(text="Vote with 👍 or 👎")
    await interaction.response.send_message(embed=embed)
    msg=await interaction.original_response()
    try:
        await msg.add_reaction("👍"); await msg.add_reaction("👎")
    except Exception:
        pass

@bot.tree.command(name="coinflip", description="Flip a coin — heads or tails")
async def coinflip_cmd(interaction: discord.Interaction):
    result=random.choice(["🪙 **Heads!**", "🪙 **Tails!**"])
    await interaction.response.send_message(result)

@bot.tree.command(name="dice", description="Roll one or more dice")
@app_commands.describe(sides="How many sides? (2-1000)", rolls="How many dice? (1-10)")
async def dice_cmd(interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6, rolls: app_commands.Range[int, 1, 10] = 1):
    results=[random.randint(1, sides) for _ in range(rolls)]
    text=", ".join(f"**{n}**" for n in results)
    extra=f"\n➕ Total: **{sum(results)}**" if rolls > 1 else ""
    await interaction.response.send_message(f"🎲 Dice result: {text}{extra}")

@bot.tree.command(name="choose", description="Let Nezu choose one option for you")
@app_commands.describe(options="Write options separated by commas, e.g. BGMI, Valorant, CODM")
async def choose_cmd(interaction: discord.Interaction, options: str):
    choices=[o.strip() for o in options.split(",") if o.strip()]
    if len(choices) < 2:
        await interaction.response.send_message("❌ Please give at least 2 options separated by commas.", ephemeral=True); return
    await interaction.response.send_message(f"🤔 I choose: **{random.choice(choices)}**")

@bot.tree.command(name="eightball", description="Ask the magic 8-ball a yes/no question")
@app_commands.describe(question="Ask your question")
async def eightball_cmd(interaction: discord.Interaction, question: str):
    answers=["Yes, definitely! ✅", "No, not this time. ❌", "Most likely yes. 👍", "Very doubtful. 🤔", "Absolutely! 🔥", "Better ask again later. ⏳", "Signs point to yes. ✨", "Do not count on it. ⚠️"]
    embed=discord.Embed(title="🎱 Magic 8-Ball", color=0x2c3e50)
    embed.add_field(name="❓ Question", value=question[:1000], inline=False)
    embed.add_field(name="🔮 Answer", value=random.choice(answers), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rps", description="Play Rock Paper Scissors with Nezu")
@app_commands.describe(choice="Choose rock, paper, or scissors")
@app_commands.choices(choice=[
    app_commands.Choice(name="🪨 Rock", value="rock"),
    app_commands.Choice(name="📄 Paper", value="paper"),
    app_commands.Choice(name="✂️ Scissors", value="scissors"),
])
async def rps_cmd(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    user_choice=choice.value
    bot_choice=random.choice(["rock", "paper", "scissors"])
    icons={"rock": "🪨 Rock", "paper": "📄 Paper", "scissors": "✂️ Scissors"}
    if user_choice == bot_choice:
        result="🤝 It's a tie!"
    elif (user_choice, bot_choice) in {("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")}:
        result="🏆 You win!"
    else:
        result="🤖 Nezu wins!"
    await interaction.response.send_message(f"You chose **{icons[user_choice]}**\nNezu chose **{icons[bot_choice]}**\n\n{result}")

@bot.tree.command(name="calc", description="Calculate a maths expression safely")
@app_commands.describe(expression="Example: 25*4+10 or (50+10)/2")
async def calc_cmd(interaction: discord.Interaction, expression: str):
    try:
        result=_safe_calc(expression)
    except ZeroDivisionError:
        await interaction.response.send_message("❌ Division by zero is not possible.", ephemeral=True); return
    except Exception:
        await interaction.response.send_message("❌ Invalid calculation. Use only numbers and `+ - * / // % **` with brackets.", ephemeral=True); return
    if isinstance(result, float) and result.is_integer():
        result=int(result)
    await interaction.response.send_message(f"🧮 `{expression}` = **{result}**")

@bot.tree.command(name="password", description="Generate a strong random password (only you can see it)")
@app_commands.describe(length="Password length (8-64)")
async def password_cmd(interaction: discord.Interaction, length: app_commands.Range[int, 8, 64] = 16):
    alphabet=string.ascii_letters+string.digits+"!@#$%^&*_-"
    pwd="".join(random.SystemRandom().choice(alphabet) for _ in range(length))
    await interaction.response.send_message(f"🔐 Your new password:\n`{pwd}`\n\nKeep it private and save it in a password manager.", ephemeral=True)

@bot.tree.command(name="qr", description="Create a QR code for any text or link")
@app_commands.describe(text="Text or link for the QR code")
async def qr_cmd(interaction: discord.Interaction, text: str):
    text=text.strip()
    if not text or len(text) > 1000:
        await interaction.response.send_message("❌ Please enter text/link between 1 and 1000 characters.", ephemeral=True); return
    url="https://api.qrserver.com/v1/create-qr-code/?size=350x350&margin=10&data="+urllib.parse.quote(text)
    embed=discord.Embed(title="🔳 QR Code", color=0x000000)
    embed.set_image(url=url)
    embed.set_footer(text="Scan this QR code with your phone camera.")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="color", description="Show information about a hex colour")
@app_commands.describe(hex_code="Example: FF5733 or #FF5733")
async def color_cmd(interaction: discord.Interaction, hex_code: str):
    try:
        clean, value, (r, g, b)=_parse_hex_color(hex_code)
    except ValueError:
        await interaction.response.send_message("❌ Invalid colour. Example: `FF5733`", ephemeral=True); return
    embed=discord.Embed(title=f"🎨 Colour #{clean.upper()}", color=value)
    embed.add_field(name="HEX", value=f"#{clean.upper()}", inline=True)
    embed.add_field(name="RGB", value=f"{r}, {g}, {b}", inline=True)
    embed.add_field(name="More shades", value=f"[Open colour details](https://www.colorhexa.com/{clean})", inline=False)
    embed.set_thumbnail(url=f"https://singlecolorimage.com/get/{clean}/300x300")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="emojiinfo", description="Show information about an emoji")
@app_commands.describe(emoji="Paste any emoji")
async def emojiinfo_cmd(interaction: discord.Interaction, emoji: str):
    emoji=emoji.strip()
    match=re.fullmatch(r"<(a?):([A-Za-z0-9_]+):(\d+)>", emoji)
    embed=discord.Embed(title="😀 Emoji Information", color=0xf39c12)
    if match:
        animated=bool(match.group(1)); name=match.group(2); emoji_id=int(match.group(3))
        ext="gif" if animated else "png"
        url=f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        embed.add_field(name="Name", value=f":{name}:", inline=True)
        embed.add_field(name="ID", value=str(emoji_id), inline=True)
        embed.add_field(name="Animated", value="Yes" if animated else "No", inline=True)
        embed.add_field(name="Created", value=discord.utils.snowflake_time(emoji_id).strftime("%d %b %Y"), inline=True)
        embed.set_image(url=url)
    elif emoji:
        codepoints=" ".join(f"U+{ord(ch):04X}" for ch in emoji if not ch.isspace())
        embed.add_field(name="Emoji", value=emoji[:100], inline=True)
        embed.add_field(name="Unicode", value=codepoints[:1000] or "-", inline=False)
    else:
        await interaction.response.send_message("❌ Please paste an emoji.", ephemeral=True); return
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="channelinfo", description="Show information about a channel")
@app_commands.describe(channel="Select a channel")
async def channelinfo_cmd(interaction: discord.Interaction, channel: discord.abc.GuildChannel):
    embed=discord.Embed(title=f"📺 #{getattr(channel, 'name', 'Channel')}", color=0x3498db)
    embed.add_field(name="ID", value=str(channel.id), inline=True)
    embed.add_field(name="Type", value=str(channel.type).title(), inline=True)
    embed.add_field(name="Created", value=channel.created_at.strftime("%d %b %Y"), inline=True)
    embed.add_field(name="Category", value=channel.category.name if getattr(channel, "category", None) else "None", inline=True)
    embed.add_field(name="Position", value=str(getattr(channel, "position", "-")), inline=True)
    topic=getattr(channel, "topic", None)
    if topic:
        embed.add_field(name="Topic", value=topic[:1000], inline=False)
    if isinstance(channel, discord.VoiceChannel):
        embed.add_field(name="Bitrate", value=f"{channel.bitrate//1000} kbps", inline=True)
        embed.add_field(name="User Limit", value=str(channel.user_limit or "Unlimited"), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="roles", description="Show all roles in this server")
async def roles_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command works only inside a server.", ephemeral=True); return
    roles=sorted(interaction.guild.roles, key=lambda r: r.position, reverse=True)
    lines=[f"{r.mention} — {len(r.members)} member(s)" for r in roles if r.name != "@everyone"]
    embed=discord.Embed(title=f"🎭 Roles in {interaction.guild.name}", color=0x9b59b6)
    embed.description="\n".join(lines[:50])[:4000] or "No roles found."
    if len(lines) > 50:
        embed.set_footer(text=f"Showing 50 of {len(lines)} roles.")
    else:
        embed.set_footer(text=f"Total roles: {len(lines)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="banner", description="Show a member's profile banner")
@app_commands.describe(member="Whose banner do you want to see? Leave blank for yourself")
async def banner_cmd(interaction: discord.Interaction, member: discord.Member = None):
    target=member or interaction.user
    await interaction.response.defer(ephemeral=True)
    banner_obj=getattr(target, "banner", None)
    if banner_obj is None:
        try:
            fetched=await bot.fetch_user(target.id)
            banner_obj=fetched.banner
        except Exception:
            banner_obj=None
    if banner_obj is None:
        await interaction.followup.send(f"ℹ️ {target.mention} has not set a profile banner.", ephemeral=True); return
    embed=discord.Embed(title=f"🖼️ {target.display_name}'s Banner", color=0x9b59b6)
    embed.set_image(url=banner_obj.url)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="afk", description="Set your AFK status")
@app_commands.describe(reason="Why are you AFK? (optional)")
async def afk_cmd(interaction: discord.Interaction, reason: str = "AFK"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ AFK works only inside a server.", ephemeral=True); return
    reason=(reason or "AFK").strip()[:200] or "AFK"
    _afk_data[_afk_key(interaction.guild.id, interaction.user.id)]={
        "reason": reason,
        "since": datetime.datetime.now(datetime.timezone.utc).timestamp(),
    }
    _save_afk()
    await interaction.response.send_message(f"💤 {interaction.user.mention} is now AFK: **{reason}**")

@bot.tree.command(name="snipe", description="Show the most recently deleted message in this channel")
async def snipe_cmd(interaction: discord.Interaction):
    data=_snipes.get(interaction.channel.id) if interaction.channel else None
    if not data:
        await interaction.response.send_message("ℹ️ No deleted message has been recorded in this channel yet.", ephemeral=True); return
    embed=discord.Embed(title="🎯 Last Deleted Message", description=data["content"], color=0xe67e22)
    embed.add_field(name="Author", value=f"<@{data['author_id']}> ({data['author_name']})", inline=False)
    embed.add_field(name="Recorded", value=f"<t:{int(data['created_at'])}:R>", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="timer", description="Start a countdown timer")
@app_commands.describe(minutes="How many minutes? (1-10080)", message="What should I say when time is over?")
async def timer_cmd(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 10080], message: str = "Time is up!"):
    message=message.strip()[:300] or "Time is up!"
    await interaction.response.send_message(f"⏲️ Timer started for **{minutes} minute(s)**, {interaction.user.mention}. I will remind you here.")
    async def _finish_timer():
        await asyncio.sleep(minutes*60)
        try:
            await interaction.channel.send(f"⏰ {interaction.user.mention} **Timer finished!** {message}")
        except Exception:
            pass
    asyncio.create_task(_finish_timer())

# ---------------- LEVELS / XP SYSTEM ----------------
_LEVELS_FILE = Path(__file__).resolve().parent / "levels.json"
def _load_levels() -> dict:
    try:
        data=json.loads(_LEVELS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
_levels: dict = _load_levels()
def _save_levels():
    try:
        _LEVELS_FILE.write_text(json.dumps(_levels), encoding="utf-8")
    except Exception:
        log.warning("levels save fail\n%s", traceback.format_exc())
def _xp_needed(level: int) -> int:
    """Level L se L+1 tak pahuchne ke liye chahiye XP."""
    return 100 + int(level) * 75
def _level_progress(total_xp: int):
    level=0; remaining=int(total_xp)
    while remaining >= _xp_needed(level):
        remaining -= _xp_needed(level); level += 1
    return level, remaining, _xp_needed(level)
async def _award_xp(message: discord.Message):
    if message.guild is None or message.author.bot: return
    gid=str(message.guild.id); uid=str(message.author.id)
    guild_data=_levels.setdefault(gid, {})
    rec=guild_data.setdefault(uid, {"xp": 0, "messages": 0, "last_xp": 0})
    rec["messages"]=int(rec.get("messages", 0)) + 1
    now=datetime.datetime.now(datetime.timezone.utc).timestamp()
    if now - float(rec.get("last_xp", 0)) < 60:
        return  # har minute sirf ek baar XP — spam farming band
    old_level, _, _=_level_progress(int(rec.get("xp", 0)))
    gain=random.randint(15, 25)
    rec["xp"]=int(rec.get("xp", 0)) + gain
    rec["last_xp"]=now
    _save_levels()
    new_level, _, _=_level_progress(int(rec["xp"]))
    if new_level > old_level:
        try:
            await message.channel.send(f"🎉 Congratulations {message.author.mention}! You reached **Level {new_level}** ⭐\nUse `/rank` to see your progress.")
        except Exception: pass

@bot.tree.command(name="rank", description="See your level, XP and server rank")
@app_commands.describe(member="Whose rank? Leave blank for yourself")
async def rank_cmd(interaction: discord.Interaction, member: discord.Member = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Levels work only inside a server.", ephemeral=True); return
    target=member or interaction.user
    guild_data=_levels.get(str(interaction.guild.id), {})
    rec=guild_data.get(str(target.id), {"xp": 0, "messages": 0})
    total_xp=int(rec.get("xp", 0))
    level, progress, needed=_level_progress(total_xp)
    ranking=sorted(guild_data.items(), key=lambda kv: int(kv[1].get("xp", 0)), reverse=True)
    position=next((i+1 for i, (uid, _) in enumerate(ranking) if uid == str(target.id)), len(ranking) + 1)
    filled=max(0, min(10, int((progress / needed) * 10) if needed else 10))
    bar="🟩" * filled + "⬛" * (10 - filled)
    embed=discord.Embed(title=f"⭐ {target.display_name}'s Rank", color=0xf1c40f)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🏅 Level", value=str(level), inline=True)
    embed.add_field(name="✨ Total XP", value=str(total_xp), inline=True)
    embed.add_field(name="🏆 Server Rank", value=f"#{position}", inline=True)
    embed.add_field(name="📈 Progress", value=f"{bar}\n{progress} / {needed} XP to Level {level + 1}", inline=False)
    embed.add_field(name="💬 Messages Counted", value=str(rec.get("messages", 0)), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="leaderboard", description="See the top 10 most active members of this server")
async def leaderboard_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Leaderboard works only inside a server.", ephemeral=True); return
    guild_data=_levels.get(str(interaction.guild.id), {})
    ranking=sorted(guild_data.items(), key=lambda kv: int(kv[1].get("xp", 0)), reverse=True)[:10]
    if not ranking:
        await interaction.response.send_message("ℹ️ No XP data yet — start chatting to earn XP!", ephemeral=True); return
    embed=discord.Embed(title=f"🏆 {interaction.guild.name} Leaderboard", color=0xf1c40f)
    medals=["🥇", "🥈", "🥉"]
    lines=[]
    for i, (uid, rec) in enumerate(ranking):
        level, _, _=_level_progress(int(rec.get("xp", 0)))
        prefix=medals[i] if i < 3 else f"**#{i+1}**"
        lines.append(f"{prefix} <@{uid}> — Level **{level}** • {int(rec.get('xp', 0))} XP")
    embed.description="\n".join(lines)
    embed.set_footer(text="Chat in the server to earn XP (one XP drop per minute).")
    await interaction.response.send_message(embed=embed)

# ---------------- SUPPORT TICKETS ----------------
_TICKETS_FILE = Path(__file__).resolve().parent / "tickets.json"
def _load_tickets() -> dict:
    try:
        data=json.loads(_TICKETS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
_tickets: dict = _load_tickets()
def _save_tickets():
    try:
        _TICKETS_FILE.write_text(json.dumps(_tickets), encoding="utf-8")
    except Exception:
        log.warning("tickets save fail\n%s", traceback.format_exc())

async def _create_ticket_thread(interaction: discord.Interaction, category: str = "Support", reason: str = ""):
    """Ticket thread banao. Interaction pehle se defer ho chuka ho — jawab followup se jayega."""
    reason=(reason or category).strip()[:300] or category
    for tid, data in _tickets.items():
        if data.get("open") and data.get("guild_id") == interaction.guild.id and data.get("user_id") == interaction.user.id:
            await interaction.followup.send(f"ℹ️ You already have an open ticket: <#{tid}>", ephemeral=True); return
    safe_name=re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower().replace(" ", "-"))[:24] or "member"
    cat_slug=re.sub(r"[^a-z0-9-]", "", category.lower().replace(" ", "-"))[:12] or "support"
    try:
        thread=await interaction.channel.create_thread(
            name=f"ticket-{cat_slug}-{safe_name}"[:90],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=1440,
            invitable=False,
            reason=f"Support ticket by {interaction.user}",
        )
    except discord.Forbidden:
        await interaction.followup.send("❌ I could not create a private ticket thread. Please give me **Create Private Threads** and **Manage Threads** permissions.", ephemeral=True); return
    except Exception:
        log.error("ticket create fail\n%s", traceback.format_exc())
        await interaction.followup.send("⚠️ Ticket could not be created right now. Please try again later.", ephemeral=True); return
    try: await thread.add_user(interaction.user)
    except Exception: pass
    try:
        owner=interaction.guild.owner or await interaction.guild.fetch_member(interaction.guild.owner_id)
        if owner and owner.id != interaction.user.id: await thread.add_user(owner)
    except Exception: pass
    _tickets[str(thread.id)]={"guild_id": interaction.guild.id, "channel_id": interaction.channel.id, "user_id": interaction.user.id, "category": category, "reason": reason, "open": True, "created_at": datetime.datetime.now(datetime.timezone.utc).timestamp()}
    _save_tickets()
    try:
        embed=discord.Embed(title="🎫 Support Ticket", color=0x3498db)
        embed.add_field(name="Member", value=interaction.user.mention, inline=True)
        embed.add_field(name="Category", value=category, inline=True)
        embed.add_field(name="Details", value=reason, inline=False)
        embed.set_footer(text="Explain your problem here. A moderator will help you. Use /close when done.")
        await thread.send(content=f"👋 {interaction.user.mention} Welcome to support!", embed=embed)
    except Exception: pass
    await interaction.followup.send(f"✅ Ticket created: {thread.mention}", ephemeral=True)

class TicketPanelView(discord.ui.View):
    """#ticket channel wala panel — HELP / BUG REPORT / IDEA FOR US? buttons (persistent)."""
    def __init__(self): super().__init__(timeout=None)

    async def _press(self, interaction: discord.Interaction, category: str):
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ Please use this inside a server text channel.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        await _create_ticket_thread(interaction, category=category)

    @discord.ui.button(label="HELP", style=discord.ButtonStyle.success, emoji="🎟️", custom_id="nezu:ticket_help")
    async def btn_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "Help")

    @discord.ui.button(label="BUG REPORT", style=discord.ButtonStyle.success, emoji="🎟️", custom_id="nezu:ticket_bug")
    async def btn_bug(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "Bug Report")

    @discord.ui.button(label="IDEA FOR US?", style=discord.ButtonStyle.success, emoji="🎟️", custom_id="nezu:ticket_idea")
    async def btn_idea(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._press(interaction, "Idea For Us")

@bot.tree.command(name="ticket", description="Open a private support ticket")
@app_commands.describe(reason="What do you need help with?")
async def ticket_cmd(interaction: discord.Interaction, reason: str = "Support"):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Please use this inside a server text channel.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    await _create_ticket_thread(interaction, category="Support", reason=reason)

@bot.tree.command(name="close", description="Close the current support ticket")
async def close_cmd(interaction: discord.Interaction):
    channel=interaction.channel
    if not isinstance(channel, discord.Thread):
        await interaction.response.send_message("❌ Use `/close` inside a ticket thread.", ephemeral=True); return
    data=_tickets.get(str(channel.id))
    if data is None and not channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ This is not a ticket thread.", ephemeral=True); return
    if data and interaction.user.id != data.get("user_id") and not _is_mod(interaction):
        await interaction.response.send_message("❌ Only the ticket owner or a moderator can close this ticket.", ephemeral=True); return
    await interaction.response.send_message("🔒 Closing this ticket...")
    if data:
        data["open"]=False; _save_tickets()
    try: await channel.edit(locked=True, archived=True, reason=f"Ticket closed by {interaction.user}")
    except Exception:
        log.error("ticket close fail\n%s", traceback.format_exc())

# ---------------- ECONOMY SYSTEM ----------------
_ECONOMY_FILE = Path(__file__).resolve().parent / "economy.json"
def _load_economy() -> dict:
    try:
        data=json.loads(_ECONOMY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
_economy: dict = _load_economy()
def _save_economy():
    try:
        _ECONOMY_FILE.write_text(json.dumps(_economy), encoding="utf-8")
    except Exception:
        log.warning("economy save fail\n%s", traceback.format_exc())
def _eco_rec(guild_id: int, user_id: int) -> dict:
    guild_data=_economy.setdefault(str(guild_id), {})
    return guild_data.setdefault(str(user_id), {"coins": 0, "last_daily": 0})

@bot.tree.command(name="balance", description="See your Nezu coin balance")
@app_commands.describe(member="Whose balance? Leave blank for yourself")
async def balance_cmd(interaction: discord.Interaction, member: discord.Member = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Economy works only inside a server.", ephemeral=True); return
    target=member or interaction.user
    rec=_eco_rec(interaction.guild.id, target.id)
    embed=discord.Embed(title="🪙 Coin Balance", color=0xf1c40f)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Member", value=target.mention, inline=True)
    embed.add_field(name="Coins", value=f"🪙 **{int(rec.get('coins', 0)):,}**", inline=True)
    embed.set_footer(text="Earn more with /daily and /gamble — spend them carefully!")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="daily", description="Claim your free daily coins (every 24 hours)")
async def daily_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Economy works only inside a server.", ephemeral=True); return
    rec=_eco_rec(interaction.guild.id, interaction.user.id)
    now=datetime.datetime.now(datetime.timezone.utc).timestamp()
    last=float(rec.get("last_daily", 0))
    remaining=86400 - (now - last)
    if last and remaining > 0:
        hours=int(remaining // 3600); minutes=int((remaining % 3600) // 60)
        await interaction.response.send_message(f"⏳ You already claimed today's coins. Come back in **{hours}h {minutes}m**.", ephemeral=True); return
    reward=random.randint(100, 250)
    rec["coins"]=int(rec.get("coins", 0)) + reward
    rec["last_daily"]=now
    _save_economy()
    await interaction.response.send_message(f"🎁 {interaction.user.mention} claimed **🪙 {reward} coins!**\n💰 New balance: **🪙 {int(rec['coins']):,}**")

@bot.tree.command(name="pay", description="Send some of your coins to another member")
@app_commands.describe(member="Who should receive the coins?", amount="How many coins? (minimum 1)")
async def pay_cmd(interaction: discord.Interaction, member: discord.Member, amount: int):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Economy works only inside a server.", ephemeral=True); return
    if amount < 1:
        await interaction.response.send_message("❌ Please send at least 1 coin.", ephemeral=True); return
    if member.id == interaction.user.id:
        await interaction.response.send_message("❌ You cannot pay yourself.", ephemeral=True); return
    if member.bot:
        await interaction.response.send_message("❌ You cannot pay a bot account.", ephemeral=True); return
    sender=_eco_rec(interaction.guild.id, interaction.user.id)
    if int(sender.get("coins", 0)) < amount:
        await interaction.response.send_message(f"❌ Not enough coins. Your balance is 🪙 **{int(sender.get('coins', 0)):,}**.", ephemeral=True); return
    receiver=_eco_rec(interaction.guild.id, member.id)
    sender["coins"]=int(sender.get("coins", 0)) - amount
    receiver["coins"]=int(receiver.get("coins", 0)) + amount
    _save_economy()
    await interaction.response.send_message(f"💸 {interaction.user.mention} sent **🪙 {amount:,} coins** to {member.mention}!")

@bot.tree.command(name="gamble", description="Bet coins on a 50/50 gamble — win double or lose it all")
@app_commands.describe(amount="How many coins do you want to bet? (minimum 10)")
async def gamble_cmd(interaction: discord.Interaction, amount: int):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Economy works only inside a server.", ephemeral=True); return
    if amount < 10:
        await interaction.response.send_message("❌ Minimum bet is 🪙 10 coins.", ephemeral=True); return
    rec=_eco_rec(interaction.guild.id, interaction.user.id)
    balance=int(rec.get("coins", 0))
    if balance < amount:
        await interaction.response.send_message(f"❌ Not enough coins. Your balance is 🪙 **{balance:,}**. Try `/daily` first!", ephemeral=True); return
    if random.random() < 0.5:
        rec["coins"]=balance + amount
        _save_economy()
        await interaction.response.send_message(f"🎰🍀 **YOU WIN, {interaction.user.mention}!**\nBet: 🪙 {amount:,} → Won: 🪙 **{amount:,}**\n💰 New balance: **🪙 {int(rec['coins']):,}**")
    else:
        rec["coins"]=balance - amount
        _save_economy()
        await interaction.response.send_message(f"🎰💔 **You lost, {interaction.user.mention}.**\nLost: 🪙 {amount:,}\n💰 New balance: **🪙 {int(rec['coins']):,}**\nBetter luck next time!")

# ---------------- AUTOMOD: ANTI-LINK ----------------
_AUTOMOD_FILE = Path(__file__).resolve().parent / "automod.json"
def _load_automod() -> dict:
    try:
        data=json.loads(_AUTOMOD_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
_automod: dict = _load_automod()
def _save_automod():
    try:
        _AUTOMOD_FILE.write_text(json.dumps(_automod), encoding="utf-8")
    except Exception:
        log.warning("automod save fail\n%s", traceback.format_exc())
def _antilink_enabled(guild_id: int) -> bool:
    return bool(_automod.get(str(guild_id), {}).get("antilink", False))
def _antispam_enabled(guild_id: int) -> bool:
    return bool(_automod.get(str(guild_id), {}).get("antispam", False))
def _badwords_enabled(guild_id: int) -> bool:
    return bool(_automod.get(str(guild_id), {}).get("badwords", False))
def _antiraid_enabled(guild_id: int) -> bool:
    return bool(_automod.get(str(guild_id), {}).get("antiraid", False))
_DEFAULT_BADWORDS=["fuck", "bitch", "bastard", "asshole", "madarchod", "behenchod", "bhenchod", "chutiya", "gandu", "haramkhor", "randi", "saala kutta"]
def _badword_list(guild_id: int) -> list:
    custom=_automod.get(str(guild_id), {}).get("custom_badwords", [])
    return _DEFAULT_BADWORDS + [str(w).lower() for w in custom]
async def _modlog(guild, text: str):
    """Server ka mod-log channel set ho to waha moderation ki report bhejo."""
    if guild is None: return
    ch_id=_automod.get(str(guild.id), {}).get("modlog_channel")
    if not ch_id: return
    ch=guild.get_channel(int(ch_id))
    if ch is None: return
    try:
        await ch.send(text)
    except Exception:
        pass
_LINK_MARKERS=("http://", "https://", "www.", "discord.gg/", "discord.com/invite/", "t.me/", "bit.ly/", "tinyurl.com/")
async def _check_antilink(message: discord.Message) -> bool:
    """Anti-link ON ho to normal member ka link wala message delete kar do. Mods exempt."""
    if message.guild is None: return False
    if not _antilink_enabled(message.guild.id): return False
    content=(message.content or "").lower()
    if not any(marker in content for marker in _LINK_MARKERS): return False
    perms=getattr(message.author, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_messages or perms.manage_guild): return False
    try:
        await message.delete()
    except Exception:
        return False
    try:
        await message.channel.send(f"🛡️ {message.author.mention} Links are not allowed here — your message was removed by AutoMod.", delete_after=6)
    except Exception:
        pass
    log.info("antilink: %s ka link message delete (guild %s)", message.author, message.guild.id)
    await _modlog(message.guild, f"🛡️ **Anti-Link:** A link message from {message.author.mention} was deleted in {message.channel.mention}")
    return True

# ---------------- AUTOMOD: ANTI-SPAM ----------------
_spam_tracker: dict[tuple[int, int], list] = {}
_spam_warned: dict[tuple[int, int], float] = {}
async def _check_antispam(message: discord.Message) -> bool:
    """Anti-spam ON ho to flood/repeat spam delete Karo. Mods exempt.
    Trigger: 5 second me 5 messages, ya 20 second me ek hi message 3 baar."""
    if message.guild is None: return False
    if not _antispam_enabled(message.guild.id): return False
    perms=getattr(message.author, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_messages or perms.manage_guild): return False
    now=time.time()
    key=(message.guild.id, message.author.id)
    hist=_spam_tracker.setdefault(key, [])
    hist[:] = [(ts, txt) for ts, txt in hist if now - ts <= 20]
    norm=(message.content or "").strip().lower()
    hist.append((now, norm))
    flood=sum(1 for ts, _ in hist if now - ts <= 5) >= 5
    repeat=bool(norm) and sum(1 for _, txt in hist if txt == norm) >= 3
    if not (flood or repeat): return False
    hist.clear()
    try:
        await message.delete()
    except Exception:
        return False
    if now - _spam_warned.get(key, 0) >= 15:
        _spam_warned[key]=now
        try:
            await message.channel.send(f"🛡️ {message.author.mention} Please don't spam — your message was removed by AutoMod.", delete_after=8)
        except Exception:
            pass
    log.info("antispam: %s ka spam message delete (guild %s)", message.author, message.guild.id)
    await _modlog(message.guild, f"🛡️ **Anti-Spam:** A spam message from {message.author.mention} was deleted in {message.channel.mention}")
    return True

@bot.tree.command(name="antilink", description="AutoMod: automatically delete links from normal members (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — delete links automatically", value="on"),
    app_commands.Choice(name="Off — allow links", value="off"),
    app_commands.Choice(name="Status — check anti-link setting", value="status"),
])
async def antilink_cmd(interaction: discord.Interaction, action: str = "on"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Anti-link works only inside a server.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    if action == "on":
        _automod.setdefault(gid, {})["antilink"]=True; _save_automod()
        await interaction.response.send_message("🛡️ **Anti-Link ON!** ✅\nLink messages from normal members will now be deleted automatically. Links from admins/mods will not be deleted.", ephemeral=True)
    elif action == "off":
        _automod.setdefault(gid, {})["antilink"]=False; _save_automod()
        await interaction.response.send_message("🛡️ Anti-Link is **OFF** — links are now allowed.", ephemeral=True)
    else:
        state="ON ✅" if _antilink_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🛡️ Anti-Link is currently **{state}** in this server.", ephemeral=True)

@bot.tree.command(name="antispam", description="AutoMod: automatically delete spam/flood messages (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — delete spam automatically", value="on"),
    app_commands.Choice(name="Off — allow spam", value="off"),
    app_commands.Choice(name="Status — check anti-spam setting", value="status"),
])
async def antispam_cmd(interaction: discord.Interaction, action: str = "on"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Anti-spam works only inside a server.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    if action == "on":
        _automod.setdefault(gid, {})["antispam"]=True; _save_automod()
        await interaction.response.send_message("🛡️ **Anti-Spam ON!** ✅\nFlooding (5 messages in 5 seconds) or repeating the same message will now be deleted automatically. Admins/mods are exempt.", ephemeral=True)
    elif action == "off":
        _automod.setdefault(gid, {})["antispam"]=False; _save_automod()
        await interaction.response.send_message("🛡️ Anti-Spam is **OFF**.", ephemeral=True)
    else:
        state="ON ✅" if _antispam_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🛡️ Anti-Spam is currently **{state}** in this server.", ephemeral=True)

async def _check_badwords(message: discord.Message) -> bool:
    """Bad-words filter ON ho to gaali wala message delete Karo. Mods exempt."""
    if message.guild is None: return False
    if not _badwords_enabled(message.guild.id): return False
    perms=getattr(message.author, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_messages or perms.manage_guild): return False
    content=(message.content or "").lower()
    if not content: return False
    hit=next((w for w in _badword_list(message.guild.id) if w and w in content), None)
    if not hit: return False
    try:
        await message.delete()
    except Exception:
        return False
    try:
        await message.channel.send(f"🛡️ {message.author.mention} Please keep the chat clean — your message was removed by AutoMod.", delete_after=8)
    except Exception:
        pass
    log.info("badwords: %s ka message delete (guild %s)", message.author, message.guild.id)
    await _modlog(message.guild, f"🛡️ **Bad-Words:** A message from {message.author.mention} was deleted in {message.channel.mention}")
    return True

@bot.tree.command(name="badwords", description="AutoMod: bad-words filter for the server (admin)")
@app_commands.describe(action="On, Off, Status, Add, Remove, or List", word="Word to add/remove (only for Add/Remove)")
@app_commands.choices(action=[
    app_commands.Choice(name="On — enable the filter", value="on"),
    app_commands.Choice(name="Off — disable the filter", value="off"),
    app_commands.Choice(name="Status — view the setting", value="status"),
    app_commands.Choice(name="Add — add your word", value="add"),
    app_commands.Choice(name="Remove — remove your word", value="remove"),
    app_commands.Choice(name="List — view custom words", value="list"),
])
async def badwords_cmd(interaction: discord.Interaction, action: str = "on", word: str | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ The bad-words filter only works inside a server.", ephemeral=True); return
    if action in ("on", "off", "add", "remove") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "on":
        cfg["badwords"]=True; _save_automod()
        await interaction.response.send_message("🛡️ **Bad-Words Filter ON!** ✅\nMessages with abusive words will now be deleted automatically (mods are exempt). Add your own words with `/badwords add`.", ephemeral=True)
    elif action == "off":
        cfg["badwords"]=False; _save_automod()
        await interaction.response.send_message("🛡️ Bad-Words Filter is **OFF**.", ephemeral=True)
    elif action == "add":
        if not word or len(word.strip()) < 2:
            await interaction.response.send_message("❌ Also provide the `word` to add.", ephemeral=True); return
        w=word.strip().lower()
        custom=cfg.setdefault("custom_badwords", [])
        if w in custom:
            await interaction.response.send_message(f"ℹ️ `{w}` is already in the list.", ephemeral=True); return
        custom.append(w); _save_automod()
        await interaction.response.send_message(f"✅ `{w}` was added to the bad-words list.", ephemeral=True)
    elif action == "remove":
        if not word:
            await interaction.response.send_message("❌ Also provide the `word` to remove.", ephemeral=True); return
        w=word.strip().lower()
        custom=cfg.setdefault("custom_badwords", [])
        if w in custom:
            custom.remove(w); _save_automod()
            await interaction.response.send_message(f"✅ `{w}` was removed from the list.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ `{w}` is not in the custom list.", ephemeral=True)
    elif action == "list":
        custom=cfg.get("custom_badwords", [])
        txt=", ".join(f"`{w}`" for w in custom) if custom else "no custom words"
        await interaction.response.send_message(f"🛡️ Custom bad-words: {txt}\n(The default Hindi/English abusive-word list is always included when the filter is ON.)", ephemeral=True)
    else:
        state="ON ✅" if _badwords_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🛡️ Bad-Words Filter is currently **{state}** in this server.", ephemeral=True)

@bot.tree.command(name="setmodlog", description="Set the channel for moderation logs (admin)")
@app_commands.describe(action="Set, Off, or Status", channel="Which channel should receive logs? (Set only)")
@app_commands.choices(action=[
    app_commands.Choice(name="Set — make this/selected channel the mod-log", value="set"),
    app_commands.Choice(name="Off — disable mod-log", value="off"),
    app_commands.Choice(name="Status — view the current mod-log", value="status"),
])
async def setmodlog_cmd(interaction: discord.Interaction, action: str = "set", channel: discord.TextChannel | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Mod-log only works inside a server.", ephemeral=True); return
    if action in ("set", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "set":
        ch=channel or interaction.channel
        if ch is None:
            await interaction.response.send_message("❌ Channel not found.", ephemeral=True); return
        cfg["modlog_channel"]=ch.id; _save_automod()
        await interaction.response.send_message(f"📋 **Mod-Log set!** All moderation actions (anti-link, anti-spam, bad-words, ban/unban, raid alerts) will now appear in {ch.mention}.", ephemeral=True)
    elif action == "off":
        cfg.pop("modlog_channel", None); _save_automod()
        await interaction.response.send_message("📋 Mod-Log is **OFF**.", ephemeral=True)
    else:
        ch_id=cfg.get("modlog_channel")
        ch=interaction.guild.get_channel(int(ch_id)) if ch_id else None
        await interaction.response.send_message(f"📋 Mod-Log channel: {ch.mention if ch else '**not set**'}", ephemeral=True)

_raid_joins: dict[int, list] = {}
_raid_alerted: dict[int, float] = {}
async def _check_antiraid(member: discord.Member):
    """Anti-Raid ON ho aur 60 sec me 6+ log join kare to mods ko alert Karo."""
    if not _antiraid_enabled(member.guild.id): return
    now=time.time()
    hist=_raid_joins.setdefault(member.guild.id, [])
    hist[:] = [ts for ts in hist if now - ts <= 60]
    hist.append(now)
    if len(hist) < 6: return
    if now - _raid_alerted.get(member.guild.id, 0) < 300: return
    _raid_alerted[member.guild.id]=now
    hist.clear()
    msg=(f"🚨 **RAID ALERT!** 6+ members joined {member.guild.name} within 1 minute. "
         f"Mods, please check suspicious accounts. (Nezu Anti-Raid)")
    log.warning("antiraid alert guild %s", member.guild.id)
    await _modlog(member.guild, msg)
    ch=getattr(member.guild, "system_channel", None)
    if ch is not None:
        try: await ch.send(msg)
        except Exception: pass

@bot.tree.command(name="antiraid", description="Security: raid (mass join) alert system (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — enable raid alerts", value="on"),
    app_commands.Choice(name="Off — disable raid alerts", value="off"),
    app_commands.Choice(name="Status — view the setting", value="status"),
])
async def antiraid_cmd(interaction: discord.Interaction, action: str = "on"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Anti-Raid only works inside a server.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    if action == "on":
        _automod.setdefault(gid, {})["antiraid"]=True; _save_automod()
        await interaction.response.send_message("🚨 **Anti-Raid ON!** ✅\nIf 6+ members join within 1 minute, mods will get an alert in the mod-log/system channel. Set a log channel first with `/setmodlog`.", ephemeral=True)
    elif action == "off":
        _automod.setdefault(gid, {})["antiraid"]=False; _save_automod()
        await interaction.response.send_message("🚨 Anti-Raid is **OFF**.", ephemeral=True)
    else:
        state="ON ✅" if _antiraid_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🚨 Anti-Raid is currently **{state}** in this server.", ephemeral=True)

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    await _modlog(guild, f"🔨 **Ban:** {user} ({user.id}) was banned from the server.")
    await _antinuke_event(guild, "member ban", discord.AuditLogAction.ban, user.id)

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    await _modlog(guild, f"♻️ **Unban:** The ban for {user} ({user.id}) was removed.")

# ---------------- SECURITY: ANTINUKE / NUKE / LOCKDOWN ----------------
def _antinuke_enabled(guild_id: int) -> bool:
    return bool(_automod.get(str(guild_id), {}).get("antinuke", False))
def _antinuke_whitelist(guild_id: int) -> list:
    return [int(x) for x in _automod.get(str(guild_id), {}).get("antinuke_whitelist", [])]
_antinuke_hits: dict = {}
_antinuke_punished: dict = {}

async def _antinuke_find_executor(guild, action, target_id=None):
    """Audit log se pata karo kisne ye kaam kiya (bot ko View Audit Log chahiye)."""
    try:
        me=guild.me
        if me is None or not me.guild_permissions.view_audit_log: return None
        abhi=datetime.datetime.now(datetime.timezone.utc)
        async for entry in guild.audit_logs(limit=8, action=action):
            try: age=(abhi-entry.created_at).total_seconds()
            except Exception: age=0
            if age < -5 or age > 30: continue
            if target_id is not None and getattr(entry.target, "id", None) != target_id: continue
            return entry.user
    except Exception:
        log.warning("antinuke audit log fail guild %s\n%s", guild.id, traceback.format_exc())
    return None

async def _antinuke_punish(guild, executor, label: str, count: int):
    abhi=time.time()
    key=(guild.id, executor.id)
    if abhi - _antinuke_punished.get(key, 0) < 600: return
    _antinuke_punished[key]=abhi
    member=guild.get_member(executor.id)
    action_taken="alert only (member/role not found)"
    if member is not None and executor.id != guild.owner_id:
        if getattr(member, "bot", False):
            try:
                await member.kick(reason="AntiNuke: lagatar destructive actions")
                action_taken="kicked the rogue/compromised bot"
            except Exception:
                action_taken="could not kick the bot (permission/role)"
        else:
            me=guild.me
            removed=0
            roles=[r for r in member.roles if r.name != "@everyone" and not r.managed and (me is None or r.position < me.top_role.position)]
            if roles:
                try:
                    await member.remove_roles(*roles, reason="AntiNuke: lagatar destructive actions")
                    removed=len(roles)
                except Exception:
                    pass
            try:
                await member.timeout(datetime.timedelta(hours=6), reason="AntiNuke protection")
            except Exception:
                pass
            action_taken=f"removed {removed} roles + 6-hour timeout"
    msg=(f"🚨 **ANTINUKE ALERT!** {executor.mention} (`{executor.id}`) performed **{count} destructive actions** in 60 seconds "
         f"(last: {label}).\n🛡️ Action: {action_taken}\nIf this was a mistake, the server owner can give their roles back.")
    log.warning("antinuke trigger guild %s executor %s count %s", guild.id, executor.id, count)
    await _modlog(guild, msg)
    ch=getattr(guild, "system_channel", None)
    if ch is not None:
        try: await ch.send(msg)
        except Exception: pass

async def _antinuke_event(guild, label: str, action, target_id=None):
    """Channel/role delete, ban, kick, webhook jaisi destructive harkatein ginna."""
    try:
        if guild is None or not _antinuke_enabled(guild.id): return
        executor=await _antinuke_find_executor(guild, action, target_id)
        if executor is None: return
        if executor.id == guild.owner_id: return
        if bot.user and executor.id == bot.user.id: return
        if executor.id in _antinuke_whitelist(guild.id): return
        abhi=time.time()
        key=(guild.id, executor.id)
        hist=[t for t in _antinuke_hits.get(key, []) if abhi - t <= 60]
        hist.append(abhi)
        _antinuke_hits[key]=hist
        log.info("antinuke: %s ne %s kiya (60s me kul %s)", executor, label, len(hist))
        if len(hist) >= 3:
            _antinuke_hits[key]=[]
            await _antinuke_punish(guild, executor, label, len(hist))
    except Exception:
        log.warning("antinuke event fail\n%s", traceback.format_exc())

async def _antinuke_check_bot_add(member) -> bool:
    """AntiNuke ON ho to bina-bharose ke user ke dwara add kiya bot roko."""
    try:
        if not _antinuke_enabled(member.guild.id): return False
        await asyncio.sleep(1.5)
        adder=await _antinuke_find_executor(member.guild, discord.AuditLogAction.bot_add, member.id)
        if adder is None: return False
        if adder.id == member.guild.owner_id: return False
        if bot.user and adder.id == bot.user.id: return False
        if adder.id in _antinuke_whitelist(member.guild.id): return False
        try:
            await member.kick(reason=f"AntiNuke: {adder} added a bot without being whitelisted")
            ok=True
        except Exception:
            ok=False
        msg=f"🚨 **ANTINUKE:** {adder.mention} added the bot {member.mention} — they are not whitelisted. " + ("The bot was kicked." if ok else "The bot could not be kicked — check immediately!")
        await _modlog(member.guild, msg)
        ch=getattr(member.guild, "system_channel", None)
        if ch is not None:
            try: await ch.send(msg)
            except Exception: pass
        return ok
    except Exception:
        log.warning("antinuke bot-add check fail\n%s", traceback.format_exc())
        return False

@bot.event
async def on_guild_channel_delete(channel):
    guild=getattr(channel, "guild", None)
    if guild is not None:
        await _antinuke_event(guild, "channel delete", discord.AuditLogAction.channel_delete, channel.id)

@bot.event
async def on_guild_role_delete(role):
    guild=getattr(role, "guild", None)
    if guild is not None:
        await _antinuke_event(guild, "role delete", discord.AuditLogAction.role_delete, role.id)

@bot.event
async def on_member_remove(member):
    guild=getattr(member, "guild", None)
    if guild is not None:
        await _antinuke_event(guild, "member kick", discord.AuditLogAction.kick, member.id)

@bot.event
async def on_webhooks_update(channel):
    guild=getattr(channel, "guild", None)
    if guild is not None:
        await _antinuke_event(guild, "webhook change", discord.AuditLogAction.webhook_create)

@bot.tree.command(name="antinuke", description="AntiNuke security: protect the server from mass deletes/bans/bot-adds (admin)")
@app_commands.describe(action="On, Off, Status, Trust, Untrust, or Whitelist", member="Choose a member for Trust/Untrust")
@app_commands.choices(action=[
    app_commands.Choice(name="On — enable protection", value="on"),
    app_commands.Choice(name="Off — disable protection", value="off"),
    app_commands.Choice(name="Status — view the state", value="status"),
    app_commands.Choice(name="Trust — add member to the whitelist", value="trust"),
    app_commands.Choice(name="Untrust — remove from the whitelist", value="untrust"),
    app_commands.Choice(name="Whitelist — view trusted members", value="whitelist"),
])
async def antinuke_cmd(interaction: discord.Interaction, action: str = "status", member: discord.Member = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ AntiNuke only works inside a server.", ephemeral=True); return
    if action in ("on", "off", "trust", "untrust") and not _is_admin(interaction):
        await interaction.response.send_message("❌ This is only for server **admins**.", ephemeral=True); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "on":
        cfg["antinuke"]=True; _save_automod()
        await interaction.response.send_message("🛡️ **AntiNuke ON!** ✅\nNow anyone (including admins) who performs **3+ channel/role deletions, bans, kicks, or webhook actions in 60 seconds** will lose their roles and get a 6-hour timeout. A bot added by someone who is not whitelisted will be **kicked**.\nWhitelist trusted mods with `/antinuke Trust`. The server owner is always safe.", ephemeral=True)
    elif action == "off":
        cfg["antinuke"]=False; _save_automod()
        await interaction.response.send_message("🛡️ AntiNuke is **OFF**.", ephemeral=True)
    elif action == "trust":
        if member is None:
            await interaction.response.send_message("❌ Also choose the `member` to trust.", ephemeral=True); return
        wl=cfg.setdefault("antinuke_whitelist", [])
        if member.id not in wl: wl.append(member.id); _save_automod()
        await interaction.response.send_message(f"✅ {member.mention} is now on the AntiNuke **whitelist** — no action will be taken against them.", ephemeral=True)
    elif action == "untrust":
        if member is None:
            await interaction.response.send_message("❌ Also choose the `member` to untrust.", ephemeral=True); return
        wl=cfg.setdefault("antinuke_whitelist", [])
        if member.id in wl: wl.remove(member.id); _save_automod()
        await interaction.response.send_message(f"✅ Removed {member.mention} from the whitelist.", ephemeral=True)
    elif action == "whitelist":
        wl=_antinuke_whitelist(interaction.guild.id)
        txt="\n".join(f"• <@{u}>" for u in wl) if wl else "none — only the server owner is safe"
        await interaction.response.send_message(f"🛡️ **AntiNuke Whitelist:**\n{txt}", ephemeral=True)
    else:
        state="ON ✅" if _antinuke_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🛡️ AntiNuke is **{state}** in this server.", ephemeral=True)

@bot.tree.command(name="nuke", description="Nuke a channel: delete all messages and recreate it fresh (admin only)")
@app_commands.describe(channel="Which channel should be nuked? (blank = this channel)")
async def nuke_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Nuke only works inside a server.", ephemeral=True); return
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Only server **admins** can nuke a channel.", ephemeral=True); return
    target=channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message("❌ Only text channels can be nuked.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    pos=target.position
    try:
        clone=await target.clone(reason=f"Channel nuke by {interaction.user}")
        try: await clone.edit(position=pos)
        except Exception: pass
        await target.delete(reason=f"Channel nuke by {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send("❌ I could not nuke the channel — I need the **Manage Channels** permission.", ephemeral=True); return
    except Exception:
        log.error("nuke fail\n%s", traceback.format_exc())
        await interaction.followup.send("⚠️ Nuke failed — please check the channel permissions.", ephemeral=True); return
    embed=discord.Embed(title="💥 Channel Nuked!", description=f"This channel was nuked by {interaction.user.mention} — all old messages were cleared. The channel is brand new! ✨", color=0xe74c3c)
    try: await clone.send(embed=embed)
    except Exception: pass
    try: await interaction.followup.send(f"💥 **Nuked!** New channel: {clone.mention}", ephemeral=True)
    except Exception: pass

@bot.tree.command(name="lockdown", description="Lock down the whole server: stop/start talking in all text channels (admin only)")
@app_commands.describe(action="On = lock all, Off = restore, Status = check state")
@app_commands.choices(action=[
    app_commands.Choice(name="On — lock down the server", value="on"),
    app_commands.Choice(name="Off — remove lockdown (restore previous state)", value="off"),
    app_commands.Choice(name="Status — lockdown state", value="status"),
])
async def lockdown_cmd(interaction: discord.Interaction, action: str = "status"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Lockdown only works inside a server.", ephemeral=True); return
    if action in ("on", "off") and not _is_admin(interaction):
        await interaction.response.send_message("❌ Lockdown is only for server **admins**.", ephemeral=True); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "status":
        saved=cfg.get("lockdown", {})
        halat=f"**ON 🔒** ({len(saved)} channels locked)" if saved else "**OFF**"
        await interaction.response.send_message(f"🔒 Server Lockdown: {halat}", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    if action == "on":
        saved=cfg.setdefault("lockdown", {})
        ok=fail=0
        for ch in interaction.guild.text_channels:
            if str(ch.id) in saved: continue
            purana=ch.overwrites_for(interaction.guild.default_role).send_messages
            try:
                await ch.set_permissions(interaction.guild.default_role, send_messages=False, reason=f"Server lockdown by {interaction.user}")
                saved[str(ch.id)]=purana; ok+=1
            except Exception:
                fail+=1
        _save_automod()
        await interaction.followup.send(f"🔒 **Server Lockdown ON!** {ok} channels were locked" + (f", {fail} could not be locked (permission)." if fail else ".") + "\nUse `/lockdown Off` to restore everything. (Admins/mods can still speak if their roles allow it.)", ephemeral=True)
    else:
        saved=cfg.pop("lockdown", {})
        ok=fail=0
        for cid, purana in list(saved.items()):
            ch=interaction.guild.get_channel(int(cid))
            if ch is None: continue
            try:
                await ch.set_permissions(interaction.guild.default_role, send_messages=purana, reason=f"Lockdown off by {interaction.user}")
                ok+=1
            except Exception:
                fail+=1
        _save_automod()
        await interaction.followup.send(f"🔓 **Lockdown OFF!** {ok} channels were restored" + (f", {fail} failed." if fail else "."), ephemeral=True)

@bot.tree.command(name="quarantine", description="Remove all roles from a member and isolate them (mod) — restore with /unquarantine")
@app_commands.describe(member="Which member should be quarantined?")
async def quarantine_cmd(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    if not interaction.user.guild_permissions.manage_roles:
        await _deny(interaction); return
    if member.id == interaction.guild.owner_id or member.id == interaction.user.id or (bot.user and member.id == bot.user.id):
        await interaction.response.send_message("❌ This member cannot be quarantined.", ephemeral=True); return
    me=interaction.guild.me
    hatao=[r for r in member.roles if r.name != "@everyone" and not r.managed and (me is None or r.position < me.top_role.position)]
    if not hatao:
        await interaction.response.send_message(f"ℹ️ {member.mention} has no removable roles (or their roles are above mine).", ephemeral=True); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    removed=[]
    for r in hatao:
        try:
            await member.remove_roles(r, reason=f"Quarantine by {interaction.user}")
            removed.append(r.id)
        except Exception:
            pass
    if removed:
        cfg.setdefault("quarantine", {})[str(member.id)]=removed; _save_automod()
    await interaction.response.send_message(f"🚧 {member.mention} has been **quarantined** — removed {len(removed)} roles. Use `/unquarantine` to give them back.")

@bot.tree.command(name="unquarantine", description="Give back the roles of a quarantined member (mod)")
@app_commands.describe(member="Which member should get their roles back?")
async def unquarantine_cmd(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    if not interaction.user.guild_permissions.manage_roles:
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    record=cfg.setdefault("quarantine", {}).pop(str(member.id), None)
    if not record:
        await interaction.response.send_message(f"ℹ️ {member.mention} is not quarantined — there is no record to restore.", ephemeral=True); return
    _save_automod()
    me=interaction.guild.me
    wapas=0
    for rid in record:
        role=interaction.guild.get_role(int(rid))
        if role is None or role.managed or (me is not None and role.position >= me.top_role.position): continue
        try:
            await member.add_roles(role, reason=f"Unquarantine by {interaction.user}")
            wapas+=1
        except Exception:
            pass
    await interaction.response.send_message(f"✅ {member.mention} got **{wapas} roles** back — quarantine ended.")

@bot.tree.command(name="purge", description="Delete one member's messages from this channel (mod)")
@app_commands.describe(member="Which member's messages should be deleted?", amount="Maximum messages (1-100)")
async def purge_cmd(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100] = 50):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Purge only works in a server text channel.", ephemeral=True); return
    if not _is_mod(interaction):
        await _deny(interaction); return
    await interaction.response.defer(ephemeral=True)
    msgs=[]
    try:
        async for m in interaction.channel.history(limit=500):
            if m.author.id == member.id:
                msgs.append(m)
                if len(msgs) >= amount: break
    except Exception:
        await interaction.followup.send("❌ I could not read the history — I need the **Read Message History** permission.", ephemeral=True); return
    if not msgs:
        await interaction.followup.send(f"ℹ️ No message from {member.mention} was found in the recent messages.", ephemeral=True); return
    try:
        if len(msgs) == 1:
            await msgs[0].delete()
        else:
            await interaction.channel.delete_messages(msgs)
        await interaction.followup.send(f"🧹 Deleted **{len(msgs)} messages** from {member.mention}.", ephemeral=True)
    except Exception:
        await interaction.followup.send("⚠️ I could not delete them — Discord does not allow bulk-deleting messages older than 14 days, or I lack permission.", ephemeral=True)



# ---------------- ALL NUKE (sirf bot owner + server owner, confirm ke saath) ----------------
def _server_backup_bytes(guild) -> bytes:
    """Nuke se pehle server ke channels/roles ki list ka backup (JSON)."""
    data={
        "guild": guild.name,
        "guild_id": guild.id,
        "channels": [
            {"name": getattr(c, "name", "?"), "type": str(getattr(c, "type", "?")),
             "category": getattr(getattr(c, "category", None), "name", None),
             "position": getattr(c, "position", 0),
             "topic": getattr(c, "topic", None)}
            for c in guild.channels
        ],
        "roles": [r.name for r in guild.roles],
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

async def _all_nuke_execute(guild, actor) -> dict:
    """Saare channels/categories delete Karo, fir ek fresh #general banao.
    Roles, members aur bans ko haath nahi lagta."""
    channels=list(guild.channels)
    # pehle normal channels, categories sabse aakhir me
    channels.sort(key=lambda c: 1 if isinstance(c, discord.CategoryChannel) else 0)
    results=await asyncio.gather(
        *[c.delete(reason=f"All Nuke by {actor}") for c in channels],
        return_exceptions=True)
    deleted=sum(1 for r in results if not isinstance(r, Exception))
    failed=len(channels) - deleted
    fresh=None
    try:
        fresh=await guild.create_text_channel("general", reason="All Nuke ke baad fresh channel")
    except Exception:
        log.warning("allnuke: fresh #general nahi ban paya guild %s\n%s", guild.id, traceback.format_exc())
    log.warning("ALL NUKE guild %s (%s) by %s: deleted=%s failed=%s", guild.id, guild.name, actor, deleted, failed)
    return {"deleted": deleted, "failed": failed, "total": len(channels), "fresh": fresh}

class AllNukeConfirmView(discord.ui.View):
    """Confirm/Cancel button — sirf wahi dabayega jisne command chalaya."""
    def __init__(self, owner_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.owner_id=owner_id
        self.guild_id=guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This confirmation is only for the bot owner.", ephemeral=True)
            return False
        # Confirm dabane ke time bhi double-check: sirf BOT OWNER + usi server ka OWNER.
        try:
            bot_owner=await _get_bot_owner_id()
        except Exception:
            bot_owner=0
        if interaction.user.id != bot_owner or interaction.guild is None or interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message("❌ Blocked: All Nuke only runs when the bot owner uses it in their own server.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="YES, ALL NUKE", style=discord.ButtonStyle.danger, emoji="💥")
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled=True
        try: await interaction.response.edit_message(content="💥 **All Nuke started...** A backup is being sent to the owner by DM, then all channels will be deleted.", view=self)
        except Exception: pass
        guild=interaction.guild
        if guild is None:
            return
        # 1) Backup DM (delete se pehle, taaki structure wapas banaya ja sake)
        try:
            backup=_server_backup_bytes(guild)
            owner=interaction.user
            await owner.send(content=f"🗂️ All-Nuke backup for **{guild.name}** — the full list of channels/roles:",
                             file=discord.File(io.BytesIO(backup), filename=f"{guild.name}-backup.json"))
        except Exception:
            log.warning("allnuke backup DM fail\n%s", traceback.format_exc())
        # 2) Asli nuke
        result=await _all_nuke_execute(guild, interaction.user)
        fresh=result["fresh"]
        if fresh is not None:
            try:
                await fresh.send(embed=discord.Embed(
                    title="💥 Server All-Nuked!",
                    description=(f"{interaction.user.mention} (bot owner) nuked all channels in this server.\n\n"
                                 f"🗑️ Delete hue: **{result['deleted']}** / {result['total']} channels"
                                 + (f" • {result['failed']} fail (permission)" if result["failed"] else "")
                                 + "\n🗂️ A backup of the structure was sent to the owner by DM.\n✨ Here is the brand-new first channel — rebuild the server from here!"),
                    color=0xe74c3c))
            except Exception:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled=True
        await interaction.response.edit_message(content="✅ **All Nuke cancelled.** Nothing was deleted.", view=self)

@bot.tree.command(name="allnuke", description="[BOT OWNER ONLY] Nuke all channels in the server")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def allnuke_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    owner_id=await _get_bot_owner_id()
    if interaction.user.id != owner_id:
        await interaction.response.send_message("❌ This command is **only for the bot owner**.", ephemeral=True); return
    if interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message("❌ For safety: this only works in a server where **you are the server owner**. It will never run on someone else's server.", ephemeral=True); return
    me=interaction.guild.me
    if me is None or not (me.guild_permissions.administrator or me.guild_permissions.manage_channels):
        await interaction.response.send_message("❌ To nuke, the bot needs **Manage Channels / Administrator** in this server.", ephemeral=True); return
    total=len(interaction.guild.channels)
    embed=discord.Embed(
        title="💥 ALL NUKE — Final warning!",
        description=(f"All **{total} channels/categories in {interaction.guild.name}** will be deleted!\n\n"
                     "✅ Roles, members and bans will stay safe\n"
                     "🗂️ Before deletion, a backup of the channel/role list will be sent to you by DM\n"
                     "⚠️ Messages will **never** come back\n\n"
                     "If you are sure, press the red button below. It expires in 60 seconds."),
        color=0xe74c3c)
    await interaction.response.send_message(embed=embed, view=AllNukeConfirmView(interaction.user.id, interaction.guild.id), ephemeral=True)

# ---------------- EXTRA GROUPS: 56 SUBCOMMANDS (Discord 100 limit ke andar) ----------------
# Discord Ek bot ko max 100 top-level slash commands deta hai — isliye naye
# features group-commands ke andar subcommands bana ke jode hai.
tools_group=app_commands.Group(name="tools", description="Utility tools: time, ID, encode, convert and random")
text_group=app_commands.Group(name="text", description="Text tools: reverse, upper/lowercase, count, spoiler and more")
actions_group=app_commands.Group(name="actions", description="Fun actions: hug, pat, wave, dance and more")
server_group=app_commands.Group(name="server", description="Detailed server info: stats, emojis, boosts and more")
games_group=app_commands.Group(name="games", description="Small games: trivia, riddle, scramble, math, slots and more")

# ---------- /tools (11) ----------
@tools_group.command(name="timestamp", description="Create a Discord time code for a time from now")
@app_commands.describe(minutes="Minutes from now (negative = in the past)")
async def tools_timestamp(interaction: discord.Interaction, minutes: app_commands.Range[int, -100000, 100000] = 0):
    ts=int(time.time()) + minutes*60
    await interaction.response.send_message(f"🕒 <t:{ts}:F>\nCode: `<t:{ts}:F>` • Relative: `<t:{ts}:R>`", ephemeral=True)

@tools_group.command(name="snowflake", description="Get the creation date/time from a Discord ID")
@app_commands.describe(user_id="Discord ID (user/channel/message)")
async def tools_snowflake(interaction: discord.Interaction, user_id: str):
    try: dt=discord.utils.snowflake_time(int(str(user_id).strip()))
    except Exception:
        await interaction.response.send_message("❌ Give a valid numeric ID.", ephemeral=True); return
    await interaction.response.send_message(f"❄️ ID `{user_id}` was created on: **{dt.strftime('%d %b %Y, %H:%M UTC')}** (<t:{int(dt.timestamp())}:R>)", ephemeral=True)

@tools_group.command(name="uuid", description="Generate a random unique ID (UUID)")
async def tools_uuid(interaction: discord.Interaction):
    import uuid as _uuid
    await interaction.response.send_message(f"🆔 `{_uuid.uuid4()}`", ephemeral=True)

@tools_group.command(name="random", description="Pick a random number between two numbers")
@app_commands.describe(minimum="Smaller number", maximum="Larger number")
async def tools_random(interaction: discord.Interaction, minimum: int = 1, maximum: int = 100):
    if maximum < minimum:
        await interaction.response.send_message("❌ The maximum must be greater than the minimum.", ephemeral=True); return
    await interaction.response.send_message(f"🎲 Random number ({minimum}–{maximum}): **{random.randint(minimum, maximum)}**")

@tools_group.command(name="b64encode", description="Convert text to Base64")
@app_commands.describe(text="Which text should be encoded?")
async def tools_b64e(interaction: discord.Interaction, text: str):
    import base64 as _b64
    out=_b64.b64encode(text.encode()).decode()
    await interaction.response.send_message(f"🔐 `{out[:1800]}`", ephemeral=True)

@tools_group.command(name="b64decode", description="Convert Base64 back to text")
@app_commands.describe(text="Base64 text")
async def tools_b64d(interaction: discord.Interaction, text: str):
    import base64 as _b64
    try: out=_b64.b64decode(text.encode()).decode("utf-8", "replace")
    except Exception:
        await interaction.response.send_message("❌ That is not valid Base64.", ephemeral=True); return
    await interaction.response.send_message(f"🔓 `{out[:1800]}`", ephemeral=True)

@tools_group.command(name="sha256", description="Create a SHA-256 hash of text")
@app_commands.describe(text="Which text should be hashed?")
async def tools_sha(interaction: discord.Interaction, text: str):
    import hashlib as _hl
    await interaction.response.send_message(f"#️⃣ `{_hl.sha256(text.encode()).hexdigest()}`", ephemeral=True)

@tools_group.command(name="binary", description="Convert text to binary (0-1)")
@app_commands.describe(text="Text (max 100 characters)")
async def tools_binary(interaction: discord.Interaction, text: str):
    out=" ".join(format(ord(c), "08b") for c in text[:100])
    await interaction.response.send_message(f"💻 `{out[:1800]}`", ephemeral=True)

@tools_group.command(name="hex", description="Convert text to hex code")
@app_commands.describe(text="Text (max 200 characters)")
async def tools_hex(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔢 `{text[:200].encode().hex()[:1800]}`", ephemeral=True)

@tools_group.command(name="unit", description="Convert units: km, kg, temperature and more")
@app_commands.describe(kind="Which conversion?", value="How much?")
@app_commands.choices(kind=[
    app_commands.Choice(name="Kilometre → Miles", value="km_mi"),
    app_commands.Choice(name="Miles → Kilometre", value="mi_km"),
    app_commands.Choice(name="Kg → Pounds", value="kg_lb"),
    app_commands.Choice(name="Pounds → Kg", value="lb_kg"),
    app_commands.Choice(name="°C → °F", value="c_f"),
    app_commands.Choice(name="°F → °C", value="f_c"),
    app_commands.Choice(name="Metre → Feet", value="m_ft"),
    app_commands.Choice(name="Feet → Metre", value="ft_m"),
])
async def tools_unit(interaction: discord.Interaction, kind: app_commands.Choice[str], value: float):
    table={"km_mi": (value*0.621371, "miles"), "mi_km": (value*1.60934, "km"), "kg_lb": (value*2.20462, "pounds"),
           "lb_kg": (value*0.453592, "kg"), "c_f": (value*9/5+32, "°F"), "f_c": ((value-32)*5/9, "°C"),
           "m_ft": (value*3.28084, "feet"), "ft_m": (value*0.3048, "metre")}
    result, unit=table[kind.value]
    await interaction.response.send_message(f"📏 **{value}** → **{result:.2f} {unit}**")

@tools_group.command(name="coinamount", description="Explain an amount of coins in words")
@app_commands.describe(amount="How many coins?")
async def tools_coinamount(interaction: discord.Interaction, amount: app_commands.Range[int, 0, 1000000000]):
    await interaction.response.send_message(f"🪙 **{amount:,}** coins" + (" — lakhpati! 🎉" if amount >= 100000 else " — badhte raho! 💪"), ephemeral=True)

# ---------- YouTube (owner-only, /tools ke andar — naya top-level nahi) ----------
# Token code/repo me NAHI hai: Termux ki safe file se padha jata hai.
# Path badalna ho to .env me YT_TOKEN_FILE de sakte ho.
_YT_SCOPES=[
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
_YT_STATE_FILE=Path(__file__).resolve().parent/"yt_state.json"
def _yt_load_enabled():
    try: return bool(json.loads(_YT_STATE_FILE.read_text(encoding="utf-8")).get("enabled", True))
    except Exception: return True
_YT_ENABLED=_yt_load_enabled()
def _yt_set_enabled(val: bool):
    global _YT_ENABLED
    _YT_ENABLED=bool(val)
    try: _YT_STATE_FILE.write_text(json.dumps({"enabled": _YT_ENABLED}), encoding="utf-8")
    except Exception: pass
    return _YT_ENABLED
def _yt_token_ready():
    import os as _os
    f=_os.path.expanduser(_os.environ.get("YT_TOKEN_FILE", "~/.config/yt-agent/token.json"))
    return _os.path.exists(f) and _os.path.getsize(f) > 0
def _yt_status_text():
    return ("📺 **YouTube Status**\n"
            f"🔘 Haal: **{'ON 🟢' if _YT_ENABLED else 'OFF 🔴'}**\n"
            f"🔑 Login token: **{'found ✅' if _yt_token_ready() else 'not found ❌'}**\n"
            "For the confirmed channel report: `/tools youtube` → **Report**")

def _yt_service():
    import os as _os
    if not _YT_ENABLED:
        raise RuntimeError("YouTube is OFF. Turn it ON first: /tools youtube → On")
    token_file=_os.path.expanduser(_os.environ.get("YT_TOKEN_FILE", "~/.config/yt-agent/token.json"))
    if not _os.path.exists(token_file):
        raise RuntimeError("YouTube login not found. Connect the channel in Termux first with youtube_oauth.py.")
    try:
        from google.auth.transport.requests import Request as _Req
        from google.oauth2.credentials import Credentials as _Creds
        from googleapiclient.discovery import build as _build
    except Exception:
        raise RuntimeError("YouTube libraries are not installed. Run the setup again in Termux (requirements update).")
    creds=_Creds.from_authorized_user_file(token_file, _YT_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(_Req())
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
        try: _os.chmod(token_file, 0o600)
        except Exception: pass
    return _build("youtube", "v3", credentials=creds)
def _yt_channel(yt):
    items=yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute().get("items", [])
    if not items: raise RuntimeError("Channel not found — reconnect with the correct Gmail account.")
    return items[0]
def _yt_report_sync():
    yt=_yt_service(); ch=_yt_channel(yt); st=ch["statistics"]
    lines=[f"📺 **{ch['snippet']['title']}**",
           f"👥 Subscribers: **{'hidden' if st.get('hiddenSubscriberCount') else st.get('subscriberCount')}**",
           f"👀 Total views: **{st.get('viewCount')}**",
           f"🎬 Total videos: **{st.get('videoCount')}**"]
    uploads=ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items=yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=5).execute().get("items", [])
    ids=[i["contentDetails"]["videoId"] for i in items]
    if ids:
        lines.append("\n**Last 5 videos:**")
        vids=yt.videos().list(part="snippet,statistics", id=",".join(ids)).execute().get("items", [])
        for i, v in enumerate(vids, 1):
            vs=v["statistics"]
            lines.append(f"{i}. {v['snippet']['title'][:80]} — 👁️ {vs.get('viewCount', 0)} • ❤️ {vs.get('likeCount', 0)}")
    return "\n".join(lines)
def _yt_videos_sync():
    yt=_yt_service(); ch=_yt_channel(yt)
    uploads=ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items=yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=10).execute().get("items", [])
    ids=[i["contentDetails"]["videoId"] for i in items]
    if not ids: return "No videos found."
    vids=yt.videos().list(part="snippet,statistics,status", id=",".join(ids)).execute().get("items", [])
    lines=[f"🎬 **{ch['snippet']['title']} — Latest videos:**"]
    for i, v in enumerate(vids, 1):
        vs=v["statistics"]
        lines.append(f"{i}. [{v['status']['privacyStatus']}] {v['snippet']['title'][:80]} — 👁️ {vs.get('viewCount', 0)}")
    return "\n".join(lines)
def _yt_comments_sync():
    import html as _html, re as _re
    yt=_yt_service(); ch=_yt_channel(yt)
    resp=yt.commentThreads().list(part="snippet", allThreadsRelatedToChannelId=ch["id"], maxResults=10, order="time").execute()
    items=resp.get("items", [])
    if not items: return "💬 No comments found."
    lines=["💬 **Latest comments:**"]
    for i, it in enumerate(items, 1):
        c=it["snippet"]["topLevelComment"]["snippet"]
        txt=_html.unescape(_re.sub(r"<[^>]+>", " ", c.get("textDisplay") or "")).strip()
        lines.append(f"{i}. **{c.get('authorDisplayName')}**: {txt[:120]}")
    return "\n".join(lines)
def _yt_upload_sync(file_path, title, description, privacy):
    from googleapiclient.http import MediaFileUpload as _MFU
    yt=_yt_service()
    body={"snippet": {"title": title, "description": description or "", "categoryId": "20"},
          "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    media=_MFU(file_path, chunksize=8*1024*1024, resumable=True)
    req=yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=False)
    resp=None
    while resp is None:
        _status, resp=req.next_chunk()
    return resp["id"], privacy
async def _yt_owner_gate(interaction: discord.Interaction, label: str = "YouTube") -> bool:
    if interaction.user.id != await _get_bot_owner_id():
        await interaction.response.send_message(f"❌ {label} commands are only for the bot owner.", ephemeral=True)
        return False
    return True

def _bot_health_text():
    sec=int(time.time()-START_TIME); d,sec=divmod(sec,86400); h,sec=divmod(sec,3600); m,sec=divmod(sec,60)
    uptime=(f"{d}d " if d else "") + f"{h}h {m}m {sec}s"
    import math as _math
    _lat = bot.latency if bot.latency is not None else 0
    ping_ms = round(_lat*1000) if _math.isfinite(_lat) else 0
    import os as _os
    token_file=_os.path.expanduser(_os.environ.get("YT_TOKEN_FILE", "~/.config/yt-agent/token.json"))
    yt_ok = _os.path.exists(token_file) and _os.path.getsize(token_file) > 0
    return (f"🟢 **Nezu is online!**\n"
            f"⏱️ Uptime: **{uptime}**\n"
            f"📡 Ping: **{ping_ms} ms**\n"
            f"🏠 Servers: **{len(bot.guilds)}**\n"
            f"📺 YouTube: **{'OFF 🔴' if not _YT_ENABLED else ('Connected ✅' if yt_ok else 'Not connected ❌')}**\n"
            f"💾 Hosting: Termux (phone)")

@tools_group.command(name="health", description="Bot health: uptime, ping, and whether YouTube is connected (owner only)")
async def tools_health(interaction: discord.Interaction):
    if not await _yt_owner_gate(interaction): return
    await interaction.response.send_message(_bot_health_text(), ephemeral=True)

@tools_group.command(name="ticketpanel", description="Post the ticket panel (HELP / BUG REPORT / IDEA buttons) in this channel (mods only)")
async def tools_ticketpanel(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Please use this inside a server text channel.", ephemeral=True); return
    if not _is_mod(interaction):
        await _deny(interaction); return
    embed=discord.Embed(title="SOME ISSUES?", color=0xff6b9d)
    embed.description="**NEED SOME HELP OR WANT TO CONTACT US?**\n\n🎟️ Tap a button below to open your case"
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("✅ Ticket panel posted in this channel.", ephemeral=True)

def _yt_natural_action(content: str, bot_user_id=None):
    text=(content or "").strip()
    if bot_user_id:
        text=text.replace(f"<@{bot_user_id}>", "").replace(f"<@!{bot_user_id}>", "")
    norm=" ".join(text.lower().split())
    if norm in ("bot health", "bot status", "nezu health", "nezu status", "health"):
        return ("health", "")
    if norm in ("yt on", "youtube on"): return ("yt_on", "")
    if norm in ("yt off", "youtube off"): return ("yt_off", "")
    if norm in ("yt status", "youtube status"): return ("yt_status", "")
    if norm in ("yt report", "youtube report", "yt channel report", "youtube channel report"):
        return ("report", "")
    if norm in ("yt videos", "youtube videos", "yt latest videos", "youtube latest videos", "yt video list"):
        return ("videos", "")
    if norm in ("yt comments", "youtube comments", "yt latest comments", "youtube latest comments"):
        return ("comments", "")
    for pre in ("yt upload", "youtube upload"):
        if norm == pre or norm.startswith(pre + " "):
            return ("upload", text[len(pre):].strip(" -:"))
    return (None, "")

async def _handle_yt_natural(message: discord.Message) -> bool:
    action, title=_yt_natural_action(message.content, bot.user.id if bot.user else None)
    if action is None: return False
    if message.author.id != await _get_bot_owner_id(): return False
    if action == "upload":
        if not message.attachments:
            await message.reply("🎬 Attach a video file and write `yt upload Title` in the caption — it will be uploaded privately.", mention_author=False)
            return True
        att=message.attachments[0]
        if att.size > 100*1024*1024:
            await message.reply("❌ The file is larger than 100MB.", mention_author=False); return True
        soch=await message.reply("⏫ Uploading privately to YouTube...", mention_author=False)
        import tempfile as _tf
        fd, tmp=_tf.mkstemp(prefix="ytup_", suffix=Path(att.filename).suffix or ".mp4"); os.close(fd)
        try:
            await att.save(tmp)
            vid, priv=await asyncio.to_thread(_yt_upload_sync, tmp, (title or Path(att.filename).stem)[:100], "", "private")
            await soch.edit(content=f"✅ **YouTube upload complete!** ({priv})\n🔗 https://youtu.be/{vid}")
        except Exception as e:
            await soch.edit(content=f"❌ Upload fail: {e}")
        finally:
            try: os.unlink(tmp)
            except Exception: pass
        return True
    if action == "health":
        await message.reply(_bot_health_text(), mention_author=False)
        return True
    if action == "yt_on":
        _yt_set_enabled(True); await message.reply("🟢 **YouTube is ON.**", mention_author=False); return True
    if action == "yt_off":
        _yt_set_enabled(False); await message.reply("🔴 **YouTube is OFF.** Reports/uploads will not work.", mention_author=False); return True
    if action == "yt_status":
        await message.reply(_yt_status_text(), mention_author=False); return True
    if not _YT_ENABLED:
        await message.reply("🔴 YouTube is currently **OFF**. Type `yt on` first.", mention_author=False); return True
    fn={"report": _yt_report_sync, "videos": _yt_videos_sync, "comments": _yt_comments_sync}[action]
    soch=await message.reply("📺 Fetching data from YouTube...", mention_author=False)
    try:
        txt=await asyncio.to_thread(fn)
    except Exception as e:
        txt=f"❌ Fail: {e}"
    await soch.edit(content=txt[:1900])
    return True


# ---------------- INSTAGRAM (Meta Graph API, owner only) ----------------
_IG_STATE_FILE=Path(__file__).resolve().parent/"ig_state.json"
def _load_ig_enabled():
    try: return bool(json.loads(_IG_STATE_FILE.read_text(encoding="utf-8")).get("enabled", True))
    except Exception: return True
_IG_ENABLED=_load_ig_enabled()
def _ig_set_enabled(val: bool):
    global _IG_ENABLED
    _IG_ENABLED=bool(val)
    try: _IG_STATE_FILE.write_text(json.dumps({"enabled": _IG_ENABLED}), encoding="utf-8")
    except Exception: log.warning("ig state save fail\n%s", traceback.format_exc())
def _ig_config():
    return {
        "token": os.environ.get("IG_ACCESS_TOKEN", "").strip(),
        "user_id": os.environ.get("IG_USER_ID", "").strip(),
        "version": os.environ.get("IG_GRAPH_VERSION", "v21.0").strip() or "v21.0",
    }
def _ig_connected() -> bool:
    cfg=_ig_config(); return bool(cfg["token"] and cfg["user_id"])
def _ig_status_text() -> str:
    cfg=_ig_config(); connected=_ig_connected()
    lines=["📸 **Instagram**",
           f"Power: **{'ON 🟢' if _IG_ENABLED else 'OFF 🔴'}**",
           f"Connection: **{'Connected ✅' if connected else 'Not connected ❌'}**"]
    if connected:
        lines.append(f"Instagram ID: `{cfg['user_id']}`")
        lines.append("Use Profile, Media, Comments or Post actions.")
    else:
        lines.append("Setup: put `IG_ACCESS_TOKEN` and `IG_USER_ID` in the Termux `.env` file, then restart the bot. I will guide you through getting them from the Meta app — never paste the token in chat.")
    return "\n".join(lines)
async def _ig_request(method: str, endpoint: str, params: dict = None, data: dict = None) -> dict:
    cfg=_ig_config()
    if not (cfg["token"] and cfg["user_id"]):
        raise RuntimeError("Instagram is not connected yet. Add IG_ACCESS_TOKEN and IG_USER_ID in Termux .env, then restart the bot.")
    q=dict(params or {}); q["access_token"]=cfg["token"]
    url=f"https://graph.facebook.com/{cfg['version']}/{endpoint.lstrip('/')}"
    async with _sess() as session:
        async with session.request(method, url, params=q if method.upper()=="GET" else None, data=(dict(data or {}, access_token=cfg["token"]) if method.upper()!="GET" else None), timeout=aiohttp.ClientTimeout(total=30)) as resp:
            payload=await resp.json(content_type=None)
            if resp.status != 200 or (isinstance(payload, dict) and payload.get("error")):
                err=(payload.get("error") or {}) if isinstance(payload, dict) else {}
                raise RuntimeError(err.get("message") or f"Instagram API error ({resp.status})")
            return payload if isinstance(payload, dict) else {}
async def _ig_profile_text() -> str:
    cfg=_ig_config()
    d=await _ig_request("GET", cfg["user_id"], params={"fields": "id,username,name,account_type,followers_count,follows_count,media_count,biography,website"})
    return ("📸 **Instagram Profile**\n"
            f"👤 Username: **@{d.get('username', '?')}**\n"
            f"🧾 Name: **{d.get('name', '-')}**\n"
            f"🏷️ Type: **{d.get('account_type', '-')}**\n"
            f"👥 Followers: **{d.get('followers_count', '-')}** • Following: **{d.get('follows_count', '-')}**\n"
            f"🎞️ Posts/media: **{d.get('media_count', '-')}**\n"
            f"🔗 Website: {d.get('website') or '-'}")
async def _ig_media_text() -> str:
    cfg=_ig_config()
    d=await _ig_request("GET", f"{cfg['user_id']}/media", params={"fields": "id,caption,media_type,permalink,timestamp,like_count,comments_count", "limit": 10})
    items=d.get("data") or []
    if not items: return "🎞️ No Instagram media found."
    lines=["🎞️ **Latest Instagram media:**"]
    for i, m in enumerate(items, 1):
        cap=" ".join(str(m.get("caption") or "").split())[:90] or "(no caption)"
        lines.append(f"{i}. [{m.get('media_type', 'MEDIA')}] {cap}\n   ❤️ {m.get('like_count', 0)} • 💬 {m.get('comments_count', 0)} • {m.get('permalink', '')}")
    return "\n".join(lines)[:1900]
async def _ig_comments_text() -> str:
    cfg=_ig_config()
    media=await _ig_request("GET", f"{cfg['user_id']}/media", params={"fields": "id,caption,permalink", "limit": 5})
    posts=media.get("data") or []
    if not posts: return "💬 No posts found, so no comments to show."
    lines=["💬 **Latest Instagram comments:**"]
    found=False
    for post in posts[:5]:
        try:
            comments=await _ig_request("GET", f"{post['id']}/comments", params={"fields": "username,text,timestamp,like_count", "limit": 5})
        except Exception:
            continue
        for c in comments.get("data") or []:
            found=True
            txt=" ".join(str(c.get("text") or "").split())[:130]
            lines.append(f"• **@{c.get('username', '?')}**: {txt}")
            if len(lines) >= 14: return "\n".join(lines)[:1900]
    if not found: lines.append("No comments found on the latest posts.")
    return "\n".join(lines)[:1900]
async def _ig_post_image(image_url: str, caption: str) -> str:
    cfg=_ig_config()
    if not image_url.startswith(("https://", "http://")):
        raise RuntimeError("Post needs a public image URL (https://...). Instagram cannot read a private file directly.")
    created=await _ig_request("POST", f"{cfg['user_id']}/media", data={"image_url": image_url, "caption": caption[:2200]})
    creation_id=created.get("id")
    if not creation_id: raise RuntimeError("Instagram did not return a media container ID.")
    done=await _ig_request("POST", f"{cfg['user_id']}/media_publish", data={"creation_id": creation_id})
    return str(done.get("id") or creation_id)

@tools_group.command(name="instagram", description="Instagram in one place: Status, On, Off, Profile, Media, Comments, Post (owner only)")
@app_commands.describe(action="What should I do?", image_url="Post action: public image URL", caption="Post action: caption")
@app_commands.choices(action=[
    app_commands.Choice(name="Status — On/Off and connection", value="status"),
    app_commands.Choice(name="On — enable Instagram", value="on"),
    app_commands.Choice(name="Off — disable Instagram", value="off"),
    app_commands.Choice(name="Profile — account profile", value="profile"),
    app_commands.Choice(name="Media — latest posts/reels", value="media"),
    app_commands.Choice(name="Comments — latest comments", value="comments"),
    app_commands.Choice(name="Post — publish one public image URL", value="post"),
])
async def tools_instagram(interaction: discord.Interaction, action: str = "status", image_url: str = "", caption: str = ""):
    if not await _yt_owner_gate(interaction, "Instagram"): return
    act=(action or "status").lower()
    if act == "on":
        _ig_set_enabled(True); await interaction.response.send_message("🟢 **Instagram is ON.**", ephemeral=True); return
    if act == "off":
        _ig_set_enabled(False); await interaction.response.send_message("🔴 **Instagram is OFF.**", ephemeral=True); return
    if act == "status":
        await interaction.response.send_message(_ig_status_text(), ephemeral=True); return
    if not _IG_ENABLED:
        await interaction.response.send_message("🔴 Instagram is currently **OFF**. Run the **On** action first.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        if act == "profile": txt=await _ig_profile_text()
        elif act == "media": txt=await _ig_media_text()
        elif act == "comments": txt=await _ig_comments_text()
        elif act == "post":
            if not image_url:
                await interaction.followup.send("❌ For **Post**, give a public `image_url` and a caption.", ephemeral=True); return
            media_id=await _ig_post_image(image_url.strip(), caption)
            txt=f"✅ **Instagram post published!**\nMedia ID: `{media_id}`"
        else: txt="❌ Invalid action."
    except Exception as e:
        txt=f"❌ Instagram fail: {e}"
    await interaction.followup.send(txt[:1900], ephemeral=True)

@tools_group.command(name="youtube", description="YouTube in one place: Status, On, Off, Report, Videos, Comments, Upload (owner only)")
@app_commands.describe(action="What should I do?", video="Video file for upload", title="Upload title (blank = file name)", description="Upload: description", privacy="Upload: Private/Unlisted/Public")
@app_commands.choices(action=[
    app_commands.Choice(name="Status — On/Off and whether you are logged in", value="status"),
    app_commands.Choice(name="On — enable YouTube", value="on"),
    app_commands.Choice(name="Off — disable YouTube", value="off"),
    app_commands.Choice(name="Report — channel report", value="report"),
    app_commands.Choice(name="Videos — latest videos", value="videos"),
    app_commands.Choice(name="Comments — latest comments", value="comments"),
    app_commands.Choice(name="Upload — upload a video to YouTube", value="upload"),
], privacy=[
    app_commands.Choice(name="Private — visible only to me", value="private"),
    app_commands.Choice(name="Unlisted — visible to anyone with the link", value="unlisted"),
    app_commands.Choice(name="Public — visible to everyone", value="public"),
])
async def tools_youtube(interaction: discord.Interaction, action: str = "status", video: discord.Attachment = None, title: str = "", description: str = "", privacy: str = "private"):
    if not await _yt_owner_gate(interaction): return
    act=(action or "status").lower()
    if act == "on":
        _yt_set_enabled(True); await interaction.response.send_message("🟢 **YouTube is ON.**", ephemeral=True); return
    if act == "off":
        _yt_set_enabled(False); await interaction.response.send_message("🔴 **YouTube is OFF.** Reports/uploads will not work.", ephemeral=True); return
    if act == "status":
        await interaction.response.send_message(_yt_status_text(), ephemeral=True); return
    if not _YT_ENABLED:
        await interaction.response.send_message("🔴 YouTube is currently **OFF**. Run the **On** action first.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        if act == "report":
            txt=await asyncio.to_thread(_yt_report_sync)
        elif act == "videos":
            txt=await asyncio.to_thread(_yt_videos_sync)
        elif act == "comments":
            txt=await asyncio.to_thread(_yt_comments_sync)
        elif act == "upload":
            if video is None:
                await interaction.followup.send("❌ Please also attach a **video file** for the upload.", ephemeral=True); return
            if video.size > 100*1024*1024:
                await interaction.followup.send("❌ The file is larger than 100MB. Use a smaller file or the Termux method.", ephemeral=True); return
            import tempfile as _tf
            fd, tmp=_tf.mkstemp(prefix="ytup_", suffix=Path(video.filename).suffix or ".mp4"); os.close(fd)
            try:
                await video.save(tmp)
                vid, priv=await asyncio.to_thread(_yt_upload_sync, tmp, (title or Path(video.filename).stem)[:100], description, privacy)
            finally:
                try: os.unlink(tmp)
                except Exception: pass
            await interaction.followup.send(f"✅ **YouTube upload complete!** ({priv})\n🔗 https://youtu.be/{vid}", ephemeral=True); return
        else:
            txt="❌ Invalid action."
    except Exception as e:
        txt=f"❌ Fail: {e}"
    await interaction.followup.send(txt[:1900], ephemeral=True)


@tools_group.command(name="embed", description="Create and send a clean embed message to a channel (mods only)")
@app_commands.describe(title="Embed title", description="Embed text", channel="Channel to send it in (blank = this channel)", color="Embed color")
@app_commands.choices(color=[
    app_commands.Choice(name="Purple", value="purple"), app_commands.Choice(name="Blue", value="blue"),
    app_commands.Choice(name="Green", value="green"), app_commands.Choice(name="Gold", value="gold"),
    app_commands.Choice(name="Red", value="red"), app_commands.Choice(name="Pink", value="pink"),
])
async def tools_embed(interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel = None, color: str = "purple"):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ This only works inside a server text channel.", ephemeral=True); return
    if not _is_mod(interaction):
        await _deny(interaction); return
    ch=channel or interaction.channel
    colors={"purple":0x9b59b6,"blue":0x3498db,"green":0x2ecc71,"gold":0xf1c40f,"red":0xe74c3c,"pink":0xff6b9d}
    embed=discord.Embed(title=str(title)[:256], description=str(description)[:3900], color=colors.get(color, 0x9b59b6))
    embed.set_footer(text=f"Posted by {interaction.user.display_name}")
    try:
        await ch.send(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ I cannot send messages in {ch.mention}.", ephemeral=True); return
    await interaction.response.send_message(f"✅ Embed sent to {ch.mention}.", ephemeral=True)

# ---------- /text (10) ----------
@text_group.command(name="reverse", description="Reverse text")
@app_commands.describe(text="Which text?")
async def text_reverse(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:500][::-1])

@text_group.command(name="upper", description="Convert text to UPPERCASE letters")
@app_commands.describe(text="Which text?")
async def text_upper(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:1000].upper())

@text_group.command(name="lower", description="Convert text to lowercase letters")
@app_commands.describe(text="Which text?")
async def text_lower(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:1000].lower())

@text_group.command(name="title", description="Capitalize the first letter of every word")
@app_commands.describe(text="Which text?")
async def text_title(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:1000].title())

@text_group.command(name="count", description="Count letters, words and lines in text")
@app_commands.describe(text="Which text should be counted?")
async def text_count(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔢 Letters: **{len(text)}** • Words: **{len(text.split())}** • Lines: **{len(text.splitlines()) or 1}**", ephemeral=True)

@text_group.command(name="repeat", description="Repeat text multiple times")
@app_commands.describe(text="Which text?", times="How many times? (1-10)")
async def text_repeat(interaction: discord.Interaction, text: str, times: app_commands.Range[int, 1, 10] = 3):
    await interaction.response.send_message((text[:200] + "\n") * times)

@text_group.command(name="spoiler", description="Send text as a hidden spoiler")
@app_commands.describe(text="Which text should be hidden?")
async def text_spoiler(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"||{text[:500]}||")

@text_group.command(name="mock", description="Convert text to aLtErNaTiNg case (fun)")
@app_commands.describe(text="Which text?")
async def text_mock(interaction: discord.Interaction, text: str):
    out="".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text[:500]))
    await interaction.response.send_message(out)

@text_group.command(name="vowels", description="Count vowels (a,e,i,o,u) in text")
@app_commands.describe(text="Which text?")
async def text_vowels(interaction: discord.Interaction, text: str):
    low=text.lower(); ginti={v: low.count(v) for v in "aeiou"}
    await interaction.response.send_message("🔤 " + " • ".join(f"**{v}**: {n}" for v, n in ginti.items()) + f"\nTotal vowels: **{sum(ginti.values())}**", ephemeral=True)

@text_group.command(name="slug", description="Convert text into a link-style slug (hello-world type)")
@app_commands.describe(text="Which text?")
async def text_slug(interaction: discord.Interaction, text: str):
    slug=re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:200]
    await interaction.response.send_message(f"🔗 `{slug or 'empty'}`", ephemeral=True)

# ---------- /actions (12, factory-made, with GIFs) ----------
_ACTION_GIF_ENDPOINTS={
    "hug": "hug", "pat": "pat", "slap": "slap", "wave": "wave",
    "highfive": "highfive", "dance": "dance", "cry": "cry", "laugh": "laugh",
    "angry": "angry", "sleep": "sleep", "eat": "feed", "thumbsup": "thumbsup",
}
async def _action_gif_url(action_name: str):
    """Fetch a fresh anime action GIF (nekos.best). Returns None if it fails."""
    endpoint=_ACTION_GIF_ENDPOINTS.get(action_name, action_name)
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"https://nekos.best/api/v2/{endpoint}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200: return None
                data=await resp.json()
        results=data.get("results") or []
        return results[0].get("url") if results else None
    except Exception:
        return None

def _reg_action(action_name, line):
    @actions_group.command(name=action_name, description=f"Send a GIF for the {action_name} action to a member")
    @app_commands.describe(member="Which member? (blank = yourself)")
    async def _act(interaction: discord.Interaction, member: discord.Member = None):
        target=member or interaction.user
        await interaction.response.defer()
        gif_url=await _action_gif_url(action_name)
        embed=discord.Embed(description=f"{interaction.user.mention} → {target.mention}: **{line}**", color=0x9b59b6)
        if gif_url: embed.set_image(url=gif_url)
        await interaction.followup.send(embed=embed)
    return _act
for _action_name, _action_line in [
    ("hug", "gave a warm hug!"), ("pat", "gently patted their head!"), ("slap", "gave a playful slap! (just for fun)"),
    ("wave", "waved — hello!"), ("highfive", "gave a big high-five!"), ("dance", "started dancing together!"),
    ("cry", "pretended to cry!"), ("laugh", "burst out laughing!"), ("angry", "pretended to be angry!"),
    ("sleep", "went off to sleep — good night!"), ("eat", "sat down to eat together!"), ("thumbsup", "gave a thumbs up — great job!"),
]:
    _reg_action(_action_name, _action_line)

# ---------- Scrims + Reports storage (Quo-style useful tools) ----------
_SCRIMS_FILE = Path(__file__).resolve().parent / "scrims.json"
_REPORTS_FILE = Path(__file__).resolve().parent / "reports.json"
def _load_json_dict(path):
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
_scrims: dict = _load_json_dict(_SCRIMS_FILE)
_reports: dict = _load_json_dict(_REPORTS_FILE)
def _save_scrims():
    try: _SCRIMS_FILE.write_text(json.dumps(_scrims), encoding="utf-8")
    except Exception: log.warning("scrims save fail\n%s", traceback.format_exc())
def _save_reports():
    try: _REPORTS_FILE.write_text(json.dumps(_reports), encoding="utf-8")
    except Exception: log.warning("reports save fail\n%s", traceback.format_exc())
def _scrim_bucket(guild_id: int) -> dict:
    return _scrims.setdefault(str(guild_id), {"next_id": 1, "items": {}})
def _report_bucket(guild_id: int) -> dict:
    return _reports.setdefault(str(guild_id), {"next_id": 1, "items": []})
def _find_scrim(guild_id: int, scrim_id: int):
    return _scrim_bucket(guild_id).setdefault("items", {}).get(str(int(scrim_id or 0)))
def _member_badges(guild, member) -> list:
    badges=[]
    try:
        if getattr(member, "bot", False): badges.append("🤖 Bot Account")
        perms=getattr(member, "guild_permissions", None)
        if perms and perms.administrator: badges.append("👑 Administrator")
        elif perms and (perms.manage_guild or perms.kick_members or perms.ban_members): badges.append("🛡️ Moderator")
        if getattr(member, "premium_since", None): badges.append("🚀 Server Booster")
        joined=getattr(member, "joined_at", None)
        if joined:
            days=max(0, int((datetime.datetime.now(datetime.timezone.utc)-joined).total_seconds()//86400))
            if days >= 730: badges.append("🏆 OG Member (2+ years)")
            elif days >= 365: badges.append("🎖️ Veteran Member (1+ year)")
            elif days >= 30: badges.append("🌱 Rising Member (30+ days)")
            else: badges.append("✨ New Member")
        rec=_levels.get(str(guild.id), {}).get(str(member.id), {}) if guild else {}
        level, _, _=_level_progress(int(rec.get("xp", 0)))
        if level >= 10: badges.append("⭐ Legend Chatter (Level 10+)")
        elif level >= 5: badges.append("🔥 Super Active (Level 5+)")
        elif level >= 3: badges.append("💬 Active Chatter (Level 3+)")
        eco=_eco_rec(guild.id, member.id) if guild else {"coins": 0}
        coins=int(eco.get("coins", 0))
        if coins >= 10000: badges.append("💎 Coin Millionaire (10k+ coins)")
        elif coins >= 1000: badges.append("💰 Rich Member (1k+ coins)")
        elif coins >= 250: badges.append("🪙 Coin Saver (250+ coins)")
        for item in _scrim_bucket(guild.id).get("items", {}).values():
            if str(member.id) in [str(v) for v in item.get("reservations", {}).values()]:
                badges.append("🎮 Scrim Player")
                break
    except Exception:
        log.warning("badge calc fail\n%s", traceback.format_exc())
    return badges or ["🌟 Starter Badge"]

# ---------- /server (12) ----------
def _server_lazmi(interaction):
    return interaction.guild is not None

@server_group.command(name="stats", description="Total server numbers: members, bots, channels")
async def server_stats(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    g=interaction.guild
    humans=sum(1 for m in g.members if not m.bot); bots=len(g.members) - humans
    embed=discord.Embed(title=f"📊 Stats for {g.name}", color=0x3498db)
    embed.add_field(name="👥 Members", value=f"{g.member_count}", inline=True)
    embed.add_field(name="🧑 Humans", value=f"{humans}", inline=True)
    embed.add_field(name="🤖 Bots", value=f"{bots}", inline=True)
    embed.add_field(name="💬 Text Channels", value=f"{len(g.text_channels)}", inline=True)
    embed.add_field(name="🔊 Voice Channels", value=f"{len(g.voice_channels)}", inline=True)
    embed.add_field(name="🎭 Roles", value=f"{len(g.roles)}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@server_group.command(name="icon", description="View the server icon/photo in large size")
async def server_icon(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    if interaction.guild.icon:
        embed=discord.Embed(title=f"🖼️ Icon of {interaction.guild.name}", color=0x9b59b6)
        embed.set_image(url=interaction.guild.icon.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ This server has no icon.", ephemeral=True)

@server_group.command(name="serverbanner", description="View the server banner (if it has one)")
async def server_banner(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    banner=getattr(interaction.guild, "banner", None)
    if banner:
        embed=discord.Embed(title=f"🖼️ Banner of {interaction.guild.name}", color=0x9b59b6)
        embed.set_image(url=banner.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ This server has no banner (a boost level is required).", ephemeral=True)

@server_group.command(name="emojis", description="List of custom emojis in this server")
async def server_emojis(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    emojis=list(interaction.guild.emojis)
    if not emojis:
        await interaction.response.send_message("ℹ️ This server has no custom emojis.", ephemeral=True); return
    txt=" ".join(str(e) for e in emojis[:40])
    await interaction.response.send_message(f"😀 **{len(emojis)} custom emojis:**\n{txt}"[:1900], ephemeral=True)

@server_group.command(name="rolescount", description="See how many roles the server has and which is highest")
async def server_rolescount(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    g=interaction.guild
    top=max(g.roles, key=lambda r: r.position)
    await interaction.response.send_message(f"🎭 **{g.name}** has **{len(g.roles)} roles** in total.\nHighest role: {top.mention}", ephemeral=True)

@server_group.command(name="channels", description="Count and list all server channels (first 30)")
async def server_channels(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    g=interaction.guild
    names=[f"#{c.name}" for c in g.text_channels[:30]]
    await interaction.response.send_message(f"📺 Total channels: **{len(g.channels)}** (text {len(g.text_channels)}, voice {len(g.voice_channels)}, categories {len(g.categories)})\n" + ", ".join(names), ephemeral=True)

@server_group.command(name="boosts", description="Server boosts and boost level")
async def server_boosts(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    g=interaction.guild
    await interaction.response.send_message(f"🚀 **{g.name}** — Boost Level: **{g.premium_tier}** • Total boosts: **{g.premium_subscription_count or 0}**", ephemeral=True)

@server_group.command(name="owner", description="See who owns the server")
async def server_owner(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    await interaction.response.send_message(f"👑 Owner of **{interaction.guild.name}**: <@{interaction.guild.owner_id}> (`{interaction.guild.owner_id}`)", ephemeral=True)

@server_group.command(name="created", description="See when the server was created")
async def server_created(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    dt=interaction.guild.created_at
    await interaction.response.send_message(f"🎂 **{interaction.guild.name}** was created on: **{dt.strftime('%d %b %Y')}** (<t:{int(dt.timestamp())}:R>)", ephemeral=True)

@server_group.command(name="afk", description="Server AFK channel and timeout")
async def server_afk(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    g=interaction.guild
    ch=g.afk_channel.mention if g.afk_channel else "not set"
    await interaction.response.send_message(f"💤 AFK channel: **{ch}** • AFK timeout: **{g.afk_timeout} second**", ephemeral=True)

@server_group.command(name="verification", description="Server verification level and content filter")
async def server_verification(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    g=interaction.guild
    await interaction.response.send_message(f"🛡️ Verification level: **{str(g.verification_level).title()}** • Content filter: **{str(g.explicit_content_filter).title()}**", ephemeral=True)

@server_group.command(name="welcome", description="Auto-welcome On/Off/Status (default: ON, no setup needed)")
@app_commands.describe(action="On, Off or Status")
@app_commands.choices(action=[
    app_commands.Choice(name="On — turn auto-welcome on", value="on"),
    app_commands.Choice(name="Off — turn auto-welcome off", value="off"),
    app_commands.Choice(name="Status — check status", value="status"),
])
async def server_welcome(interaction: discord.Interaction, action: str = "status"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    if action in ("on", "off") and not _is_admin(interaction):
        await interaction.response.send_message("❌ Only server **admins** can turn welcome On/Off. (It is ON by default anyway, no setup needed.)", ephemeral=True); return
    if action == "on":
        _set_welcome(interaction.guild.id, True)
        await interaction.response.send_message("🌸 **Auto-Welcome ON!** Every new member will now be welcomed automatically — no setup needed.", ephemeral=True)
    elif action == "off":
        _set_welcome(interaction.guild.id, False)
        await interaction.response.send_message("🌸 Auto-Welcome has been turned **OFF** for this server.", ephemeral=True)
    else:
        state="ON ✅ (default — runs automatically, no setup needed)" if _welcome_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🌸 Auto-Welcome in this server: **{state}**", ephemeral=True)

@server_group.command(name="features", description="See special server features (community, partner, etc.)")
async def server_features(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    feats=interaction.guild.features
    txt=", ".join(f"`{f}`" for f in feats) if feats else "no special features"
    await interaction.response.send_message(f"✨ **Features:** {txt}"[:1900], ephemeral=True)


@server_group.command(name="settings", description="Server settings in one place: Status, On or Off for protection and welcome features")
@app_commands.describe(setting="Which setting?", action="Status, On or Off")
@app_commands.choices(setting=[
    app_commands.Choice(name="Welcome — automatic member welcome", value="welcome"),
    app_commands.Choice(name="AntiLink — delete links", value="antilink"),
    app_commands.Choice(name="AntiSpam — delete flood/spam", value="antispam"),
    app_commands.Choice(name="BadWords — abusive-word filter", value="badwords"),
    app_commands.Choice(name="AntiRaid — mass-join alerts", value="antiraid"),
    app_commands.Choice(name="AntiNuke — mass delete/ban protection", value="antinuke"),
    app_commands.Choice(name="All Protection — AntiLink + AntiSpam + BadWords + AntiRaid + AntiNuke", value="all_protection"),
], action=[
    app_commands.Choice(name="Status — show current state", value="status"),
    app_commands.Choice(name="On — turn it on (mods only)", value="on"),
    app_commands.Choice(name="Off — turn it off (mods only)", value="off"),
])
async def server_settings(interaction: discord.Interaction, setting: str = "welcome", action: str = "status"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id); cfg=_automod.setdefault(gid, {})
    keys={"antilink": "antilink", "antispam": "antispam", "badwords": "badwords", "antiraid": "antiraid", "antinuke": "antinuke"}
    labels={"welcome": "Welcome", "antilink": "AntiLink", "antispam": "AntiSpam", "badwords": "BadWords", "antiraid": "AntiRaid", "antinuke": "AntiNuke", "all_protection": "All Protection"}
    def state_of(name):
        if name == "welcome": return _welcome_enabled(interaction.guild.id)
        if name == "all_protection": return all(bool(cfg.get(k, False)) for k in keys.values())
        return bool(cfg.get(keys[name], False))
    if action in ("on", "off"):
        on=(action == "on")
        if setting == "welcome": _set_welcome(interaction.guild.id, on)
        elif setting == "all_protection":
            for k in keys.values(): cfg[k]=on
            _save_automod()
        else:
            cfg[keys[setting]]=on; _save_automod()
        await interaction.response.send_message(f"✅ **{labels[setting]}** is now **{'ON' if on else 'OFF'}**.", ephemeral=True); return
    lines=[]
    for name in ("welcome", "antilink", "antispam", "badwords", "antiraid", "antinuke"):
        lines.append(f"{'🟢' if state_of(name) else '🔴'} **{labels[name]}:** {'ON' if state_of(name) else 'OFF'}")
    embed=discord.Embed(title=f"⚙️ Settings for {interaction.guild.name}", description="\n".join(lines), color=0x3498db)
    embed.set_footer(text="Use /server settings, choose a setting, then On or Off. Mods only for changes.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@server_group.command(name="badge", description="Show badges earned by you or another member")
@app_commands.describe(member="Whose badges? Leave blank for yourself")
async def server_badge(interaction: discord.Interaction, member: discord.Member = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    target=member or interaction.user
    badges=_member_badges(interaction.guild, target)
    rec=_levels.get(str(interaction.guild.id), {}).get(str(target.id), {"xp": 0, "messages": 0})
    level, _, _=_level_progress(int(rec.get("xp", 0)))
    eco=_eco_rec(interaction.guild.id, target.id)
    embed=discord.Embed(title=f"🏅 Badges of {target.display_name}", color=0xf1c40f)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.description="\n".join(f"• {b}" for b in badges)
    embed.add_field(name="⭐ Level", value=str(level), inline=True)
    embed.add_field(name="🪙 Coins", value=f"{int(eco.get('coins', 0)):,}", inline=True)
    embed.add_field(name="💬 Messages", value=str(rec.get("messages", 0)), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@server_group.command(name="reports", description="Moderator report inbox: list, resolve or clear member reports")
@app_commands.describe(action="What should I do?", report_id="Report ID for Resolve")
@app_commands.choices(action=[
    app_commands.Choice(name="List — open reports first", value="list"),
    app_commands.Choice(name="Resolve — mark one report resolved", value="resolve"),
    app_commands.Choice(name="Clear Resolved — delete resolved reports", value="clear_resolved"),
    app_commands.Choice(name="Stats — report counts", value="stats"),
])
async def server_reports(interaction: discord.Interaction, action: str = "list", report_id: int = 0):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    if not _is_mod(interaction):
        await _deny(interaction); return
    bucket=_report_bucket(interaction.guild.id); items=bucket.setdefault("items", [])
    if action == "resolve":
        for r in items:
            if int(r.get("id", 0)) == int(report_id):
                r["resolved"]=True; _save_reports()
                await interaction.response.send_message(f"✅ Report **#{report_id}** marked resolved.", ephemeral=True); return
        await interaction.response.send_message(f"❌ Report **#{report_id}** was not found.", ephemeral=True); return
    if action == "clear_resolved":
        before=len(items); bucket["items"]=[r for r in items if not r.get("resolved")]; _save_reports()
        await interaction.response.send_message(f"🧹 Cleared **{before-len(bucket['items'])}** resolved report(s).", ephemeral=True); return
    if action == "stats":
        open_n=sum(1 for r in items if not r.get("resolved")); done_n=len(items)-open_n
        await interaction.response.send_message(f"🚨 Reports: **{len(items)} total** • **{open_n} open** • **{done_n} resolved**", ephemeral=True); return
    if not items:
        await interaction.response.send_message("ℹ️ No reports saved yet. Members can use `/report`.", ephemeral=True); return
    open_items=[r for r in items if not r.get("resolved")][-10:]
    show=open_items or items[-10:]
    lines=[]
    for r in show:
        lines.append(f"**#{r.get('id')}** {'✅ resolved' if r.get('resolved') else '🟥 open'} — <@{r.get('member_id')}> reported by <@{r.get('reporter_id')}>\nReason: {str(r.get('reason',''))[:180]}")
    embed=discord.Embed(title="🚨 Report Inbox", description="\n\n".join(lines)[:3900], color=0xe74c3c)
    embed.set_footer(text="Resolve with /server reports → Resolve → report_id")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@server_group.command(name="permissions", description="View channel permissions for a member or the permission overwrites (mods only)")
@app_commands.describe(channel="Which text channel? Leave blank for this channel", member="Optional: show this member's final permissions")
async def server_permissions(interaction: discord.Interaction, channel: discord.TextChannel = None, member: discord.Member = None):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ This only works inside a server text channel.", ephemeral=True); return
    if not _is_mod(interaction):
        await _deny(interaction); return
    ch=channel or interaction.channel
    if member:
        perms=ch.permissions_for(member)
        yes=[name.replace("_", " ").title() for name, val in perms if val]
        embed=discord.Embed(title=f"🔐 Permissions in #{ch.name}", description=f"**Member:** {member.mention}\n\n" + ", ".join(f"`{x}`" for x in yes[:35]), color=0x3498db)
        await interaction.response.send_message(embed=embed, ephemeral=True); return
    ow=ch.overwrites
    if not ow:
        await interaction.response.send_message(f"ℹ️ #{ch.name} has no special permission overwrites.", ephemeral=True); return
    lines=[]
    for target, perms in list(ow.items())[:20]:
        allow=[n.replace("_"," ") for n,v in perms if v is True]
        deny=[n.replace("_"," ") for n,v in perms if v is False]
        name=getattr(target, "mention", None) or getattr(target, "name", str(target))
        lines.append(f"{name}\n✅ {', '.join(allow[:8]) or '-'}\n❌ {', '.join(deny[:8]) or '-'}")
    embed=discord.Embed(title=f"🔐 Permission Overwrites in #{ch.name}", description="\n\n".join(lines)[:3900], color=0x3498db)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@server_group.command(name="scrims", description="Scrims manager: create scrims, reserve slots, view slot lists and close/delete scrims")
@app_commands.describe(action="What should I do?", name="Create: scrim name", time_text="Create: time, e.g. Today 7 PM", scrim_id="Scrim ID for reserve/slot list/close/delete", slots="Create: total slots (2-100)", slot="Reserve: slot number (0 = first free slot)")
@app_commands.choices(action=[
    app_commands.Choice(name="Create — make a scrim (mods only)", value="create"),
    app_commands.Choice(name="List — show all scrims", value="list"),
    app_commands.Choice(name="Reserve — book my slot", value="reserve"),
    app_commands.Choice(name="Unreserve — leave my slot", value="unreserve"),
    app_commands.Choice(name="My Slots — my booked slots", value="my_slots"),
    app_commands.Choice(name="Slot List — show the slot list", value="slotlist"),
    app_commands.Choice(name="Close — stop new bookings (mods only)", value="close"),
    app_commands.Choice(name="Open — allow bookings again (mods only)", value="open"),
    app_commands.Choice(name="Delete — remove a scrim (mods only)", value="delete"),
])
async def server_scrims(interaction: discord.Interaction, action: str = "list", name: str = "", time_text: str = "", scrim_id: int = 0, slots: app_commands.Range[int, 2, 100] = 25, slot: int = 0):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Scrims work only inside a server.", ephemeral=True); return
    bucket=_scrim_bucket(interaction.guild.id); items=bucket.setdefault("items", {})
    if action == "create":
        if not _is_mod(interaction):
            await _deny(interaction); return
        sid=int(bucket.get("next_id", 1)); bucket["next_id"]=sid+1
        items[str(sid)]={"id": sid, "name": (name or f"Scrim #{sid}")[:80], "time": (time_text or "Time not set")[:80], "slots": int(slots), "reservations": {}, "closed": False, "created_by": interaction.user.id, "created_at": datetime.datetime.now(datetime.timezone.utc).timestamp()}
        _save_scrims()
        embed=discord.Embed(title="🎮 Scrim Created", color=0x2ecc71)
        embed.add_field(name="Scrim ID", value=f"#{sid}", inline=True)
        embed.add_field(name="Name", value=items[str(sid)]["name"], inline=True)
        embed.add_field(name="Time", value=items[str(sid)]["time"], inline=True)
        embed.add_field(name="Slots", value=str(slots), inline=True)
        embed.set_footer(text=f"Book with /server scrims → Reserve → scrim_id {sid}")
        await interaction.response.send_message(embed=embed); return
    if action == "list":
        if not items:
            await interaction.response.send_message("ℹ️ No scrims yet. Mods can create one with `/server scrims`.", ephemeral=True); return
        lines=[]
        for it in list(items.values())[-10:]:
            lines.append(f"**#{it['id']} {it['name']}** — {it.get('time','')}\nSlots booked: **{len(it.get('reservations', {}))}/{it.get('slots', 0)}** • {'🔴 Closed' if it.get('closed') else '🟢 Open'}")
        embed=discord.Embed(title="🎮 Scrims", description="\n\n".join(lines)[:3900], color=0x3498db)
        await interaction.response.send_message(embed=embed, ephemeral=True); return
    if action in ("reserve", "unreserve", "my_slots", "slotlist", "close", "open", "delete"):
        if action == "my_slots":
            mine=[]
            for it in items.values():
                for sn, uid in it.get("reservations", {}).items():
                    if int(uid) == interaction.user.id: mine.append(f"Scrim **#{it['id']} {it['name']}** — Slot **#{sn}** ({it.get('time','')})")
            await interaction.response.send_message("\n".join(mine) if mine else "ℹ️ You have no scrim slots booked.", ephemeral=True); return
        it=_find_scrim(interaction.guild.id, scrim_id)
        if it is None:
            await interaction.response.send_message(f"❌ Scrim **#{scrim_id}** was not found. Use List first.", ephemeral=True); return
        if action in ("close", "open", "delete") and not _is_mod(interaction):
            await _deny(interaction); return
        if action == "delete":
            items.pop(str(it["id"]), None); _save_scrims()
            await interaction.response.send_message(f"🗑️ Scrim **#{it['id']} {it['name']}** deleted.", ephemeral=True); return
        if action in ("close", "open"):
            it["closed"]=(action == "close"); _save_scrims()
            await interaction.response.send_message(f"{'🔴 Closed' if action=='close' else '🟢 Opened'} scrim **#{it['id']} {it['name']}**.", ephemeral=True); return
        res=it.setdefault("reservations", {})
        if action == "unreserve":
            found=[sn for sn, uid in res.items() if int(uid) == interaction.user.id]
            if not found:
                await interaction.response.send_message("ℹ️ You do not have a slot in this scrim.", ephemeral=True); return
            for sn in found: res.pop(sn, None)
            _save_scrims()
            await interaction.response.send_message(f"✅ Your slot in scrim **#{it['id']}** was removed.", ephemeral=True); return
        if action == "slotlist":
            lines=[f"🎮 **Scrim #{it['id']} — {it['name']}** ({it.get('time','')})"]
            for n in range(1, int(it.get("slots", 0))+1):
                uid=res.get(str(n))
                lines.append(f"**Slot {n}:** " + (f"<@{uid}>" if uid else "— empty —"))
            await interaction.response.send_message("\n".join(lines)[:1900]); return
        # reserve
        if it.get("closed"):
            await interaction.response.send_message("🔴 This scrim is closed for new bookings.", ephemeral=True); return
        for sn, uid in res.items():
            if int(uid) == interaction.user.id:
                await interaction.response.send_message(f"ℹ️ You already have **Slot #{sn}** in this scrim.", ephemeral=True); return
        chosen=0
        if slot and 1 <= int(slot) <= int(it.get("slots", 0)):
            if str(int(slot)) in res:
                await interaction.response.send_message(f"❌ Slot **#{slot}** is already booked.", ephemeral=True); return
            chosen=int(slot)
        else:
            for n in range(1, int(it.get("slots", 0))+1):
                if str(n) not in res: chosen=n; break
        if not chosen:
            await interaction.response.send_message("❌ All slots are full.", ephemeral=True); return
        res[str(chosen)]=interaction.user.id; _save_scrims()
        await interaction.response.send_message(f"✅ Slot booked! Scrim **#{it['id']} {it['name']}** — your slot is **#{chosen}**. Use Slot List to see everyone.", ephemeral=True); return
    await interaction.response.send_message("❌ Invalid action.", ephemeral=True)

# ---------- /games (11) ----------
_game_state: dict = {}
_TRIVIA=[
    ("What is the capital of India?", ["Mumbai", "Delhi", "Kolkata", "Chennai"], "b"),
    ("Which is the largest planet?", ["Earth", "Jupiter", "Mars", "Venus"], "b"),
    ("What is the chemical formula of water?", ["CO2", "O2", "H2O", "NaCl"], "c"),
    ("Where is the Taj Mahal?", ["Delhi", "Jaipur", "Agra", "Lucknow"], "c"),
    ("What is called the brain of a computer?", ["RAM", "CPU", "Monitor", "Mouse"], "b"),
    ("How many seconds are there in 1 hour?", ["3600", "600", "360", "6000"], "a"),
    ("How many colors are there in a rainbow?", ["5", "6", "7", "8"], "c"),
    ("Which sport is popularly considered India's national game?", ["Cricket", "Hockey", "Football", "Kabaddi"], "b"),
]
_RIDDLES=[
    ("I move without legs and cry without eyes. What am I?", ["cloud"]),
    ("The more you take from me, the bigger I get. What am I?", ["hole"]),
    ("I have cities but no houses, forests but no trees. What am I?", ["map"]),
]
_SCRAMBLE_WORDS=["nezuko", "discord", "gaming", "python", "dost", "music", "server", "cricket", "mobile", "duniya"]

@games_group.command(name="trivia", description="Get a question — answer with /games trivia_answer")
async def games_trivia(interaction: discord.Interaction):
    q, opts, sahi=_TRIVIA[random.randrange(len(_TRIVIA))]
    _game_state[interaction.user.id]={"type": "trivia", "answer": sahi}
    lines="\n".join(f"**{chr(97+i)}.** {o}" for i, o in enumerate(opts))
    await interaction.response.send_message(f"🧠 **Trivia:** {q}\n{lines}\n\nAnswer with `/games trivia_answer`!")

@games_group.command(name="trivia_answer", description="Answer the trivia question")
@app_commands.describe(choice="a, b, c or d")
@app_commands.choices(choice=[app_commands.Choice(name=x, value=x) for x in ("a", "b", "c", "d")])
async def games_trivia_answer(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "trivia":
        await interaction.response.send_message("ℹ️ Get a question first with `/games trivia`.", ephemeral=True); return
    _game_state.pop(interaction.user.id, None)
    if choice.value == rec["answer"]:
        await interaction.response.send_message(f"🎉 **Correct answer, {interaction.user.mention}!** You win!")
    else:
        await interaction.response.send_message(f"❌ Wrong! The correct answer was **{rec['answer']}**. Try again!")

@games_group.command(name="riddle", description="Get a riddle — answer with /games riddle_answer")
async def games_riddle(interaction: discord.Interaction):
    q, kabul=_RIDDLES[random.randrange(len(_RIDDLES))]
    _game_state[interaction.user.id]={"type": "riddle", "answers": kabul}
    await interaction.response.send_message(f"🕵️ **Riddle:** {q}\n\nAnswer with `/games riddle_answer`!")

@games_group.command(name="riddle_answer", description="Answer the riddle")
@app_commands.describe(answer="Your answer")
async def games_riddle_answer(interaction: discord.Interaction, answer: str):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "riddle":
        await interaction.response.send_message("ℹ️ Start with `/games riddle` first.", ephemeral=True); return
    _game_state.pop(interaction.user.id, None)
    if answer.strip().lower() in rec["answers"]:
        await interaction.response.send_message(f"🎉 **You got it, {interaction.user.mention}!**")
    else:
        await interaction.response.send_message(f"❌ No! The answer was: **{rec['answers'][0]}**")

@games_group.command(name="scramble", description="Get a scrambled word — unscramble it with /games scramble_guess")
async def games_scramble(interaction: discord.Interaction):
    shabd=_SCRAMBLE_WORDS[random.randrange(len(_SCRAMBLE_WORDS))]
    ulta=list(shabd); random.shuffle(ulta); ulta="".join(ulta)
    if ulta == shabd: ulta=shabd[::-1]
    _game_state[interaction.user.id]={"type": "scramble", "answer": shabd}
    await interaction.response.send_message(f"🔤 **Scramble:** `{ulta}`\nUse `/games scramble_guess` to answer!")

@games_group.command(name="scramble_guess", description="Tell the correct unscrambled word")
@app_commands.describe(word="Correct word")
async def games_scramble_guess(interaction: discord.Interaction, word: str):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "scramble":
        await interaction.response.send_message("ℹ️ Start with `/games scramble` first.", ephemeral=True); return
    if word.strip().lower() == rec["answer"]:
        _game_state.pop(interaction.user.id, None)
        await interaction.response.send_message(f"🎉 **Correct!** The word was: **{rec['answer']}**")
    else:
        await interaction.response.send_message("❌ Wrong — try again! (the game is still running)")

@games_group.command(name="mathquiz", description="Get a math question — answer with /games math_answer")
async def games_mathquiz(interaction: discord.Interaction):
    a, b=random.randint(2, 50), random.randint(2, 30)
    op=random.choice(["+", "-", "*"])
    sahi={"+" : a+b, "-": a-b, "*": a*b}[op]
    _game_state[interaction.user.id]={"type": "math", "answer": sahi}
    await interaction.response.send_message(f"➗ **Math Quiz:** {a} {op} {b} = ?\nAnswer with `/games math_answer`!")

@games_group.command(name="math_answer", description="Answer the math quiz (number)")
@app_commands.describe(answer="Your number answer")
async def games_math_answer(interaction: discord.Interaction, answer: int):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "math":
        await interaction.response.send_message("ℹ️ Start with `/games mathquiz` first.", ephemeral=True); return
    _game_state.pop(interaction.user.id, None)
    if answer == rec["answer"]:
        await interaction.response.send_message(f"🎉 **Absolutely correct, {interaction.user.mention}!**")
    else:
        await interaction.response.send_message(f"❌ Wrong! The correct answer was **{rec['answer']}**.")

@games_group.command(name="numberguess", description="I picked a number between 1-100 — find it with /games guess")
async def games_numberguess(interaction: discord.Interaction):
    _game_state[interaction.user.id]={"type": "number", "answer": random.randint(1, 100), "tries": 0}
    await interaction.response.send_message(f"🎯 {interaction.user.mention} I picked a number between **1-100**! Find it with `/games guess`!")

@games_group.command(name="guess", description="Guess the number")
@app_commands.describe(number="Your guess (1-100)")
async def games_guess(interaction: discord.Interaction, number: app_commands.Range[int, 1, 100]):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "number":
        await interaction.response.send_message("ℹ️ Start with `/games numberguess` first.", ephemeral=True); return
    rec["tries"]+=1
    if number == rec["answer"]:
        tries=rec["tries"]; _game_state.pop(interaction.user.id, None)
        await interaction.response.send_message(f"🎉 **Found it!** The number was **{number}** — you found it in {tries} tries!")
    elif number < rec["answer"]:
        await interaction.response.send_message("⬆️ It is **higher** than that! Guess again.")
    else:
        await interaction.response.send_message("⬇️ It is **lower** than that! Guess again.")

@games_group.command(name="idp", description="Share a game room ID and password in a clean embed")
@app_commands.describe(room_id="Room ID", password="Room password (optional)", game="Game name")
@app_commands.choices(game=[
    app_commands.Choice(name="BGMI / PUBG Mobile", value="BGMI / PUBG Mobile"),
    app_commands.Choice(name="Free Fire", value="Free Fire"),
    app_commands.Choice(name="Valorant", value="Valorant"),
    app_commands.Choice(name="Custom Game", value="Custom Game"),
])
async def games_idp(interaction: discord.Interaction, room_id: str, password: str = "", game: str = "BGMI / PUBG Mobile"):
    embed=discord.Embed(title=f"🎮 {game} Room Details", color=0x2ecc71)
    embed.add_field(name="Room ID", value=f"`{str(room_id)[:80]}`", inline=True)
    if password: embed.add_field(name="Password", value=f"||{str(password)[:80]}||", inline=True)
    embed.add_field(name="Shared by", value=interaction.user.mention, inline=True)
    embed.set_footer(text="Join quickly — slots fill fast. Do not share room passwords outside this server.")
    await interaction.response.send_message(embed=embed)

@games_group.command(name="slots", description="Economy coins se slot machine (triple = 5x munafa)")
@app_commands.describe(amount="How many coins do you want to bet? (minimum 10)")
async def games_slots(interaction: discord.Interaction, amount: app_commands.Range[int, 10, 100000] = 10):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Slots only works inside a server.", ephemeral=True); return
    rec=_eco_rec(interaction.guild.id, interaction.user.id)
    bal=int(rec.get("coins", 0))
    if bal < amount:
        await interaction.response.send_message(f"❌ You do not have enough coins (balance 🪙 {bal:,}). Earn some first with `/daily`!", ephemeral=True); return
    faces=["🍒", "🍋", "🍇", "⭐", "💎"]
    spun=[random.choice(faces) for _ in range(3)]
    if spun[0] == spun[1] == spun[2]:
        rec["coins"]=bal + amount*5; result=f"🎰💰 **TRIPLE! You won +🪙 {amount*5:,}!**"
    elif spun[0] == spun[1] or spun[1] == spun[2] or spun[0] == spun[2]:
        rec["coins"]=bal + amount; result=f"🎰 **A pair matched! You won +🪙 {amount:,}!**"
    else:
        rec["coins"]=bal - amount; result=f"🎰💔 Nothing matched — you lost 🪙 {amount:,}."
    _save_economy()
    await interaction.response.send_message(f"🎰 | {' | '.join(spun)} |\n{result}\n💰 Balance: **🪙 {int(rec['coins']):,}**")


# ---------- Discord Battle Royale (browser-game ka Discord version) ----------
_ROYALE_WEAPONS=[
    ("Fists", 5, 11, 0.85),
    ("Pistol", 10, 18, 0.84),
    ("SMG", 13, 22, 0.88),
    ("Rifle", 17, 28, 0.86),
    ("Shotgun", 22, 36, 0.76),
    ("Sniper", 36, 58, 0.70),
]
_ROYALE_BOT_NAMES=[
    "ShadowFox", "BlazeQueen", "NoScopeNinja", "LootGoblin", "SilentStorm", "PixelReaper",
    "TurboTiger", "GhostByte", "IronWolf", "VenomVivi", "DarkMeteor", "CaptainClutch",
    "RapidRaven", "EchoStrike", "FrostByte", "ThunderAstra", "NovaKnight", "RogueRani",
    "CyberKabir", "MysticMira", "ViperVeer", "OmegaRex", "SolarFlare", "NightHawk",
    "QuantumQueen", "BlitzBheem", "CrimsonKai", "PhantomIra", "SteelSniper", "WildWizard",
    "AeroAce", "LunaLaser", "MechaRudra", "ToxicTara", "InfernoIshaan", "ZeroGravity",
]

def _royale_new_state(user_id: int) -> dict:
    names=random.sample(_ROYALE_BOT_NAMES, 29)
    return {
        "user_id": int(user_id), "hp": 100, "weapon": 1, "medkits": 1,
        "round": 1, "kills": 0, "bots": [{"name": n, "hp": 100} for n in names],
        "log": ["🪂 You landed on the island with 29 fighters. Loot fast — the zone will shrink!"],
        "over": False, "won": False,
    }

def _royale_hp_bar(hp: int) -> str:
    filled=max(0, min(10, int(round(max(0, hp)/10))))
    return "🟩"*filled + "⬛"*(10-filled)

def _royale_embed(state: dict) -> discord.Embed:
    wname, wmin, wmax, _hit = _ROYALE_WEAPONS[state["weapon"]]
    alive=len(state["bots"]) + (0 if state["over"] and not state["won"] and state["hp"] <= 0 else 1)
    if state["over"] and state["won"]:
        title="🏆 #1 VICTORY ROYALE!"; color=0xf1c40f
    elif state["over"]:
        title="💀 ELIMINATED"; color=0xe74c3c
    else:
        title="🎮 ARISE ROYALE — Discord Battle Royale"; color=0x9b59b6
    embed=discord.Embed(title=title, color=color)
    embed.description="\n".join(f"• {x}" for x in state["log"][-6:])[:3900]
    embed.add_field(name="❤️ HP", value=f"{_royale_hp_bar(state['hp'])}\n**{state['hp']}/100**", inline=True)
    embed.add_field(name="👥 Alive", value=f"**{alive}/30**", inline=True)
    embed.add_field(name="☠️ Kills", value=f"**{state['kills']}**", inline=True)
    embed.add_field(name="🔫 Weapon", value=f"**{wname}** ({wmin}-{wmax})", inline=True)
    embed.add_field(name="💊 Medkits", value=f"**{state['medkits']}**", inline=True)
    embed.add_field(name="⭕ Round / Zone", value=f"**{state['round']}**", inline=True)
    if state["over"]:
        place=1 if state["won"] else len(state["bots"])+1
        embed.add_field(name="🏁 Result", value=f"You finished **#{place}** with **{state['kills']} kills**.", inline=False)
    embed.set_footer(text="Only the player who started this match can use these buttons.")
    return embed

def _royale_log(state: dict, text: str):
    state["log"].append(text)
    state["log"]=state["log"][-14:]

def _royale_damage_player(state: dict, low: int, high: int, source: str):
    if state["over"]: return
    dmg=random.randint(low, high); state["hp"]=max(0, state["hp"]-dmg)
    _royale_log(state, f"{source} hit you for **{dmg} HP**.")
    if state["hp"] <= 0:
        state["over"]=True; state["won"]=False
        _royale_log(state, "💀 You were eliminated from the island.")

def _royale_find_loot(state: dict, better_chance: bool = False):
    roll=random.random()
    weapon_chance=0.48 if better_chance else 0.34
    if roll < weapon_chance:
        if state["weapon"] < len(_ROYALE_WEAPONS)-1:
            state["weapon"] += 1
            _royale_log(state, f"🎒 You found a better weapon: **{_ROYALE_WEAPONS[state['weapon']][0]}**!")
        else:
            state["medkits"] += 1
            _royale_log(state, "💊 You already have the best weapon, so you picked up a **Medkit** instead.")
    elif roll < weapon_chance + 0.32:
        state["medkits"] += 1
        _royale_log(state, "💊 You found a **Medkit**.")
    elif roll < weapon_chance + 0.47:
        heal=random.randint(8, 18); before=state["hp"]; state["hp"]=min(100, state["hp"]+heal)
        _royale_log(state, f"🩹 You used a bandage and recovered **{state['hp']-before} HP**.")
    else:
        _royale_log(state, "📦 This place was empty — only dust and empty crates.")

def _royale_bot_battle(state: dict):
    bots=state["bots"]
    if len(bots) <= 1 or state["over"]: return
    fights=random.randint(1, 3) if len(bots) > 14 else random.randint(0, 2)
    for _ in range(fights):
        if len(bots) <= 1: break
        attacker, victim=random.sample(bots, 2)
        victim["hp"] -= random.randint(28, 62)
        if victim["hp"] <= 0:
            bots.remove(victim)
            _royale_log(state, f"☠️ **{attacker['name']}** eliminated **{victim['name']}** somewhere on the island.")

def _royale_zone_and_ambush(state: dict, moved: bool):
    if state["over"]: return
    if state["round"] % 3 == 0:
        stage=max(1, state["round"]//3)
        zone_dmg=5 + stage*4
        fallen=[]
        for enemy in list(state["bots"]):
            enemy["hp"] -= zone_dmg
            if enemy["hp"] <= 0: fallen.append(enemy)
        for enemy in fallen: state["bots"].remove(enemy)
        if fallen:
            _royale_log(state, f"⭕ The zone swallowed **{len(fallen)} fighter(s)**.")
        if moved:
            _royale_log(state, "🏃 You reached the safe zone before it closed.")
        else:
            _royale_damage_player(state, zone_dmg, zone_dmg+4, "⭕ The zone")
            if state["over"]: return
    ambush_chance=0.10 if moved else 0.27
    if state["bots"] and random.random() < ambush_chance:
        enemy=random.choice(state["bots"])
        _royale_damage_player(state, 7, 16, f"🔫 **{enemy['name']}** ambushed you and")

def _royale_take_turn(state: dict, action: str):
    if state["over"]: return state
    moved=False
    if action == "loot":
        _royale_find_loot(state, better_chance=True)
    elif action == "move":
        moved=True
        _royale_log(state, "🏃 You rotated toward the safe zone.")
        if random.random() < 0.42: _royale_find_loot(state)
    elif action == "heal":
        if state["medkits"] <= 0:
            _royale_log(state, "💊 You have no medkits left! Find one with **Loot** or **Move**.")
        elif state["hp"] >= 100:
            _royale_log(state, "❤️ Your HP is already full — medkit saved.")
        else:
            state["medkits"] -= 1; heal=random.randint(30, 45)
            before=state["hp"]; state["hp"]=min(100, state["hp"]+heal)
            _royale_log(state, f"💊 Medkit used: **+{state['hp']-before} HP**.")
    elif action == "attack":
        if not state["bots"]:
            pass
        else:
            target=random.choice(state["bots"])
            wname, wmin, wmax, hit = _ROYALE_WEAPONS[state["weapon"]]
            if random.random() <= hit:
                dmg=random.randint(wmin, wmax); target["hp"] -= dmg
                if target["hp"] <= 0:
                    state["bots"].remove(target); state["kills"] += 1
                    _royale_log(state, f"🎯 You eliminated **{target['name']}** with your **{wname}**! (**{dmg} damage**)")
                else:
                    _royale_log(state, f"🔫 You hit **{target['name']}** for **{dmg} damage** with your **{wname}**.")
                    if random.random() < 0.48:
                        _royale_damage_player(state, 6, 15, f"🔥 **{target['name']}** fired back and")
                        if state["over"]: return state
            else:
                _royale_log(state, f"💨 Your shot missed **{target['name']}**.")
                if random.random() < 0.35:
                    _royale_damage_player(state, 5, 12, f"🔥 **{target['name']}** fired back and")
                    if state["over"]: return state
    if not state["bots"] and state["hp"] > 0:
        state["over"]=True; state["won"]=True
        _royale_log(state, "🏆 You are the last fighter standing!")
        return state
    _royale_bot_battle(state)
    if not state["bots"] and state["hp"] > 0:
        state["over"]=True; state["won"]=True
        _royale_log(state, "🏆 You are the last fighter standing!")
        return state
    _royale_zone_and_ambush(state, moved)
    if not state["bots"] and state["hp"] > 0:
        state["over"]=True; state["won"]=True
        _royale_log(state, "🏆 You are the last fighter standing!")
    if not state["over"]: state["round"] += 1
    return state

class RoyaleGameView(discord.ui.View):
    def __init__(self, state: dict):
        super().__init__(timeout=900)
        self.state=state
        self.message=None
        self._sync_buttons()

    def _sync_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled=bool(self.state["over"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state["user_id"]:
            await interaction.response.send_message("❌ This is not your Battle Royale match. Start your own with `/games royale`.", ephemeral=True)
            return False
        return True

    async def _play(self, interaction: discord.Interaction, action: str):
        _royale_take_turn(self.state, action)
        self._sync_buttons()
        await interaction.response.edit_message(embed=_royale_embed(self.state), view=self)
        if self.state["over"]: self.stop()

    @discord.ui.button(label="Loot", style=discord.ButtonStyle.primary, emoji="🎒")
    async def btn_loot(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "loot")

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.danger, emoji="🔫")
    async def btn_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "attack")

    @discord.ui.button(label="Heal", style=discord.ButtonStyle.success, emoji="💊")
    async def btn_heal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "heal")

    @discord.ui.button(label="Move to Zone", style=discord.ButtonStyle.secondary, emoji="🏃")
    async def btn_move(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "move")

    async def on_timeout(self):
        if not self.state["over"]:
            self.state["over"]=True; self.state["won"]=False
            _royale_log(self.state, "⌛ This match timed out.")
        self._sync_buttons()
        if self.message is not None:
            try: await self.message.edit(embed=_royale_embed(self.state), view=self)
            except Exception: pass

@games_group.command(name="royale", description="Play a solo Battle Royale against 29 bots with buttons")
async def games_royale(interaction: discord.Interaction):
    state=_royale_new_state(interaction.user.id)
    view=RoyaleGameView(state)
    await interaction.response.send_message(embed=_royale_embed(state), view=view)
    try: view.message=await interaction.original_response()
    except Exception: pass


for _grp in (tools_group, text_group, actions_group, server_group, games_group):
    bot.tree.add_command(_grp)


# ---------------- MUSIC SYSTEM ----------------
try:
    import yt_dlp as _yt_dlp
except Exception:
    _yt_dlp=None
_LIBOPUS_OK=[False]
def _ensure_opus():
    """Discord voice-speak ke liye opus encoder load karo (Termux fix).
    opus install na ho to bot sirf sunn sakti hai, bol nahi paati."""
    try:
        if not discord.opus.is_loaded():
            _lib=_ctypes_util.find_library("opus") or "libopus.so.0"
            discord.opus.load_opus(_lib)
        _LIBOPUS_OK[0]=True
    except Exception:
        log.debug("opus load fail (voice bol nahi seetegi)")
        _LIBOPUS_OK[0]=False
    return _LIBOPUS_OK[0]
try:
    _ensure_opus()
except Exception:
    pass
def _ensure_ytdlp():
    """yt-dlp baad me install ho to bot bina restart ke bhi utha lega."""
    global _yt_dlp
    if _yt_dlp is None:
        try:
            import yt_dlp as _m
            _yt_dlp=_m
            log.info("yt-dlp load ho gaya (version %s)", getattr(_m, "version", "?"))
        except Exception:
            log.warning("yt-dlp import fail\n%s", traceback.format_exc())
    return _yt_dlp
_YTDL_OPTS={"format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True, "default_search": "ytsearch1:",
            "extractor_args": {"youtube": {"player_client": ["android", "android_vr", "web"]}}}
_FFMPEG_OPTS={"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "options": "-vn"}
class FFmpegYTDLSource(discord.AudioSource):
    """Audio source: yt-dlp pehle song ko file me download kare, fir ffmpeg usko chale.
    Is tareeke se YouTube signed-URL wali 403 dikkat nahi aati aur speed pakki rehti hai."""
    def __init__(self, filename: str):
        self._ff=discord.FFmpegPCMAudio(filename, options="-vn")
        self._filename=filename; self._cleaned=False
    def read(self) -> bytes:
        return self._ff.read()
    def is_opus(self) -> bool:
        return False
    def cleanup(self):
        try: self._ff.cleanup()
        except Exception: pass
        if not self._cleaned:
            self._cleaned=True
            try:
                import os as _o
                _o.remove(self._filename)
            except Exception: pass
def _prefetch_track(track: dict):
    """Gaana bajne se pahle background me download kar ke ready rakho — queue me gap nahi aayega."""
    try:
        return _yt_download(track)
    except Exception:
        return None
def _yt_download(track: dict) -> str:
    """Song ko local file me download karo (403-proof). Caller FFmpegYTDLSource se chalata hai."""
    import tempfile, os as _os
    src=track.get("webpage") or track["url"]
    out_tmpl=_os.path.join(tempfile.gettempdir(), f"Nezuko_song_{abs(hash(src)) % 99999999}.%(ext)s")
    opts={"format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True, "outtmpl": out_tmpl}
    with _yt_dlp.YoutubeDL(opts) as ydl:
        dl_info=ydl.extract_info(src, download=True)
        if dl_info is None: raise RuntimeError("download fail hua")
        out_file=ydl.prepare_filename(dl_info)
        for cand in (out_file, out_file.rsplit(".",1)[0]+".webm", out_file.rsplit(".",1)[0]+".m4a", out_file.rsplit(".",1)[0]+".opus"):
            if _os.path.exists(cand): return cand
    raise RuntimeError("download fail hua")
_music_state: dict[int, dict] = {}
def _music_of(guild_id: int) -> dict:
    st=_music_state.setdefault(guild_id, {"queue": [], "current": None, "text_channel": None, "autoplay": True, "manual_stop": False, "played_vids": [], "fail_count": 0})
    st.setdefault("autoplay", True); st.setdefault("manual_stop", False)
    st.setdefault("played_vids", []); st.setdefault("fail_count", 0)
    return st

async def _prefetch_track_async(track: dict):
    """Background me gaana pehle-se download rakho — sahi async tareeke se
    (purana code create_task(Future) se yahi crash ho jata tha = autoplay band)."""
    if track.pop("_prefetch_started", None): return
    track["_prefetch_started"]=True
    try:
        track["_prefetched"]=await asyncio.get_event_loop().run_in_executor(None, _prefetch_track, track)
    except Exception:
        track["_prefetched"]=None
def _yt_extract(query: str) -> dict:
    with _yt_dlp.YoutubeDL(_YTDL_OPTS) as ydl:
        info=ydl.extract_info(query, download=False)
        if "entries" in info:
            entries=[e for e in info["entries"] if e]
            if not entries: raise RuntimeError("no result")
            info=entries[0]
        return {"title": info.get("title", "Unknown song"), "url": info.get("url"), "webpage": info.get("webpage_url", ""), "duration": info.get("duration") or 0, "vid": info.get("id", "")}
def _yt_similar(track: dict, seen_vids=None) -> dict | None:
    """Uske gaane jaisa (similar) gaana dhundo autoplay ke liye.
    Titles YouTube search me cleaned rakhe to sahi results aate hai."""
    raw=(track.get("title") or "").strip()
    if not raw: return None
    for ch_to_cut in ("|", "–", "—", "(", "[", " ft", " Ft", "Feat", "lyrics", "Lyrics", "full video", "official video", "Official Video", "song", "Song", "S O N G", "video"):
        raw=raw.split(ch_to_cut)[0].strip()
    base=raw.strip(" -–—|")
    if len(base) < 3: base=(track.get("title") or "")[:40]
    seen=set(seen_vids or [])
    if track.get("vid"): seen.add(track.get("vid"))
    opts=dict(_YTDL_OPTS); opts["default_search"]="ytsearch8:"
    with _yt_dlp.YoutubeDL(opts) as ydl:
        info=ydl.extract_info(base + " similar mix", download=False)
        for e in (info.get("entries") or []):
            if not e: continue
            vid=e.get("id"); wp=e.get("webpage_url") or (("https://www.youtube.com/watch?v="+vid) if vid else None)
            dur=e.get("duration") or 0
            if vid and vid not in seen and wp and (not dur or dur <= 900):
                return {"title": e.get("title", "Unknown song"), "url": wp, "webpage": wp, "duration": dur, "vid": vid, "requester": "Autoplay 🔀"}
    return None

async def _play_next(guild: discord.Guild):
    st=_music_of(guild.id)
    vc=guild.voice_client
    if vc is None: return
    if st.get("manual_stop"):
        st["current"]=None
        return
    if vc.is_playing() or vc.is_paused():
        return  # kuch baj raha hai — do jagah se ek saath play mat karo
    if not st["queue"]:
        last=st.get("last_played") or st.get("current")
        if st.get("autoplay", True) and last and _ensure_ytdlp() is not None:
            try:
                sim=await asyncio.get_event_loop().run_in_executor(None, lambda: _yt_similar(last, st.get("played_vids")))
            except Exception:
                sim=None
            if sim:
                st["queue"].append(sim)
                # Autoplay wala gaana bhi pehle se download hona chalu — Khatam hone par gap nahin aayega
                asyncio.create_task(_prefetch_track_async(sim))
                ch=st.get("text_channel")
                if ch is not None:
                    try: await ch.send(f"🔀 **Autoplay:** The song ended — bringing a similar song... 🎵")
                    except Exception: pass
            else:
                st["current"]=None
                return
        else:
            st["current"]=None
            return
    track=st["queue"].pop(0)
    st["current"]=track
    st["last_played"]=track
    if track.get("vid"):
        st["played_vids"]=(st["played_vids"] + [track["vid"]])[-20:]
    # Direct stream URL ki jagah download karo — signed-URL 403 wali dikkat nahi aati.
    try:
        pre=track.pop("_prefetched", None)
        downloaded=pre or await asyncio.get_event_loop().run_in_executor(None, _yt_download, track)
        source=FFmpegYTDLSource(downloaded)
    except Exception as e:
        log.error("music ffmpeg fail\n%s", traceback.format_exc())
        st["fail_count"]=int(st.get("fail_count", 0)) + 1
        ch=st.get("text_channel")
        if ch is not None:
            try: await ch.send(f"⚠️ The song could not play — reason: `{str(e)[:120]}`")
            except Exception: pass
        if st.get("queue"):
            asyncio.create_task(_play_next(guild))
        elif st.get("autoplay", True) and not st.get("manual_stop") and st["fail_count"] < 3:
            # queue khali par autoplay se agla similar try karo — ek fail par gana band nahi hoga
            asyncio.create_task(_play_next(guild))
        else:
            st["current"]=None
            if st["fail_count"] >= 3 and ch is not None:
                try: await ch.send("⏹️ 3 songs failed in a row — autoplay stopped. Play a new song with `/p`.")
                except Exception: pass
        return
    st["fail_count"]=0
    def _after(error):
        if error: log.warning("music playback error: %s", error)
        asyncio.run_coroutine_threadsafe(_play_next(guild), bot.loop)
    # Agla gaana pehle se background me download karna shuru Karo (gap-free playback)
    if st.get("queue"):
        _nxt=st["queue"][0]
        try:
            asyncio.create_task(_prefetch_track_async(_nxt))
        except Exception:
            pass
    try:
        vc.play(source, after=_after)
    except Exception:
        log.error("music play fail\n%s", traceback.format_exc())
        st["current"]=None
        return
    ch=st.get("text_channel")
    if ch is not None:
        try:
            await ch.send(f"🎵 **Now Playing:** {track['title']}\n_(Requested by {track['requester']})_\n🔀 Autoplay: **{'ON' if st.get('autoplay', True) else 'OFF'}** _(Use the panel below to control it 👇)_", view=MusicPanelView())
        except Exception: pass

# ------------- MIC LISTEN: Nezuko naam sunke VC me bolkar jawab -------------
_VOICE_LISTEN: dict[int, bool] = {}
_VOICE_LISTEN_CH: dict[int, int] = {}
_VC_VOICE_CH: dict[int, int] = {}
_VC_REJOIN: dict[int, object] = {}
_VC_REJOIN_TRIES: dict[int, list] = {}
_vc_tasks: dict[int, asyncio.Task] = {}
_VC_COOLDOWN: dict[int, float] = {}
_VC_STYLE: dict[int, str] = {}
_VC_SPEAKING: dict[int, bool] = {}
_VC_PROCESSING: set = set()

def _stereo_to_mono(pcm: bytes):
    """Discord opus decoder STEREO (2-channel, 48kHz) PCM deta hai.
    Dono channel ko mila ke saaf mono banao — yahi asli awaaz hai."""
    import struct as _st
    if len(pcm) % 2: pcm=pcm[:-1]
    vals=_st.unpack("<%dh" % (len(pcm)//2), pcm)
    if len(vals) % 2: vals=vals[:-1]
    mono=[]
    for i in range(0, len(vals), 2):
        mono.append((vals[i] + vals[i+1]) // 2)
    # Awaaz saaf Karo: DC offset hatao, halki awaaz ko uthao aur phati hui tez awaaz ko neeche lao.
    # Isse Google/Gemini STT ko har type ki awaaz samajhne me madad milti hai.
    try:
        if mono:
            _mean=sum(mono)/len(mono)
            mono=[int(v-_mean) for v in mono]
            _rms=(sum(v*v for v in mono)/len(mono)) ** 0.5
            if _rms > 20:
                _gain=max(0.45, min(3.5, 2700.0/_rms))
                _gate=max(45, int(_rms*0.10))
                mono=[0 if abs(v) < _gate else max(-32768, min(32767, int(v*_gain))) for v in mono]
    except Exception: pass
    return mono

def _pcm_to_wav_48k(pcm: bytes, channels: int = 2) -> bytes:
    """Stereo awaaz ko mono 48k WAV banao (bina kata, bina slow kiye) — Gemini STT ke liye."""
    import io, wave, struct as _st
    try:
        mono=_stereo_to_mono(pcm)
        raw=_st.pack("<%dh" % len(mono), *mono)
    except Exception:
        raw=pcm
    bio=io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(raw)
    return bio.getvalue()

def _pcm_to_wav(pcm: bytes) -> bytes:
    """Stereo 48k awaaz -> mono 16kHz WAV (Google STT ke liye sahi speed/pitch)."""
    import io, wave, struct as _st
    try:
        mono=_stereo_to_mono(pcm)
        # 48k -> 16k: har 3 sample ka average (saaf downsample, speed/pitch sahi)
        out=[]
        for i in range(0, len(mono)-2, 3):
            out.append((mono[i] + mono[i+1] + mono[i+2]) // 3)
        raw=_st.pack("<%dh" % len(out), *out)
    except Exception:
        raw=pcm
    bio=io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(raw)
    return bio.getvalue()

async def _stt_gemini(wav_bytes: bytes) -> str:
    """User ki boli hui baat ko text me badlo — wake word check ke liye.
    Pehle Gemini ke awaaz samajhne wale pakke models, chal jaye to wahi.
    Saath me asal error body bhi log karo taaki pata chale manaa kyun kiya."""
    if not GEMINI_API_KEY: return ""
    import base64 as _b64
    prompt=("Listen to this audio and transcribe EXACTLY what the person said "
            "(Hindi/Hinglish/English jaise bola hai waise hi). Return ONLY the transcript text.")
    payload={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "audio/wav", "data": _b64.b64encode(wav_bytes).decode()}}]}]}
    for model in [GEMINI_MODEL, "gemini-flash-lite-latest", "gemini-3-flash-preview", "gemini-3.8-flash"]:
        if not model: continue
        try:
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            async with _sess() as session:
                async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    txt=await resp.text()
                    if resp.status == 200:
                        try:
                            data=json.loads(txt)
                            t=(data["candidates"][0]["content"]["parts"][0]["text"] or "").strip()
                            if t: return t
                        except Exception: pass
                    else:
                        log.warning("STT HTTP %s model %s body: %s", resp.status, model, txt[:300])
                        if resp.status in (400, 401, 403) and ("API key" in txt or "API_KEY" in txt):
                            break
        except Exception:
            log.warning("STT fail %s\n%s", model, traceback.format_exc())
    # Last try: Google ki free awaaz-pehchan (bina key) — file 16k mono bana ke
    try:
        return await asyncio.get_event_loop().run_in_executor(None, _stt_google_free, wav_bytes)
    except Exception:
        log.warning("STT google free fail\n%s", traceback.format_exc())
    return ""


async def _stt_gemini_once(wav_bytes: bytes) -> str:
    """Gemini se awaaz ko ek tez try me samjho (race ke liye).
    Chalne wale models ek-ek try karo — purana dead model jawab rok raha tha."""
    if not GEMINI_API_KEY: return ""
    import base64 as _b64
    prompt=("Listen to this audio and transcribe EXACTLY what the person said, in whatever language they speak "
            "(Hindi, English, Bengali, Tamil, Japanese, Spanish — ANY language), in that same language and script. "
            "Return ONLY the transcript text.")
    payload={"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "audio/wav", "data": _b64.b64encode(wav_bytes).decode()}}]}]}
    for model in (GEMINI_MODEL, "gemini-flash-lite-latest", "gemini-3-flash-preview"):
        if not model: continue
        try:
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            async with _sess() as session:
                async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=18)) as resp:
                    txt=await resp.text()
                    if resp.status != 200:
                        log.warning("STT Gemini HTTP %s model %s body: %s", resp.status, model, txt[:200])
                        if resp.status in (401, 403): return ""  # key kharab — baaki model bhi fail honge
                        continue
                    data=json.loads(txt)
                    t=(data["candidates"][0]["content"]["parts"][0]["text"] or "").strip()
                    if t: return t
        except Exception:
            log.warning("STT Gemini fail %s\n%s", model, traceback.format_exc())
            continue
    return ""

async def _stt_first_try(wav_bytes: bytes, wav48_bytes: bytes = b"") -> str:
    """Voice ko samajhne ka sahi tareeka: Google-fast aur Gemini-accurate dono chalo,
    lekin pehle mile hue galat transcript par rukna nahi. Gemini mile to usi ko lo,
    warna Google wala. Gemini ko 48k saaf WAV milta hai, Google ko 16k."""
    async def _google():
        try:
            return await asyncio.get_event_loop().run_in_executor(None, lambda: _stt_google_free(wav_bytes, quick=True))
        except Exception:
            log.warning("STT google fail\n%s", traceback.format_exc())
            return ""
    gemini_audio=wav48_bytes or wav_bytes
    tasks={asyncio.create_task(_google()): "google-free", asyncio.create_task(_stt_gemini_once(gemini_audio)): "gemini"}
    results={}; pending=set(tasks); deadline=time.time()+14
    try:
        while pending and time.time() < deadline:
            done, pending = await asyncio.wait(pending, timeout=max(0.1, deadline-time.time()), return_when=asyncio.FIRST_COMPLETED)
            if not done: break
            for t in done:
                label=tasks[t]
                try: r=t.result()
                except Exception: r=""
                results[label]=r or ""
                if label == "gemini" and r:
                    for b in pending: b.cancel()
                    log.info("STT source: gemini")
                    return r
            if "gemini" in results and "google-free" in results:
                break
    except Exception:
        log.warning("STT race fail\n%s", traceback.format_exc())
    if results.get("gemini"): return results["gemini"]
    if results.get("google-free"):
        log.info("STT source: google-free")
        return results["google-free"]
    return ""

def _stt_google_free(wav_bytes: bytes, quick: bool = False) -> str:
    """Fallback: SpeechRecognition + Google free STT (16kHz mono chahiye)."""
    import io as _io, wave as _wv
    src=_wv.open(_io.BytesIO(wav_bytes), "rb")
    nch, wid, rate, nfr = src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getnframes()
    raw=src.readframes(nfr)
    if wid != 2: return ""
    import struct as _st
    vals=_st.unpack("<%dh" % (len(raw)//2), raw)
    if nch == 2:
        vals=vals[0::2]  # left channel hi kaafi hai
    if rate != 16000:
        step=rate/16000.0; out=[]; i=0.0
        while int(i) < len(vals):
            out.append(vals[int(i)]); i+=step
        vals=out
    mono=_st.pack("<%dh" % len(vals), *vals)
    bio=_io.BytesIO()
    with _wv.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(mono)
    import speech_recognition as _sr
    rec=_sr.Recognizer()
    with _sr.AudioFile(_io.BytesIO(bio.getvalue())) as source:
        audio=rec.record(source)
    pehli=""
    if quick:
        # Race wala tez rasta: sabse aam 3 bhashaye, pehli mili transcript = jawab (36 bhashaon wala intezaar khatm)
        for lang in ("hi-IN", "en-IN", "en-US"):
            try:
                t=rec.recognize_google(audio, language=lang)
            except Exception as e:
                # wajah dabao mat — log me dikhe (net band hai ya awaaz samajh nahi aayi)
                log.warning("STT google %s fail: %s: %s", lang, type(e).__name__, str(e)[:120])
                continue
            if t: return t
        return ""
    for lang in ("hi-IN", "en-IN", "en-US", "mr-IN", "bn-IN", "ta-IN", "te-IN",
                 "kn-IN", "ml-IN", "gu-IN", "pa-IN", "ur-IN", "es-ES", "fr-FR",
                 "de-DE", "pt-BR", "it-IT", "nl-NL", "ru-RU", "ja-JP", "ko-KR",
                 "zh-CN", "ar-EG", "tr-TR", "id-ID", "vi-VN", "th-TH", "ne-NP",
                 "si-LK", "fil-PH", "sw-KE", "uk-UA", "pl-PL", "fa-IR", "my-MM"):
        try:
            t=rec.recognize_google(audio, language=lang)
        except Exception:
            continue
        if not t: continue
        if not pehli: pehli=t
        low=t.lower().replace("\u093c", "")
        if any(w in low for w in ("Nezuko", "nezuko", "नेजुको", "नेजको", "नेज़ुको", "nezko")):
            return t
    return pehli

_WAKE_WORDS=("nezuko", "Nezuko", "nezuku", "nezko", "nuzuko", "ne juko", "ne zuko", "Nezuko", "neetu", "neetoo", "neethu", "nitu", "nittu", "neetu ko", "nitu ko",
             "nezo", "Nezo", "ne zu", "nijuko", "mezuko", "nezukho", "nezu", "ne zu ko",
             "नेज़ुको", "नेजुको", "नेज़ुको", "नेजुको", "नेजको", "नेज़को",
             "नीतू", "नीटू", "नेजु", "नेजू", "नेजो", "नेज़ो", "नीजुको", "नेतुको")
_WAKE_PATTERN="|".join(sorted((re.escape(w) for w in _WAKE_WORDS), key=len, reverse=True))
_VC_CONVO: dict[tuple[int, int], float] = {}
def _find_wake(transcript: str) -> bool:
    """Naam kisi bhi roop/script me bola ho — pehchan lo (Hindi me aksar Devanagari aata hai)."""
    low=(transcript or "").lower()
    saaf=low.replace("\u093c", "").replace("ज़", "ज")
    for w in _WAKE_WORDS:
        w2=w.lower().replace("\u093c", "").replace("ज़", "ज")
        if w in low or w2 in saaf:
            return True
    return False
async def _handle_voice_utterance(guild, user_id: int, pcm: bytes):
    """Ek bandi hui boli process Karo: wake word -> sawal -> jawab -> VC me bol do."""
    _pkey=(guild.id, user_id)
    if _pkey in _VC_PROCESSING: return
    _VC_PROCESSING.add(_pkey)
    _t0=time.time()
    try:
        if not _VOICE_LISTEN.get(guild.id): return
        now=time.time()
        if now - _VC_COOLDOWN.get(user_id, 0) < 2.5: return
        import struct as _stt_st
        try:
            _vals=_stt_st.unpack("<%dh" % (len(pcm)//2), pcm[:len(pcm)//2*2])
            _mx=max((abs(v) for v in _vals), default=0)
        except Exception:
            _mx=0
        _rms=0; _clip=0
        try:
            _n=max(1, len(_vals))
            _rms=int((sum(v*v for v in _vals)/_n) ** 0.5)
            _clip=sum(1 for v in _vals if abs(v) >= 32767) * 100 // _n
        except Exception: pass
        log.info("VC awaaz level max=%s rms=%s clip=%s%% (rms 300-4000=saaf boli, 10000+=shor/gadbad)", _mx, _rms, _clip)
        wav48=_pcm_to_wav_48k(pcm, 1)
        wav=_pcm_to_wav(pcm)
        if len(wav48) > 6_000_000: return
        transcript=await _stt_first_try(wav, wav48)
        if not transcript:
            try: transcript=await _stt_gemini(wav48)
            except Exception: transcript=""
        log.info("VC STT time %.1fs", time.time()-_t0)
        if not transcript:
            log.warning("STT transcript empty (koi boli samjh nahi aayi)")
            return
        log.info("VC suna user %s: %s", user_id, transcript[:160])
        wake_hit=_find_wake(transcript)
        convo_key=(guild.id, user_id)
        in_convo=time.time() < _VC_CONVO.get(convo_key, 0)
        if not wake_hit and not in_convo:
            log.info("VC: naam nahi suna gaya, jawab nahi diya")
            return
        _VC_COOLDOWN[user_id]=time.time()
        sawal=transcript
        if wake_hit:
            sawal=re.sub(_WAKE_PATTERN, " ", sawal, count=1, flags=re.IGNORECASE).strip(" ,.!?\n")
        # ek baar naam lo, fir 90 second tak bina naam liye baat chalti rahegi (insaanon jaisi baat-cheet)
        _VC_CONVO[convo_key]=time.time()+90
        ch=None
        chid=_VOICE_LISTEN_CH.get(guild.id)
        if chid:
            ch=bot.get_channel(chid)
            if ch is None:
                try: ch=await bot.fetch_channel(chid)
                except Exception: ch=None
        if not sawal or len(sawal) < 2:
            jawab="Yes, tell me — I'm listening! 🌸"
        else:
            hist=_memory.setdefault((guild.id, user_id), [])
            member=None
            try: member=guild.get_member(user_id)
            except Exception: pass
            naam=member.display_name if member else "friend"
            prompt_vc=(f"{naam} is talking to you in a voice channel like a friend. They said: {sawal[:1200]}. "
                       "Reply completely naturally, like a real friend — very short (1-2 sentences, max 40 words), in English only.")
            if _VC_STYLE.get(guild.id) == "japanese":
                prompt_vc=(f"{naam} is talking to you in a voice channel. They said: {sawal[:1200]}. "
                           "You are a cute Japanese anime girl — reply in a very soft, slightly sleepy and caring style, "
                           "ONLY in Japanese (日本語), very short (1-2 sentences). Sometimes you may start with a soft sound like 'んー…'.")
            try:
                jawab=await asyncio.wait_for(_ask_ai_fast(prompt_vc, hist, timeout=10, mode="chat"), timeout=12)
            except Exception:
                jawab=""
            log.info("VC AI time kul %.1fs", time.time()-_t0)
            if not jawab or jawab.startswith("⚠️"):
                jawab="Hmm, wait a second... something got stuck. Could you say that once more? 🌸"
            hist += [{"role": "user", "parts": [{"text": sawal[:1500]}]}, {"role": "model", "parts": [{"text": jawab[:1500]}]}]
            del hist[:-6]
        if ch is not None:
            try: await ch.send(f"🎙️ **Nezu VC:** {jawab}")
            except Exception: pass
        await _speak_in_vc(guild, jawab)
    except Exception:
        log.warning("voice utterance handle fail\n%s", traceback.format_exc())
    finally:
        _VC_PROCESSING.discard(_pkey)

async def _speak_in_vc_asli(guild, text: str) -> bool:
    """VC me bol do (gaana baj raha ho to rok ke bolti hu, fir queue se fir chalega)."""
    try:
        vc=guild.voice_client
        if vc is None or not vc.is_connected(): return False
        if not _ensure_opus(): return False
        audio=await _tts_bytes(text)
        if not audio: return False
        path=_tts_file(audio)
        st=_music_of(guild.id)
        gaana_tha=bool(vc.is_playing() or vc.is_paused() or st.get("current"))
        try:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
                await asyncio.sleep(0.3)
            _VC_SPEAKING[guild.id]=True
            done=asyncio.Event()
            def _cb(e, done=done):
                try: bot.loop.call_soon_threadsafe(done.set)
                except Exception: pass
            vc.play(discord.FFmpegPCMAudio(path, options="-vn"), after=_cb)
            try: await asyncio.wait_for(done.wait(), timeout=90)
            except Exception: pass
        finally:
            _VC_SPEAKING[guild.id]=False
            try: os.remove(path)
            except Exception: pass
        if gaana_tha and st.get("queue"):
            try: asyncio.create_task(_play_next(guild))
            except Exception: pass
        return True
    except Exception:
        log.warning("speak_in_vc fail\n%s", traceback.format_exc())
        return False

_VC_SPEAK_LOCKS: dict[int, asyncio.Lock] = {}
async def _speak_in_vc(guild, text: str) -> bool:
    """Ek guild me ek time par sirf ek hi boli chalegi — jawab takrayenge nahi."""
    lock=_VC_SPEAK_LOCKS.setdefault(guild.id, asyncio.Lock())
    async with lock:
        return await _speak_in_vc_asli(guild, text)

class _WakeSink(_voice_recv.AudioSink if _voice_recv is not None else object):
    """VC ki awaaz sunne wala sink (discord-ext-voice-recv) — per-user boli jama Karo,
    chup hone par kaat ke process karo."""
    def __init__(self, guild):
        self.guild=guild
        self.buffers: dict[int, bytearray] = {}
        self.last_seen: dict[int, float] = {}
        self.started=time.time()
        self._decoders: dict[int, object] = {}
        self._badcount: dict[int, int] = {}
        self._int_frames: dict[int, int] = {}
    def wants_opus(self) -> bool:
        # opus ham khud decode karenge taaki ek corrupted packet se
        # library ka sunne wala hissa crash na ho (OpusError fix)
        return True
    def write(self, user, data):
        try:
            uid=getattr(user, "id", None)
            if uid is None and _voice_recv is not None and hasattr(data, "source") and data.source is not None:
                uid=getattr(data.source, "id", None)
            if uid is None or (bot.user and uid == bot.user.id): return
            opus_bytes=None
            if data is not None:
                opus_bytes=getattr(data, "opus", None)
                if not opus_bytes:
                    pkt=getattr(data, "packet", None)
                    opus_bytes=getattr(pkt, "decrypted_data", None) if pkt is not None else None
            if not opus_bytes: return
            if opus_bytes == b"\xf8\xff\xfe": return  # Discord silence frame — bolna nahi hai
            dec=self._decoders.get(uid)
            if dec is None:
                try: dec=discord.opus.Decoder(); self._decoders[uid]=dec
                except Exception: return
            try:
                pcm=dec.decode(opus_bytes, fec=False)
            except Exception:
                # kharab packet — decoder reset karo, ye packet chhodo, sunna chlta rahega
                self._badcount[uid]=self._badcount.get(uid, 0)+1
                try: self._decoders[uid]=discord.opus.Decoder()
                except Exception: self._decoders.pop(uid, None)
                return
            if not pcm: return
            # Nezu bol rahi ho aur user bolna shuru kare (0.5 sec) -> woh khud ruk jayegi
            try:
                if _VC_SPEAKING.get(self.guild.id):
                    _nf=self._int_frames.get(uid, 0) + 1
                    self._int_frames[uid]=_nf
                    if _nf >= 25:
                        self._int_frames[uid]=0
                        _vc=self.guild.voice_client
                        if _vc is not None and _vc.is_playing(): _vc.stop()
                else:
                    self._int_frames[uid]=0
            except Exception: pass
            buf=self.buffers.setdefault(uid, bytearray())
            buf.extend(pcm)
            self.last_seen[uid]=time.time()
            if len(buf) > 48000 * 2 * 2 * 20:  # 20 sec se zyada ek saath na rakhe
                del buf[:-48000 * 2 * 2 * 10]
            return
        except Exception:
            pass
    def cleanup(self):
        self.buffers.clear(); self.last_seen.clear(); self._decoders.clear()
    async def watcher(self):
        """Chup ho jaye to boli ka tukda process karo (0.9 sec ki chuppi = baat khatm)."""
        while _VOICE_LISTEN.get(self.guild.id):
            await asyncio.sleep(0.25)
            now=time.time()
            for uid, last in list(self.last_seen.items()):
                buf=self.buffers.get(uid)
                if not buf: continue
                dur=len(buf) / (48000 * 2 * 2)
                if now - last >= 1.05 and dur >= 0.65:
                    pcm=bytes(buf); self.buffers[uid]=bytearray(); self.last_seen.pop(uid, None)
                    log.info("VC boli mili user %s (%.1f sec ki awaaz)", uid, dur)
                    asyncio.create_task(_handle_voice_utterance(self.guild, uid, pcm))
                elif dur <= 0.3 and now - last >= 3:
                    self.buffers.pop(uid, None); self.last_seen.pop(uid, None)

async def _vc_recording_done(sink, guild):
    log.info("voice recording band hui guild %s", guild.id)

def _start_listening(guild, channel_id: int):
    vc=guild.voice_client
    if vc is None: return False, "not_in_vc"
    if not _ensure_opus(): return False, "opus_missing"
    _VOICE_LISTEN[guild.id]=True
    _VOICE_LISTEN_CH[guild.id]=channel_id
    sink=_WakeSink(guild)
    if _voice_recv is None: return False, "no_voice_recv_lib"
    try:
        if not _voice_recv.VoiceRecvClient in type(vc).__mro__: raise RuntimeError("VC sunne wala client nahi hai — /join fir se Karo")
        if vc.is_listening(): vc.stop_listening()
        vc.listen(sink)
        try:
            if vc.channel is not None: _VC_VOICE_CH[guild.id]=vc.channel.id
        except Exception: pass
        log.info("voice listening chalu guild %s", guild.id)
    except Exception:
        log.warning("listen fail\n%s", traceback.format_exc())
        _VOICE_LISTEN[guild.id]=False
        return False, "recording_fail"
    t=_vc_tasks.get(guild.id)
    if not t or t.done():
        _vc_tasks[guild.id]=asyncio.create_task(sink.watcher())
    return True, "ok"

def _stop_listening(guild):
    _VOICE_LISTEN[guild.id]=False
    _VC_VOICE_CH.pop(guild.id, None)
    try:
        if guild.voice_client is not None:
            guild.voice_client.stop_listening()
    except Exception: pass
    t=_vc_tasks.pop(guild.id, None)
    if t: t.cancel()

async def _join_voice(channel):
    """VC join — handshake fail ho to saaf karke 3 baar tak dobara koshish.
    Phone-net par pehla handshake aksar tootta hai, ek retry me jud jata hai."""
    guild=channel.guild
    vc=guild.voice_client
    if vc is not None and vc.is_connected() and _voice_recv is not None and _voice_recv.VoiceRecvClient not in type(vc).__mro__:
        try: await vc.disconnect(force=True)
        except Exception: pass
        vc=None
    if vc is not None and vc.is_connected():
        if vc.channel == channel: return vc
        try:
            await vc.move_to(channel); return vc
        except Exception:
            log.warning("voice move fail, dobara connect hoga\n%s", traceback.format_exc())
            try: await vc.disconnect(force=True)
            except Exception: pass
    last=None
    for koshish in range(1, 4):
        try:
            vc=guild.voice_client
            if vc is not None and not vc.is_connected():
                try: await vc.disconnect(force=True)
                except Exception: pass
            _vcls=_voice_recv.VoiceRecvClient if _voice_recv is not None else discord.VoiceClient
            vc=await channel.connect(cls=_vcls, timeout=60, reconnect=True)
            if vc is not None and vc.is_connected():
                log.info("voice connected (koshish %s) #%s", koshish, channel.name)
                return vc
        except Exception as e:
            last=e
            log.warning("voice connect koshish %s fail\n%s", koshish, traceback.format_exc())
        try: await asyncio.sleep(2)
        except Exception: pass
    if last is not None: raise last
    return guild.voice_client

def _schedule_vc_rejoin(guild):
    """Bot VC se gir jaye aur vcchat On ho, to khud usi channel me wapas aao."""
    if not _VOICE_LISTEN.get(guild.id): return
    home=_VC_VOICE_CH.get(guild.id)
    if not home: return
    t=_VC_REJOIN.get(guild.id)
    if t is not None and not t.done(): return
    _VC_REJOIN[guild.id]=asyncio.create_task(_vc_rejoin(guild, home))

async def _vc_rejoin(guild, home_id: int):
    try:
        await asyncio.sleep(4)
        if not _VOICE_LISTEN.get(guild.id): return
        vc=guild.voice_client
        if vc is not None and vc.is_connected(): return  # khud jud gaya
        now=time.time()
        tries=[t for t in _VC_REJOIN_TRIES.get(guild.id, []) if now-t < 300]
        if len(tries) >= 4:
            log.warning("VC auto-rejoin: bohot koshish ho gayi guild %s, ab /vcchat On dobara Karo", guild.id)
            return
        tries.append(now); _VC_REJOIN_TRIES[guild.id]=tries
        ch=guild.get_channel(home_id)
        if ch is None:
            try: ch=await bot.fetch_channel(home_id)
            except Exception: return
        vc=await _join_voice(ch)
        if vc is not None and vc.is_connected():
            _start_listening(guild, _VOICE_LISTEN_CH.get(guild.id, home_id))
            log.info("VC auto-rejoin ho gaya guild %s (#%s)", guild.id, getattr(ch, "name", "?"))
    except Exception:
        log.warning("VC auto-rejoin fail\n%s", traceback.format_exc())

@bot.event
async def on_voice_state_update(member, before, after):
    """Nezu VC se girte hi (net/handshake) — vcchat On ho to khud wapas bula lo."""
    try:
        if bot.user is None or member.id != bot.user.id: return
        guild=member.guild
        if after is not None and after.channel is not None:
            if _VOICE_LISTEN.get(guild.id): _VC_VOICE_CH[guild.id]=after.channel.id
            return
        if after is not None and after.channel is None:
            _schedule_vc_rejoin(guild)
    except Exception:
        pass


class MusicPanelView(discord.ui.View):
    """Music control panel — Now Playing message ke neeche lage button."""
    def __init__(self): super().__init__(timeout=None)

    async def _vc(self, interaction: discord.Interaction):
        vc=interaction.guild.voice_client if interaction.guild else None
        if vc is None:
            await interaction.response.send_message("ℹ️ Nezu is not in a VC right now — play a song first with `/play`.", ephemeral=True)
            return None
        user_voice=getattr(interaction.user, "voice", None)
        if not user_voice or not user_voice.channel or user_voice.channel != vc.channel:
            await interaction.response.send_message("❌ To use the panel, you must be in the same VC where the song is playing.", ephemeral=True)
            return None
        return vc

    @discord.ui.button(label="Pause/Resume", emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="music:pause")
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc=await self._vc(interaction)
        if vc is None: return
        if vc.is_playing():
            vc.pause(); await interaction.response.send_message("⏸️ **Paused** (panel)", ephemeral=True)
        elif vc.is_paused():
            vc.resume(); await interaction.response.send_message("▶️ **Resumed** (panel)", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc=await self._vc(interaction)
        if vc is None: return
        if vc.is_playing() or vc.is_paused():
            vc.stop(); await interaction.response.send_message("⏭️ **Skipped!** (panel)", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music:stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc=await self._vc(interaction)
        if vc is None: return
        st=_music_of(interaction.guild.id)
        st["queue"].clear(); st["current"]=None; st["manual_stop"]=True
        try: vc.stop()
        except Exception: pass
        await vc.disconnect()
        for child in self.children: child.disabled=True
        try: await interaction.message.edit(view=self)
        except Exception: pass
        await interaction.response.send_message("⏹️ **Music stopped!** (panel) Nezu left the VC.")

    @discord.ui.button(label="Queue", emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="music:queue")
    async def btn_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
        st=_music_of(interaction.guild.id)
        cur=st.get("current")
        if not cur and not st["queue"]:
            await interaction.response.send_message("🎵 The queue is empty.", ephemeral=True); return
        lines=[]
        if cur: lines.append(f"▶️ **Now Playing:** {cur['title']}")
        for i, t in enumerate(st["queue"][:10], 1): lines.append(f"{i}. {t['title']}")
        await interaction.response.send_message("🎵 **Queue:**\n" + "\n".join(lines), ephemeral=True)

    @discord.ui.button(label="Autoplay", emoji="🔀", style=discord.ButtonStyle.success, custom_id="music:autoplay")
    async def btn_autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc=await self._vc(interaction)
        if vc is None: return
        st=_music_of(interaction.guild.id)
        st["autoplay"]=not st.get("autoplay", True)
        await interaction.response.send_message(f"🔀 **Autoplay {'ON' if st['autoplay'] else 'OFF'}!** When a song ends, a similar song {'will play automatically' if st['autoplay'] else 'will not play automatically'}.", ephemeral=True)

async def _song_autocomplete(interaction: discord.Interaction, current: str):
    """Gaane ka naam type karte hi upar suggestions."""
    q=(current or "").strip()
    if len(q) < 2: return []
    try:
        url="https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q=" + urllib.parse.quote(q)
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status != 200: return []
                data=json.loads(await resp.text())
        return [app_commands.Choice(name=str(x)[:100], value=str(x)[:100]) for x in (data[1][:8] if len(data) > 1 else [])]
    except Exception:
        return []

@bot.tree.command(name="vcchat", description="Voice chat with Nezu: listen and answer in VC, or make her speak typed text")
@app_commands.describe(action="On/Off/Status/Style, or Speak to test her voice", text="Speak action: type the text Nezu should say out loud")
@app_commands.choices(action=[
    app_commands.Choice(name="On — start listening in your voice channel", value="on"),
    app_commands.Choice(name="Off — stop listening", value="off"),
    app_commands.Choice(name="Status — check voice chat state", value="status"),
    app_commands.Choice(name="Speak — Nezu will say the typed text out loud (voice test)", value="speak"),
    app_commands.Choice(name="Style: Japanese 🌸 (cute replies in Japanese)", value="style_japanese"),
    app_commands.Choice(name="Style: Sweet (default, English)", value="style_sweet"),
])
async def vcchat_cmd(interaction: discord.Interaction, action: str = "on", text: str = ""):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True); return
    act=(action or "on").lower().strip()
    guild=interaction.guild
    if act in ("style_japanese", "style_sweet"):
        _VC_STYLE[guild.id]="japanese" if act == "style_japanese" else "sweet"
        _lbl="Japanese 🌸 (soft, cute replies in Japanese only)" if act == "style_japanese" else "Sweet (default — English)"
        await interaction.response.send_message(f"🎧 VC style set: **{_lbl}**\n(If listening is on, the style applies right away.)", ephemeral=True)
        if _VOICE_LISTEN.get(guild.id):
            asyncio.create_task(_speak_in_vc(guild, "こんにちは、ネズだよ。これからは日本語で、ゆっくりお話ししようね。" if act == "style_japanese" else "Okay, I will speak in English now!"))
        return
    if act == "status":
        halat="ON 🎙️" if _VOICE_LISTEN.get(guild.id) else "OFF"
        _styl="Japanese 🌸" if _VC_STYLE.get(guild.id) == "japanese" else "Sweet"
        await interaction.response.send_message(f"🎧 Nezu voice chat: **{halat}** • Style: **{_styl}**\nTest her voice: `/vcchat` → **Speak** → type any text. Talk to her: `/vcchat` → **On**, then say **'Nezuko, how are you?'**", ephemeral=True); return
    if act == "off":
        _stop_listening(guild)
        await interaction.response.send_message("🔇 Nezu stopped listening in the VC.", ephemeral=True); return
    if act == "speak":
        boli=(text or "").strip()
        if not boli:
            await interaction.response.send_message("❌ Choose **Speak**, then type something in the **text** box — Nezu will say it out loud in the VC.", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        vc=guild.voice_client
        if vc is None or not vc.is_connected():
            voice=getattr(interaction.user, "voice", None)
            if not voice or not voice.channel:
                await interaction.followup.send("❌ First join a **voice channel** yourself, then use `/vcchat` → **Speak**.", ephemeral=True); return
            try: vc=await _join_voice(voice.channel)
            except Exception:
                log.error("vcchat speak join fail\n%s", traceback.format_exc())
                await interaction.followup.send("⚠️ Nezu could not join your voice channel. Check that she has **Connect + Speak** permission, then try again.", ephemeral=True); return
        bola=await _speak_in_vc(guild, boli[:300])
        if bola:
            await interaction.followup.send(f"🔊 **Nezu spoke in the VC:** “{boli[:300]}”\nIf you heard her, her voice is working. Now try **On** and say: **'Nezuko, how are you?'**", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Nezu could not speak. In Termux, make sure `ffmpeg`, `libopus` and `opusfile` are installed, restart the bot, then try **Speak** again.", ephemeral=True)
        return
    # On: zarurat pade to Nezu khud tumhare VC me aa jayegi (alag se /join nahi karna padega)
    vc=guild.voice_client
    need_join = vc is None or not vc.is_connected() or (_voice_recv is not None and _voice_recv.VoiceRecvClient not in type(vc).__mro__)
    if need_join:
        voice=getattr(interaction.user, "voice", None)
        if not voice or not voice.channel:
            await interaction.response.send_message("❌ First join a **voice channel** yourself, then use `/vcchat` → **On**. Nezu will come to your channel herself.", ephemeral=True); return
        await interaction.response.defer()
        try: vc=await _join_voice(voice.channel)
        except Exception:
            log.error("vcchat auto-join fail\n%s", traceback.format_exc())
            await interaction.followup.send("⚠️ Nezu could not join your voice channel. Check her **Connect + Speak** permission, then try again."); return
        ok, wajah=_start_listening(guild, interaction.channel.id)
        if ok:
            await interaction.followup.send("🎙️ **Nezu is in your VC and listening!** Now speak:\n> **'Nezuko, how are you?'**\nShe will hear her name and answer out loud 🌸\n_(Say her name first; after that you can keep talking for a while without repeating it.)_")
            asyncio.create_task(_speak_in_vc(guild, "こんにちは、ネズだよ。ゆっくりお話ししようね。" if _VC_STYLE.get(guild.id) == "japanese" else "Yes, I'm here and listening. Tell me, what's up?"))
        elif wajah == "opus_missing":
            await interaction.followup.send("⚠️ In Termux, run `pkg install -y libopus opusfile`, restart the bot, then use `/vcchat` → **On** again.")
        else:
            await interaction.followup.send("⚠️ Listening could not start — restart the bot and try `/vcchat` → **On** again.")
        return
    ok, wajah=_start_listening(guild, interaction.channel.id)
    if ok:
        await interaction.response.send_message("🎙️ **Nezu is listening!** Now speak in the VC:\n> **'Nezuko, how are you?'**\nShe will hear her name and answer out loud 🌸\n_(Say her name first; after that you can keep talking for a while without repeating it.)_")
        asyncio.create_task(_speak_in_vc(guild, "こんにちは、ネズだよ。ゆっくりお話ししようね。" if _VC_STYLE.get(guild.id) == "japanese" else "Yes, I'm here and listening. Tell me, what's up?"))
    elif wajah == "opus_missing":
        await interaction.response.send_message("⚠️ In Termux, run `pkg install -y libopus opusfile`, restart the bot, then use `/vcchat` → **On** again.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Listening could not start — restart the bot and try `/vcchat` → **On** again.", ephemeral=True)

@bot.tree.command(name="join", description="Call Nezu into your voice channel")
async def join_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Join only works inside a server.", ephemeral=True); return
    voice=getattr(interaction.user, "voice", None)
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ First join a **voice channel** yourself, then use `/join`.", ephemeral=True); return
    vc=interaction.guild.voice_client
    try:
        vc=await _join_voice(voice.channel)
    except Exception as e:
        log.error("join connect fail\n%s", traceback.format_exc())
        if isinstance(e, RuntimeError) and ("PyNaCl" in str(e) or "davey" in str(e)):
            await interaction.response.send_message("⚠️ Nezu could not join the VC — the **voice library (PyNaCl/davey) is missing** in Termux; this is not a permission problem. Tell an admin; the fix is in Termux.", ephemeral=True)
        elif isinstance(e, discord.Forbidden):
            await interaction.response.send_message("⚠️ Nezu could not join the VC — her `Connect/Speak` permission in this VC is really disabled.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Nezu could not join the VC — reason: `{type(e).__name__}`. Please tell an admin.", ephemeral=True)
        return
    await interaction.response.send_message(f"🎵 **Nezu joined {voice.channel.name}!** Now use `/p song name` and that exact song will play. Use the panel to skip/stop/control autoplay.")

@bot.tree.command(name="play", description="Play a song in your voice channel (search or YouTube link)")
@app_commands.describe(song="Song name or YouTube link")
@app_commands.autocomplete(song=_song_autocomplete)
async def play_cmd(interaction: discord.Interaction, song: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Music only works inside a server.", ephemeral=True); return
    voice=getattr(interaction.user, "voice", None)
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ First join a **voice channel**, then use `/play`.", ephemeral=True); return
    if _ensure_ytdlp() is None:
        await interaction.response.send_message("❌ `yt-dlp` is not loading inside the bot right now. In Termux run `python3 -m pip install -U yt-dlp`, then restart the bot.", ephemeral=True); return
    await interaction.response.defer()
    lag=await interaction.followup.send(f"🔎 **Nezu is searching for your song...** 🎵 _{song}_")
    vc=interaction.guild.voice_client
    try:
        vc=await _join_voice(voice.channel)
    except Exception as e:
        log.error("music connect fail\n%s", traceback.format_exc())
        if isinstance(e, RuntimeError) and ("PyNaCl" in str(e) or "davey" in str(e)):
            await lag.edit(content="⚠️ I could not join the voice channel — the **voice library (PyNaCl/davey) is missing** in Termux; this is not a permission problem.")
        elif isinstance(e, discord.Forbidden):
            await lag.edit(content="⚠️ I could not join the voice channel — my `Connect/Speak` permission in this VC is really disabled.")
        else:
            await lag.edit(content=f"⚠️ I could not join the voice channel — reason: `{type(e).__name__}`")
        return
    loop=asyncio.get_event_loop()
    try:
        track=await loop.run_in_executor(None, _yt_extract, song)
    except Exception:
        log.error("music search fail\n%s", traceback.format_exc())
        await lag.edit(content="⚠️ I could not find that song — check the name and try again."); return
    track["requester"]=interaction.user.display_name
    st=_music_of(interaction.guild.id)
    st["manual_stop"]=False
    st["text_channel"]=interaction.channel
    if vc.is_playing() or vc.is_paused():
        st["queue"].append(track)
        await lag.edit(content=f"➕ **Added to the queue (#{len(st['queue'])}):** {track['title']} 🎵")
    else:
        st["queue"].append(track)
        await lag.edit(content=f"🎵 **Found it:** {track['title']} — playing now...")
        await _play_next(interaction.guild)

@bot.tree.command(name="p", description="Shortcut of /play — play a song in your voice channel")
@app_commands.describe(song="Song name or YouTube link")
@app_commands.autocomplete(song=_song_autocomplete)
async def p_cmd(interaction: discord.Interaction, song: str):
    await play_cmd.callback(interaction, song)

@bot.tree.command(name="skip", description="Skip the current song")
async def skip_cmd(interaction: discord.Interaction):
    vc=interaction.guild.voice_client if interaction.guild else None
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ **Skipped!** The next song is coming...")
    else:
        await interaction.response.send_message("ℹ️ Nothing is playing right now.", ephemeral=True)

@bot.tree.command(name="stop", description="Stop music, clear queue and leave voice channel")
async def stop_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    st=_music_of(interaction.guild.id)
    st["queue"].clear(); st["current"]=None; st["manual_stop"]=True
    vc=interaction.guild.voice_client
    if vc:
        try: vc.stop()
        except Exception: pass
        await vc.disconnect()
    await interaction.response.send_message("⏹️ **Music stopped!** The queue was cleared and Nezu left the VC.")

@bot.tree.command(name="pause", description="Pause the current song")
async def pause_cmd(interaction: discord.Interaction):
    vc=interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ **Paused.** It will play again with `/resume`.")
    else:
        await interaction.response.send_message("ℹ️ Nothing is playing right now that I can pause.", ephemeral=True)

@bot.tree.command(name="resume", description="Resume the paused song")
async def resume_cmd(interaction: discord.Interaction):
    vc=interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ **Resumed!**")
    else:
        await interaction.response.send_message("ℹ️ No song is paused.", ephemeral=True)

@bot.tree.command(name="queue", description="See the music queue")
async def queue_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    st=_music_of(interaction.guild.id)
    cur=st.get("current")
    if not cur and not st["queue"]:
        await interaction.response.send_message("🎵 The queue is empty — play a song with `/play`.", ephemeral=True); return
    lines=[]
    if cur: lines.append(f"▶️ **Now Playing:** {cur['title']}")
    for i, t in enumerate(st["queue"][:10], 1):
        lines.append(f"{i}. {t['title']}")
    if len(st["queue"]) > 10: lines.append(f"...and {len(st['queue'])-10} more songs")
    await interaction.response.send_message("🎵 **Music Queue:**\n" + "\n".join(lines))

@bot.tree.command(name="nowplaying", description="See what song is playing right now")
async def nowplaying_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Only inside a server.", ephemeral=True); return
    cur=_music_of(interaction.guild.id).get("current")
    if cur:
        dur=int(cur.get("duration") or 0)
        mmss=f" ({dur//60}:{dur%60:02d})" if dur else ""
        await interaction.response.send_message(f"🎵 **Now Playing:** {cur['title']}{mmss}\n_(Requested by {cur['requester']})_")
    else:
        await interaction.response.send_message("ℹ️ Nothing is playing right now — play one with `/play`.", ephemeral=True)

@bot.tree.command(name="autoplay", description="Autoplay: when a song ends, a similar song plays automatically (On/Off/Status)")
@app_commands.describe(action="Choose On, Off, or Status — default is Status")
@app_commands.choices(action=[
    app_commands.Choice(name="On — similar songs will play automatically", value="on"),
    app_commands.Choice(name="Off — only queued songs", value="off"),
    app_commands.Choice(name="Status — view the setting", value="status"),
])
async def autoplay_cmd(interaction: discord.Interaction, action: str = "status"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Autoplay only works inside a server.", ephemeral=True); return
    st=_music_of(interaction.guild.id)
    if action == "on":
        st["autoplay"]=True
        await interaction.response.send_message("🔀 **Autoplay ON!** The song you choose will play, and when it ends a similar song will start automatically. 🎵")
    elif action == "off":
        st["autoplay"]=False
        await interaction.response.send_message("🔀 **Autoplay OFF.** Now only songs you add with `/play` or `/p` will play.")
    else:
        state="ON ✅" if st.get("autoplay", True) else "OFF"
        await interaction.response.send_message(f"🔀 Autoplay is **{state}** in this server.")

# ---- GitHub -> Discord ----
async def github_handler(request: web.Request):
    # Secret set na ho to ye rasta band — bahar ka koi banda nakli message nahi bhej sakta
    if not GITHUB_HOOK_SECRET:
        return web.json_response({"error":"disabled"}, status=404)
    import hmac as _hmac, hashlib as _hashlib
    _body=await request.read()
    _sig=request.headers.get("X-Hub-Signature-256", "")
    _mac=_hmac.new(GITHUB_HOOK_SECRET.encode(), _body, _hashlib.sha256).hexdigest()
    if not _sig.startswith("sha256=") or not _hmac.compare_digest(_sig[7:], _mac):
        return web.json_response({"error":"bad signature"}, status=403)
    try: payload=json.loads(_body.decode("utf-8") or "{}")
    except: return web.json_response({"error":"invalid json"}, status=400)
    channel_id=int(payload.get("channel_id") or 0)
    text=str(payload.get("text") or "Update")
    title=str(payload.get("title") or "Update")
    url=str(payload.get("url") or "")
    color=int(payload.get("color") or 3447003)
    ch=bot.get_channel(channel_id) if channel_id else None
    if ch is None and WELCOME_CHANNEL_ID: ch=bot.get_channel(WELCOME_CHANNEL_ID)
    if ch is None: return web.json_response({"error":"channel not found"}, status=404)
    embed=discord.Embed(title=title, description=text, color=color, url=url or None)
    embed.set_footer(text=str(payload.get("event", "update")))
    await ch.send(embed=embed)
    log.info("webhook -> #%s", ch)
    return web.json_response({"ok":True, "channel":ch.id})

async def start_http_server():
    app=web.Application()
    app.router.add_post("/github", github_handler)
    app.router.add_get("/health", lambda r: web.json_response({"ok":True}))
    runner=web.AppRunner(app); await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",HTTP_PORT); await site.start()
    log.info("HTTP server chalu (port %s)", HTTP_PORT)

def main():
    if not TOKEN: raise SystemExit("DISCORD_BOT_TOKEN is not set. Put it in the .env file.")
    import asyncio
    log.info("Bot is starting...")
    async def _run():
        # HTTP (GitHub webhook) server fail ho jaye (port busy wagera) to bhi
        # bot online hona NAHI rukna chahiye — pehle yehi ek chhupa crash tha
        try: await start_http_server()
        except Exception: log.warning("HTTP server did not start (port busy?) — the bot will still come online\n%s", traceback.format_exc())
        # Supervisor loop: net/DNS ki wajah se gateway connect pehli baar
        # fail ho to discord.py khud AttributeError se resumr jata hai
        # (ws None par .sequence) — isliye bot band nahi hoga, retry hoga.
        _tries=0
        while True:
            try:
                await bot.start(TOKEN)
                break  # saaf band (bot.close) — bahar niklo
            except discord.LoginFailure:
                log.error("LOGIN FAIL: the token is wrong/invalid (401). Put the correct DISCORD_BOT_TOKEN in .env.")
                raise SystemExit(1)
            except discord.PrivilegedIntentsRequired:
                log.error("LOGIN FAIL: Turn ON the Server Members Intent in the Developer Portal (Privileged Gateway Intents).")
                raise SystemExit(1)
            except Exception:
                _tries+=1
                log.error("Discord se connect fail (network/DNS gadbad?) — 20s me retry #%s\n%s", _tries, traceback.format_exc())
                await asyncio.sleep(20)
    try: asyncio.run(_run())
    except KeyboardInterrupt: pass
if __name__=="__main__": main()
