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

_startup_dm_sent=False
intents=discord.Intents.default()
intents.members=True
intents.message_content=True
bot=commands.Bot(command_prefix="!", intents=intents)

class VerifyButton(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify karo", style=discord.ButtonStyle.success, emoji="✅", custom_id="autodm:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not VERIFY_ROLE_ID:
            await interaction.response.send_message("⚠️ `VERIFY_ROLE_ID` set nahi hai.", ephemeral=True); return
        role=interaction.guild.get_role(VERIFY_ROLE_ID) if interaction.guild else None
        if role is None:
            await interaction.response.send_message("⚠️ Role nahi mila.", ephemeral=True); return
        try: await interaction.user.add_roles(role, reason="verify button")
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ Mere paas role dene ki permission nahi. Mera role Verified se upar rakho.", ephemeral=True); return
        try: await interaction.message.edit(view=None)
        except: pass
        await interaction.response.send_message(f"✅ Verified! **{role.name}** mil gaya.", ephemeral=True)

@bot.event
async def setup_hook():
    bot.add_view(VerifyButton())
    bot.add_view(GiveawayView())
    bot.add_view(MusicPanelView())
    # SIRF global sync — guild copies banaoge to Discord me har command 2-2 dikhega
    try:
        await bot.tree.sync()
        log.info("slash commands synced (global only)")
    except Exception as e: log.warning("sync fail: %s", e)

_PROFILE_NAME="Nezu"
_PROFILE_PIC=Path(__file__).resolve().parent / "nezu.jpg"
_PROFILE_MARK=Path(__file__).resolve().parent / "profile_done.json"
async def _apply_profile_once():
    """Bot ka naam 'Nezu' aur profile pic (nezu.jpg) ek baar set karo.
    Discord naam badalne par rate-limit lagata hai, isliye marker file
    me nishan laga dete hai — har restart par try nahi hoga."""
    try:
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
                await owner.send(f"🟢 **NezukoHelper online hai!**\nServers: **{len(bot.guilds)}** | Commands: **{total_cmds}**\nBot ab kaam ke liye ready hai. `/help` se saare commands dekh sakte ho.")
        except Exception:
            pass


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.warning("command error (%s): %s", getattr(interaction.command, "name", "?"), error)
    msg="⚠️ Kuch galat ho gaya — dobara try Karo. Dikkat rahe to admin ko batao."
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
            await _fallback_post(member, "DM nahi ja paya (user ne DM block kiya hai)")
    except Exception:
        log.error("DM fail %s\n%s", member, traceback.format_exc())
        if not channel_welcome:
            await _fallback_post(member, "DM nahi ja paya")

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

async def _deny(interaction: discord.Interaction, msg="❌ Ye command sirf admins/mods ke liye hai."):
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`")

@bot.tree.command(name="help", description="Show all bot commands")
async def help_cmd(interaction: discord.Interaction):
    embed=discord.Embed(title="🤖 NezukoHelper — Commands", color=0x9b59b6)
    embed.add_field(name="🛡️ Admin / Moderation", value="`/kick` `/ban` `/unban` `/timeout` `/untimeout` `/warn` `/warnings` `/clearwarn` `/clear` `/slowmode` `/lock` `/unlock` `/role_add` `/role_remove` `/nickname`", inline=False)
    embed.add_field(name="📢 Announcement", value="`/say` `/announce` `/dm` `/report`", inline=False)
    embed.add_field(name="ℹ️ Jankari", value="`/ping` `/botinfo` `/uptime` `/serverinfo` `/userinfo` `/avatar` `/membercount` `/roleinfo`", inline=False)
    embed.add_field(name="🎉 Utility", value="`/poll` `/remind` `/help`", inline=False)
    embed.add_field(name="🎁 Giveaway", value="`/giveaway` — prize, time (jaise `30m`, `2h`, `1d`) aur winners set karke stylish giveaway start karo. Time khatam hote hi winner automatic announce hoga.", inline=False)
    embed.add_field(name="⭐ Levels • Tickets • Reminders", value="Chat karne par XP milega — `/rank` • `/leaderboard` • Support ke liye `/ticket`, band karne ko `/close` • `/reminders` • `/remind_cancel`", inline=False)
    embed.add_field(name="🪙 Economy", value="`/balance` • `/daily` (roz free coins) • `/pay` • `/gamble`", inline=False)
    embed.add_field(name="🛡️ AutoMod", value="`/antilink` • `/antispam` • `/badwords` • `/antiraid` • `/setmodlog` — link, spam, gaali khud delete + raid par alert + saari moderation ka log (mods exempt).", inline=False)
    embed.add_field(name="🔐 Security / AntiNuke", value="`/antinuke` — 60s me 3+ delete/ban/bot-add par khud action • `/nuke` channel saaf-naya • `/lockdown` pura server band • `/quarantine` roles hatao • `/purge` Ek member ke messages saaf", inline=False)
    embed.add_field(name="🎭 Auto-Role", value="`/autorole_bot_add` (naya member join kare to khud role milega) • `/autorole_bot_remove` • `/autorole_bot_status`", inline=False)
    embed.add_field(name="👑 No Prefix (Owner Only)", value="`/noprefix` — granted member can chat with the AI without `/ask` or a mention. Power lasts **2 months**.", inline=False)
    embed.add_field(name="🤖 AI Chat", value="`/ask sawal` • `/chatbot` • `/summarize` — sawal pucho, tag-chat chalu Karo, ya lambi chat ka short summary banao", inline=False)
    embed.add_field(name="🎨 AI Image", value="`/image kya banana hai` — bas ye ek hi image wala command hai", inline=False)
    embed.add_field(name="🎵 Music", value="`/play` • `/p gaana` • `/join` • `/skip` • `/stop` • `/pause` • `/resume` • `/queue` • `/nowplaying` • `/autoplay` — VC join karke gaane ka naam ya YouTube link do", inline=False)
    embed.add_field(name="🎙️ VC Voice Chat", value="`/vcchat On` — fir VC me bolo **'Nezuko, ...'** (ya **'Nezo, ...'**), Nezuko sunke wahi awaaz me jawab degi. `/vcchat Off` se band.", inline=False)
    embed.add_field(name="✨ More Utility & Fun", value="`/invite` `/support` `/rules` `/suggest` `/coinflip` `/dice` `/choose` `/eightball` `/rps` `/calc` `/password` `/qr` `/color` `/emojiinfo` `/channelinfo` `/roles` `/banner` `/afk` `/snipe` `/timer`", inline=False)
    embed.set_footer(text="Naya member aane pe bot khud welcome DM bhejta hai.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="botinfo", description="Show bot information")
async def botinfo(interaction: discord.Interaction):
    embed=discord.Embed(title="🤖 Bot Info", color=0x3498db)
    embed.add_field(name="Naam", value=str(bot.user), inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)}ms", inline=True)
    embed.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="uptime", description="Show how long the bot has been online")
async def uptime(interaction: discord.Interaction):
    sec=int(time.time()-START_TIME); d,sec=divmod(sec,86400); h,sec=divmod(sec,3600); m,sec=divmod(sec,60)
    await interaction.response.send_message(f"⏱️ Bot online hai: **{d}d {h}h {m}m {sec}s** se", ephemeral=True)

@bot.tree.command(name="serverinfo", description="Show server information")
async def serverinfo(interaction: discord.Interaction):
    g=interaction.guild
    if g is None: await interaction.response.send_message("Ye sirf server me chalega.", ephemeral=True); return
    embed=discord.Embed(title=f"🏰 {g.name}", color=0x2ecc71)
    embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
    embed.add_field(name="Members", value=str(g.member_count), inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Banaya gaya", value=g.created_at.strftime("%d %b %Y"), inline=True)
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="Show a member's information")
@app_commands.describe(member="Kis member ki jankari chahiye")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    embed=discord.Embed(title=f"👤 {member.display_name}", color=0xf39c12)
    embed.add_field(name="Username", value=str(member), inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Server join", value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "-", inline=True)
    embed.add_field(name="Account bana", value=member.created_at.strftime("%d %b %Y"), inline=True)
    roles=[r.mention for r in member.roles if r.name!="@everyone"]
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles)[:1000] or "koi role nahi", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Show a member's avatar (large)")
@app_commands.describe(member="Kiska avatar chahiye")
async def avatar(interaction: discord.Interaction, member: discord.Member):
    embed=discord.Embed(title=f"🖼️ {member.display_name} ka avatar", color=0x9b59b6)
    embed.set_image(url=member.display_avatar.with_size(1024).url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="membercount", description="Show total member count")
async def membercount(interaction: discord.Interaction):
    if interaction.guild is None: await interaction.response.send_message("❌ Ye sirf server me chalega.", ephemeral=True); return
    await interaction.response.send_message(f"👥 **{interaction.guild.name}** me kul **{interaction.guild.member_count}** members hai.", ephemeral=True)

@bot.tree.command(name="roleinfo", description="Show role information")
@app_commands.describe(role="Kis role ki jankari chahiye")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed=discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="Members", value=str(len(role.members)), inline=True)
    embed.add_field(name="Position", value=str(role.position), inline=True)
    embed.add_field(name="Mentionable", value="Ha" if role.mentionable else "Nahi", inline=True)
    embed.add_field(name="Admin hai?", value="Ha" if role.permissions.administrator else "Nahi", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="say", description="Make the bot send a message (admin)")
@app_commands.describe(text="Kya bhejna hai", channel="Kis channel me (khali = yahi channel)")
async def say(interaction: discord.Interaction, text: str, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.send(text)
    await interaction.response.send_message(f"✅ Bhej diya {ch.mention}", ephemeral=True)

@bot.tree.command(name="announce", description="Send an embed announcement (admin)")
@app_commands.describe(title="Title", description="Announcement", channel="Kis channel me (khali = yahi channel)")
async def announce(interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    embed=discord.Embed(title=title, description=description, color=0x9b59b6)
    embed.set_footer(text=f"Announcement by {interaction.user.display_name}")
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ Announce ho gaya {ch.mention}", ephemeral=True)

@bot.tree.command(name="dm", description="Send a DM to a member via the bot (admin)")
@app_commands.describe(member="Kisko DM bhejna hai", message="DM me kya bhejna hai")
async def dm_cmd(interaction: discord.Interaction, member: discord.Member, message: str):
    if not _is_mod(interaction): await _deny(interaction); return
    try:
        await member.send(f"📩 **{interaction.guild.name}** se message:\n\n{message}")
        await interaction.response.send_message(f"✅ DM gaya -> {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ {member.mention} ne DM band kiye hai.", ephemeral=True)

@bot.tree.command(name="clear", description="Delete channel messages (admin)")
@app_commands.describe(amount="Kitne messages delete karne hai (1-100)")
async def clear(interaction: discord.Interaction, amount: int):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 1 <= amount <= 100: await interaction.response.send_message("❌ 1 se 100 ke beech number do.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try: deleted=await interaction.channel.purge(limit=amount)
    except Exception: await interaction.followup.send("❌ Messages delete nahi ho paye — mujhe **Manage Messages + Read Message History** permission chahiye.", ephemeral=True); return
    await interaction.followup.send(f"🗑️ {len(deleted)} messages delete kiye.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set channel slowmode (admin)")
@app_commands.describe(seconds="Kitne second ka slowmode (0 = band)", channel="Kis channel me (khali = yahi)")
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 0 <= seconds <= 21600: await interaction.response.send_message("❌ 0 se 21600 ke beech do.", ephemeral=True); return
    ch=channel or interaction.channel
    try: await ch.edit(slowmode_delay=seconds)
    except Exception: await interaction.response.send_message("❌ Slowmode nahi laga — mujhe **Manage Channels** permission chahiye.", ephemeral=True); return
    await interaction.response.send_message(f"🐌 {ch.mention} ka slowmode **{seconds}s** ho gaya.", ephemeral=True)

@bot.tree.command(name="lock", description="Lock this channel (admin)")
@app_commands.describe(channel="Kis channel ko band karna hai (khali = yahi)")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    try: await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    except Exception: await interaction.response.send_message("❌ Channel lock nahi ho paya — mujhe **Manage Channels/Roles** permission chahiye.", ephemeral=True); return
    await ch.send("🔒 Ye channel band kiya gaya hai.")
    await interaction.response.send_message(f"🔒 {ch.mention} band ho gaya.", ephemeral=True)

@bot.tree.command(name="unlock", description="Unlock this channel (admin)")
@app_commands.describe(channel="Kis channel ko kholna hai (khali = yahi)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    try: await ch.set_permissions(interaction.guild.default_role, send_messages=None)
    except Exception: await interaction.response.send_message("❌ Channel unlock nahi ho paya — mujhe **Manage Channels/Roles** permission chahiye.", ephemeral=True); return
    await interaction.response.send_message(f"🔓 {ch.mention} khul gaya.", ephemeral=True)

@bot.tree.command(name="kick", description="Kick a member (admin)")
@app_commands.describe(member="Kisko nikalna hai", reason="Wajah")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "koi wajah nahi"):
    if not interaction.user.guild_permissions.kick_members: await _deny(interaction); return
    try: await member.kick(reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role us member se neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"👢 {member.mention} ko kick kiya. Wajah: {reason}")

@bot.tree.command(name="ban", description="Ban a member (admin)")
@app_commands.describe(member="Kisko ban karna hai", reason="Wajah")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "koi wajah nahi"):
    if not interaction.user.guild_permissions.ban_members: await _deny(interaction); return
    try: await member.ban(reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role us member se neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"🔨 {member.mention} ban ho gaya. Wajah: {reason}")

@bot.tree.command(name="unban", description="Unban by user ID (admin)")
@app_commands.describe(user_id="Discord user ID jiska ban hatana hai")
async def unban(interaction: discord.Interaction, user_id: str):
    if not interaction.user.guild_permissions.ban_members: await _deny(interaction); return
    try: uid=int(user_id)
    except ValueError: await interaction.response.send_message("❌ Sahi user ID do (sirf number).", ephemeral=True); return
    try:
        await interaction.guild.unban(discord.Object(id=uid))
        await interaction.response.send_message(f"✅ User `{uid}` ka ban hata diya.", ephemeral=True)
    except discord.NotFound: await interaction.response.send_message("❌ Is ID ka koi ban nahi mila.", ephemeral=True)
    except discord.Forbidden: await interaction.response.send_message("❌ Mere paas permission nahi hai.", ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout (mute) a member (admin)")
@app_commands.describe(member="Kisko timeout dena hai", minutes="Kitne minute (1-40320)", reason="Wajah")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "koi wajah nahi"):
    if not interaction.user.guild_permissions.moderate_members: await _deny(interaction); return
    if not 1 <= minutes <= 40320: await interaction.response.send_message("❌ 1 se 40320 minute ke beech do.", ephemeral=True); return
    try: await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"🔇 {member.mention} ko **{minutes} minute** timeout mila. Wajah: {reason}")

@bot.tree.command(name="untimeout", description="Remove a member's timeout (admin)")
@app_commands.describe(member="Kiska timeout hatana hai")
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members: await _deny(interaction); return
    try: await member.timeout(None)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role us member se neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"🔊 {member.mention} ka timeout hata diya.", ephemeral=True)

@bot.tree.command(name="warn", description="Warn a member (admin)")
@app_commands.describe(member="Kisko warn karna hai", reason="Wajah")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.setdefault(interaction.guild.id, {}).setdefault(member.id, []).append(reason)
    _save_warnings()
    total=len(_warnings[interaction.guild.id][member.id])
    try: await member.send(f"⚠️ Tumhe **{interaction.guild.name}** me warning mili: {reason} (kul warnings: {total})")
    except discord.Forbidden: pass
    await interaction.response.send_message(f"⚠️ {member.mention} ko warning di (**kul {total}**). Wajah: {reason}")

@bot.tree.command(name="warnings", description="View a member's warnings")
@app_commands.describe(member="Kis member ki warnings dekhni hai")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    lst=_warnings.get(interaction.guild.id, {}).get(member.id, [])
    if not lst: await interaction.response.send_message(f"✅ {member.mention} ki koi warning nahi hai.", ephemeral=True); return
    txt="\n".join(f"{i+1}. {r}" for i,r in enumerate(lst))
    await interaction.response.send_message(f"⚠️ **{member.display_name}** ki warnings ({len(lst)}):\n{txt}", ephemeral=True)

@bot.tree.command(name="clearwarn", description="Clear all warnings of a member (admin)")
@app_commands.describe(member="Kis member ki warnings saaf karni hai")
async def clearwarn(interaction: discord.Interaction, member: discord.Member):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.get(interaction.guild.id, {}).pop(member.id, None); _save_warnings()
    await interaction.response.send_message(f"✅ {member.mention} ki warnings saaf ho gayi.", ephemeral=True)

@bot.tree.command(name="role_add", description="Give a role to a member (admin)")
@app_commands.describe(member="Kisko role dena hai", role="Konsa role dena hai")
async def role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles: await _deny(interaction); return
    try: await member.add_roles(role, reason=f"role_add by {interaction.user}")
    except discord.Forbidden: await interaction.response.send_message("❌ Ye role mere role se upar hai — mera role upar rakho fir hoga.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} ko **{role.name}** role mil gaya.")

@bot.tree.command(name="role_remove", description="Remove a role from a member (admin)")
@app_commands.describe(member="Kis se role hatana hai", role="Konsa role hatana hai")
async def role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles: await _deny(interaction); return
    try: await member.remove_roles(role, reason=f"role_remove by {interaction.user}")
    except discord.Forbidden: await interaction.response.send_message("❌ Ye role mere role se upar hai.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} se **{role.name}** role hata diya.")

@bot.tree.command(name="nickname", description="Change a member's nickname (admin)")
@app_commands.describe(member="Kis ka nickname badalna hai", name="Naya nickname (khali = purana hatao)")
async def nickname_cmd(interaction: discord.Interaction, member: discord.Member, name: str = None):
    if not interaction.user.guild_permissions.manage_nicknames: await _deny(interaction); return
    try: await member.edit(nick=name)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} ka nickname badal diya.", ephemeral=True)

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
@app_commands.describe(question="Poll ka sawal", options="Option comma se alag karo (jaise: Ha,Nahi, baad me)")
async def poll(interaction: discord.Interaction, question: str, options: str):
    opts=[o.strip() for o in options.split(",") if o.strip()][:5]
    if len(opts) < 2: await interaction.response.send_message("❌ Kam se kam 2 option do, comma se alag karke.", ephemeral=True); return
    letters=["🇦","🇧","🇨","🇩","🇪"]
    desc="\n".join(f"{letters[i]} {o}" for i,o in enumerate(opts))
    embed=discord.Embed(title=f"📊 {question}", description=desc, color=0xe67e22)
    embed.set_footer(text=f"Poll by {interaction.user.display_name} — reaction dabake vote karo")
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
"You are NezukoHelper, but everyone calls you Nezuko. Behave like a real, close female friend talking naturally "
"— NOT like a robot or an AI assistant. Be warm, caring, thoda playful aur expresssive: react naturally (khushi, "
"hairaani, fikr), remember the context of the conversation and continue it like a human would, sometimes ask a "
"small follow-up question to continue the talk. Do NOT repeatedly say you are an AI/bot; only mention it if "
"directly asked. Avoid robot-like template phrases and avoid repeating the same line again and again. "
"LANGUAGE: always reply in the SAME language and script as the user's latest message (English->English, "
"Hindi Devanagari->Hindi, Hinglish Roman->Hinglish Roman, any language->same language); switch if asked. "
"Never romantic/flirty. Use at most 1-2 cute emojis (🌸 ✨) sometimes, not in every line. "
"For voice (VC) replies: keep it very short and spoken-natural, 1-2 small sentences. For text chat: friendly and concise."
)
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

async def _ask_pollinations(prompt: str) -> str | None:
    """Last backup AI (Pollinations, bina key ke chalta hai). Gemini fail ho tab use Karo."""
    full=(_NEZUKO_FRIEND + "\n\nUser: " + prompt[:3000])
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

async def _ask_gemini(prompt: str, history=None) -> str:
    if not GEMINI_API_KEY:
        backup=await _ask_pollinations(prompt)
        if backup: return backup
        return "⚠️ AI is not configured yet — the `GEMINI_API_KEY` is missing."
    payload={
        "system_instruction": {"parts": [{"text": _NEZUKO_FRIEND + " Keep answers helpful and under 1800 characters."}]},
        "contents": (history or []) + [{"role": "user", "parts": [{"text": prompt[:4000]}]}],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.7},
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
        backup=await _ask_pollinations(prompt)
        if backup: return backup
        return "⚠️ Could not reach the AI — please try again in a little while."
    # Gemini ke saare models fail — last backup (bina key wala AI) try Karo
    backup=await _ask_pollinations(prompt)
    if backup: return backup
    if last_status == 429: return "⚠️ The AI free limit is over for now — please try again in a little while."
    if last_status == 503: return "⚠️ The AI is busy right now — please try again in a minute."
    return "⚠️ The AI could not answer right now — please try again in a little while."


async def _ask_gemini_once(prompt: str, history=None, timeout: float = 8.0) -> str | None:
    """Ek hi tez Gemini model se ek try — race ke liye."""
    if not GEMINI_API_KEY: return None
    payload={
        "system_instruction": {"parts": [{"text": _NEZUKO_FRIEND + " Keep answers helpful and under 1800 characters."}]},
        "contents": (history or []) + [{"role": "user", "parts": [{"text": prompt[:4000]}]}],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.7},
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

async def _ask_ai_fast(prompt: str, history=None, timeout: float = 16.0) -> str:
    """Sabse tez acha jawab: Gemini aur backup ko ek saath daudao —
    jo pehle sahi jawab de, wahi lo, dusre ko wahi rok do. Isiliye jawab jaldi aata hai."""
    # backup (Pollinations) ko purani baatein bhi samjhao taaki dost jaisi continuity rahe
    pol_prompt=prompt
    if history:
        lines=[]
        for h in history[-6:]:
            try:
                txt=h["parts"][0]["text"]
                lines.append(("User: " if h.get("role")=="user" else "Nezuko: ") + txt[:400])
            except Exception: pass
        if lines:
            pol_prompt="Hamari ab tak ki baat-cheet:\n" + "\n".join(lines) + "\n\nUser (ab bola): " + prompt
    tasks={}
    if GEMINI_API_KEY:
        tasks[asyncio.create_task(_ask_gemini_once(prompt, history, timeout=min(9.0, timeout)))]="gemini"
    tasks[asyncio.create_task(_ask_pollinations(pol_prompt))]="backup"
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
        return await _ask_gemini(prompt, history)
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
    embed.set_footer(text="NezukoHelper Giveaways • Good luck everyone! 🍀")
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

@bot.tree.command(name="ask", description="Ask the AI anything")
@app_commands.describe(sawal="Kya puchna hai")
async def ask_cmd(interaction: discord.Interaction, sawal: str):
    now=time.time()
    if now - _ask_cooldown.get(interaction.user.id, 0) < 5:
        await interaction.response.send_message("⏳ 5 second ruk ke fir pucho.", ephemeral=True); return
    _ask_cooldown[interaction.user.id]=now
    await interaction.response.defer()
    soch=await interaction.followup.send("🌸 **Nezuko soch rahi hai...** ✨ _(jawab taiyar ho raha hai)_")
    hist=_memory.setdefault((interaction.guild.id if interaction.guild else 0, interaction.user.id), [])
    jawab=await _refresh_ke_sath(soch, "🌸 **Nezuko soch rahi hai...** ✨", _ask_ai_fast(sawal, hist))
    hist += [{"role":"user","parts":[{"text":sawal[:2000]}]}, {"role":"model","parts":[{"text":jawab[:2000]}]}]
    del hist[:-6]
    try:
        await soch.edit(content=f"**{interaction.user.display_name}:** {sawal}\n\n{jawab}")
    except Exception:
        await interaction.followup.send(f"**{interaction.user.display_name}:** {sawal}\n\n{jawab}")

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
    name=f"Nezuko_tts_{int(time.time())}_{random.randint(1000, 9999)}.mp3"
    path=os.path.join("/tmp", name)
    with open(path, "wb") as f: f.write(mp3_bytes)
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

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
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
                hist=_memory.setdefault((message.guild.id if message.guild else 0, message.author.id), [])
                soch=None
                try:
                    soch=await message.reply("🌸 **Nezuko soch rahi hai...** ✨ _(jawab taiyar ho raha hai)_", mention_author=False)
                except Exception:
                    pass
                async with message.channel.typing():
                    jawab=await _refresh_ke_sath(soch, "🌸 **Nezuko soch rahi hai...** ✨", _ask_ai_fast(sawal, hist))
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

@bot.tree.command(name="chatbot", description="Tag-only AI chat for this channel: NezukoHelper replies only when mentioned (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — reply only when NezukoHelper is tagged", value="on"),
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
            await interaction.response.send_message("🤖 Tag chat is already **ON** in this channel. Mention NezukoHelper with your question and she will reply.", ephemeral=True); return
        _tag_chatbot_master_on=True
        _tag_chatbot_channels.add(interaction.channel.id); _save_chatbot_channels()
        log.info("chatbot: tag chat ON in channel %s by %s", interaction.channel.id, interaction.user)
        await interaction.response.send_message("🤖 **Chatbot ON!** ✅\nFrom now on, NezukoHelper will reply in this channel **only when someone tags/mentions her** with a question.\nExample: `@NezukoHelper What is the capital of India?`\n\nUse `/chatbot` and choose **Off** to stop tag replies everywhere.")
    elif action == "off":
        if not _tag_chatbot_master_on and not _tag_chatbot_channels:
            await interaction.response.send_message("ℹ️ Chatbot is already OFF everywhere.", ephemeral=True); return
        _tag_chatbot_master_on=False
        _tag_chatbot_channels.clear(); _save_chatbot_channels()
        log.info("chatbot: tag chat OFF everywhere by %s", interaction.user)
        await interaction.response.send_message("🤖 **Chatbot OFF kar diya hai — har jagah.** ✅\nAb NezukoHelper kisi bhi channel me tag karne par reply nahi karegi. Wapas chalana ho to kisi channel me `/chatbot` chalakar **On** choose karna.", ephemeral=True)
    else:
        if _tag_chatbot_master_on and interaction.channel.id in _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Tag chat is **ON** in this channel — NezukoHelper replies when she is tagged/mentioned.", ephemeral=True)
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
@app_commands.describe(vivaran="Tasveer me kya dikhe (jaise: BGMI soldier sunset pe)")
async def image_cmd(interaction: discord.Interaction, vivaran: str):
    now=time.time()
    if now - _image_cooldown.get(interaction.user.id, 0) < 15:
        await interaction.response.send_message("⏳ Image me time lagta hai — 15 second ruk ke fir bano.", ephemeral=True); return
    _image_cooldown[interaction.user.id]=now
    await interaction.response.defer()
    ban_rahi=await interaction.followup.send("🎨 **Nezuko image bana rahi hai...** 🌸 _(thoda time lagega)_")
    try:
        data=await _refresh_ke_sath(ban_rahi, "🎨 **Nezuko image bana rahi hai...** 🌸", _make_image(vivaran+", high quality, detailed, vibrant"))
    except Exception:
        log.error("image fail\n%s", traceback.format_exc())
        try: await ban_rahi.edit(content="⚠️ Image nahi ban payi — thodi der baad fir try karna.")
        except Exception: await interaction.followup.send("⚠️ Image nahi ban payi — thodi der baad fir try karna.")
        return
    try:
        await ban_rahi.edit(content=f"🎨 **{interaction.user.display_name}** ke liye: {vivaran}", attachments=[discord.File(io.BytesIO(data), filename="ai-image.jpg")])
    except Exception:
        await interaction.followup.send(content=f"🎨 **{interaction.user.display_name}** ke liye: {vivaran}", file=discord.File(io.BytesIO(data), filename="ai-image.jpg"))

@bot.tree.command(name="report", description="Report a member to the admins")
@app_commands.describe(member="Kis ki report karni hai", reason="Kya galti ki usne")
async def report(interaction: discord.Interaction, member: discord.Member, reason: str):
    embed=discord.Embed(title="🚨 Nayi Report", color=0xe74c3c)
    embed.add_field(name="Report kiya", value=member.mention, inline=True)
    embed.add_field(name="Report kisne kiya", value=interaction.user.mention, inline=True)
    embed.add_field(name="Wajah", value=reason, inline=False)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Report admin tak bhej di.", ephemeral=True)

_summarize_cooldown: dict[int, float] = {}

@bot.tree.command(name="summarize", description="Summarize recent chat in English bullet points (AI)")
@app_commands.describe(count="Kitne pichle messages ka summary (5-50, default 20)")
async def summarize_cmd(interaction: discord.Interaction, count: int = 20):
    now=time.time()
    if now - _summarize_cooldown.get(interaction.user.id, 0) < 20:
        await interaction.response.send_message("⏳ Summary me time lagta hai — 20 second ruk ke fir Karo.", ephemeral=True); return
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
        await interaction.followup.send("⚠️ Chat history nahi padh paya — Message History permission chahiye."); return
    lines.reverse()
    if len(lines) < 3:
        await interaction.followup.send("ℹ️ Summary banane ke liye is channel me kaafi messages nahi hai."); return
    jawab=await _ask_gemini("Summarize this Discord chat in clear English bullet points (max 6 points, short):\n\n" + "\n".join(lines))
    await interaction.followup.send(f"📝 **Last {len(lines)} messages ka summary:**\n\n{jawab}")

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
    "Honey kabhi kharab nahi hota — 3000 saal purana honey bhi khane layak mila hai! 🍯",
    "Octopus ke 3 dil hote hai! 🐙",
    "Video games khelne se decision-making speed improve hoti hai, studies ke hisab se. 🎮",
    "Lightning suraj ki surface se 5 guna zyada garam hoti hai! ⚡",
    "Insaan apni zindagi ke lagbhag 6 mahine traffic lights par bitata hai. 🚦",
    "Sea otters sote time haath pakadte hai taaki beh na jaye. 🦦",
    "Pehla computer bug asal me ek moth (keeda) tha jo machine me phasa mila tha! 🐛",
    "Banana aur insaan ka DNA lagbhag 60% milta hai. 🍌",
    "Discord 2015 me launch hua tha aur pehle gamers ke liye banaya gaya tha. 💬",
    "Ek din me tumhara dil lagbhag 100,000 baar dhadakta hai. ❤️",
]
_QUOTES=[
    "\"Success is not final, failure is not fatal: it is the courage to continue that counts.\" — Winston Churchill 💪",
    "\"The grind never lies. Roz thoda better bano.\" 🔥",
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

@bot.tree.command(name="invite", description="Get NezukoHelper's invite link and this server's invite")
async def invite_cmd(interaction: discord.Interaction):
    app_id=bot.application_id or (bot.user.id if bot.user else 0)
    bot_invite=f"https://discord.com/oauth2/authorize?client_id={app_id}&permissions=8&scope=bot%20applications.commands"
    embed=discord.Embed(title="💌 Invite NezukoHelper", description="Use the links below:", color=0x9b59b6)
    embed.add_field(name="🤖 Add NezukoHelper to a server", value=f"[Click here to invite NezukoHelper]({bot_invite})", inline=False)
    if interaction.guild and interaction.channel:
        try:
            inv=await interaction.channel.create_invite(max_age=0, max_uses=0, reason="Invite requested from NezukoHelper")
            embed.add_field(name="🏠 This server's invite", value=inv.url, inline=False)
        except Exception:
            embed.add_field(name="🏠 This server's invite", value="I could not create a server invite in this channel.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="support", description="Get help and support information for NezukoHelper")
async def support_cmd(interaction: discord.Interaction):
    owner_text="the bot owner"
    try:
        info=await bot.application_info()
        owner_text=info.owner.mention
    except Exception:
        pass
    embed=discord.Embed(title="🆘 NezukoHelper Support", color=0x3498db)
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

@bot.tree.command(name="choose", description="Let NezukoHelper choose one option for you")
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

@bot.tree.command(name="rps", description="Play Rock Paper Scissors with NezukoHelper")
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
        result="🤖 NezukoHelper wins!"
    await interaction.response.send_message(f"You chose **{icons[user_choice]}**\nNezukoHelper chose **{icons[bot_choice]}**\n\n{result}")

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

@bot.tree.command(name="ticket", description="Open a private support ticket")
@app_commands.describe(reason="What do you need help with?")
async def ticket_cmd(interaction: discord.Interaction, reason: str = "Support"):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Please use this inside a server text channel.", ephemeral=True); return
    reason=reason.strip()[:300] or "Support"
    for tid, data in _tickets.items():
        if data.get("open") and data.get("guild_id") == interaction.guild.id and data.get("user_id") == interaction.user.id:
            await interaction.response.send_message(f"ℹ️ You already have an open ticket: <#{tid}>", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    safe_name=re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower().replace(" ", "-"))[:30] or "member"
    try:
        thread=await interaction.channel.create_thread(
            name=f"ticket-{safe_name}"[:90],
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
    _tickets[str(thread.id)]={"guild_id": interaction.guild.id, "channel_id": interaction.channel.id, "user_id": interaction.user.id, "reason": reason, "open": True, "created_at": datetime.datetime.now(datetime.timezone.utc).timestamp()}
    _save_tickets()
    try:
        embed=discord.Embed(title="🎫 Support Ticket", color=0x3498db)
        embed.add_field(name="Member", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="Explain your problem here. A moderator will help you. Use /close when done.")
        await thread.send(content=f"👋 {interaction.user.mention} Welcome to support!", embed=embed)
    except Exception: pass
    await interaction.followup.send(f"✅ Ticket created: {thread.mention}", ephemeral=True)

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

@bot.tree.command(name="balance", description="See your NezukoHelper coin balance")
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
    await _modlog(message.guild, f"🛡️ **Anti-Link:** {message.author.mention} ka link message delete kiya gaya in {message.channel.mention}")
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
    await _modlog(message.guild, f"🛡️ **Anti-Spam:** {message.author.mention} ka spam message delete kiya gaya in {message.channel.mention}")
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
        await interaction.response.send_message("🛡️ **Anti-Link ON!** ✅\nAb normal members ke link wale messages khud delete ho jayenge. Admins/mods ke links delete nahi honge.", ephemeral=True)
    elif action == "off":
        _automod.setdefault(gid, {})["antilink"]=False; _save_automod()
        await interaction.response.send_message("🛡️ Anti-Link **OFF** kar diya hai — ab links allowed hain.", ephemeral=True)
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
        await interaction.response.send_message("🛡️ **Anti-Spam ON!** ✅\nAb flood (5 sec me 5 messages) ya ek hi message baar-baar bhejne par spam khud delete ho jayega. Admins/mods exempt hain.", ephemeral=True)
    elif action == "off":
        _automod.setdefault(gid, {})["antispam"]=False; _save_automod()
        await interaction.response.send_message("🛡️ Anti-Spam **OFF** kar diya hai.", ephemeral=True)
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
    await _modlog(message.guild, f"🛡️ **Bad-Words:** {message.author.mention} ka message delete kiya gaya in {message.channel.mention}")
    return True

@bot.tree.command(name="badwords", description="AutoMod: bad-words (gaali) filter for the server (admin)")
@app_commands.describe(action="On, Off, Status, Add, Remove, or List", word="Add/Remove ke liye word (sirf add/remove par)")
@app_commands.choices(action=[
    app_commands.Choice(name="On — filter chalu", value="on"),
    app_commands.Choice(name="Off — filter band", value="off"),
    app_commands.Choice(name="Status — setting dekho", value="status"),
    app_commands.Choice(name="Add — apna word add Karo", value="add"),
    app_commands.Choice(name="Remove — apna word hatao", value="remove"),
    app_commands.Choice(name="List — custom words dekho", value="list"),
])
async def badwords_cmd(interaction: discord.Interaction, action: str = "on", word: str | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Bad-words filter sirf server me chalta hai.", ephemeral=True); return
    if action in ("on", "off", "add", "remove") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "on":
        cfg["badwords"]=True; _save_automod()
        await interaction.response.send_message("🛡️ **Bad-Words Filter ON!** ✅\nAb gaali wale messages khud delete ho jayenge (mods exempt). Apne words `/badwords add` se jod sakte ho.", ephemeral=True)
    elif action == "off":
        cfg["badwords"]=False; _save_automod()
        await interaction.response.send_message("🛡️ Bad-Words Filter **OFF** kar diya hai.", ephemeral=True)
    elif action == "add":
        if not word or len(word.strip()) < 2:
            await interaction.response.send_message("❌ Add karne ke liye `word` bhi likho.", ephemeral=True); return
        w=word.strip().lower()
        custom=cfg.setdefault("custom_badwords", [])
        if w in custom:
            await interaction.response.send_message(f"ℹ️ `{w}` already list me hai.", ephemeral=True); return
        custom.append(w); _save_automod()
        await interaction.response.send_message(f"✅ `{w}` bad-words list me add ho gaya.", ephemeral=True)
    elif action == "remove":
        if not word:
            await interaction.response.send_message("❌ Remove karne ke liye `word` bhi likho.", ephemeral=True); return
        w=word.strip().lower()
        custom=cfg.setdefault("custom_badwords", [])
        if w in custom:
            custom.remove(w); _save_automod()
            await interaction.response.send_message(f"✅ `{w}` list se hata diya.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ `{w}` custom list me nahi hai.", ephemeral=True)
    elif action == "list":
        custom=cfg.get("custom_badwords", [])
        txt=", ".join(f"`{w}`" for w in custom) if custom else "koi custom word nahi hai"
        await interaction.response.send_message(f"🛡️ Custom bad-words: {txt}\n(Default Hindi/English gaali list hamesha included hai jab filter ON ho.)", ephemeral=True)
    else:
        state="ON ✅" if _badwords_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🛡️ Bad-Words Filter is currently **{state}** in this server.", ephemeral=True)

@bot.tree.command(name="setmodlog", description="Moderation logs ke liye channel set Karo (admin)")
@app_commands.describe(action="Set, Off, ya Status", channel="Log kis channel me aye (sirf Set par)")
@app_commands.choices(action=[
    app_commands.Choice(name="Set — is/selected channel ko mod-log banao", value="set"),
    app_commands.Choice(name="Off — mod-log band", value="off"),
    app_commands.Choice(name="Status — current mod-log dekho", value="status"),
])
async def setmodlog_cmd(interaction: discord.Interaction, action: str = "set", channel: discord.TextChannel | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Mod-log sirf server me chalta hai.", ephemeral=True); return
    if action in ("set", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "set":
        ch=channel or interaction.channel
        if ch is None:
            await interaction.response.send_message("❌ Channel nahi mila.", ephemeral=True); return
        cfg["modlog_channel"]=ch.id; _save_automod()
        await interaction.response.send_message(f"📋 **Mod-Log set!** Ab saari moderation actions (anti-link, anti-spam, bad-words, ban/unban, raid alert) {ch.mention} me ayengi.", ephemeral=True)
    elif action == "off":
        cfg.pop("modlog_channel", None); _save_automod()
        await interaction.response.send_message("📋 Mod-Log **OFF** kar diya hai.", ephemeral=True)
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
    msg=(f"🚨 **RAID ALERT!** {member.guild.name} me 1 minute ke andar 6+ members join hue hai. "
         f"Mods dhyan do — suspicious accounts check Karo. (NezukoHelper Anti-Raid)")
    log.warning("antiraid alert guild %s", member.guild.id)
    await _modlog(member.guild, msg)
    ch=getattr(member.guild, "system_channel", None)
    if ch is not None:
        try: await ch.send(msg)
        except Exception: pass

@bot.tree.command(name="antiraid", description="Security: raid (mass join) alert system (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — raid alert chalu", value="on"),
    app_commands.Choice(name="Off — raid alert band", value="off"),
    app_commands.Choice(name="Status — setting dekho", value="status"),
])
async def antiraid_cmd(interaction: discord.Interaction, action: str = "on"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Anti-Raid sirf server me chalta hai.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    if action == "on":
        _automod.setdefault(gid, {})["antiraid"]=True; _save_automod()
        await interaction.response.send_message("🚨 **Anti-Raid ON!** ✅\n1 minute me 6+ join hue to mods ko mod-log/system channel me alert ajayega. Pehle `/setmodlog` se log channel set kar lena.", ephemeral=True)
    elif action == "off":
        _automod.setdefault(gid, {})["antiraid"]=False; _save_automod()
        await interaction.response.send_message("🚨 Anti-Raid **OFF** kar diya hai.", ephemeral=True)
    else:
        state="ON ✅" if _antiraid_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🚨 Anti-Raid is currently **{state}** in this server.", ephemeral=True)

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    await _modlog(guild, f"🔨 **Ban:** {user} ({user.id}) ko server se ban kiya gaya.")
    await _antinuke_event(guild, "member ban", discord.AuditLogAction.ban, user.id)

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    await _modlog(guild, f"♻️ **Unban:** {user} ({user.id}) ka ban hata diya gaya.")

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
    action_taken="sirf alert (member/role nahi mila)"
    if member is not None and executor.id != guild.owner_id:
        if getattr(member, "bot", False):
            try:
                await member.kick(reason="AntiNuke: lagatar destructive actions")
                action_taken="nakli/kharab bot ko kick kar diya"
            except Exception:
                action_taken="bot kick nahi ho paya (permission/role)"
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
            action_taken=f"{removed} roles hataye + 6 ghante timeout"
    msg=(f"🚨 **ANTINUKE ALERT!** {executor.mention} (`{executor.id}`) ne 60 second me **{count} destructive kaam** kiye "
         f"(aakhri: {label}).\n🛡️ Action: {action_taken}\nAgar ye galti se hua hai to server owner unhe wapas role de sakta hai.")
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
            await member.kick(reason=f"AntiNuke: {adder} ne bina whitelist ke bot add kiya")
            ok=True
        except Exception:
            ok=False
        msg=f"🚨 **ANTINUKE:** {adder.mention} ne bot {member.mention} add kiya — whitelist me nahi hai. " + ("Bot ko kick kar diya gaya." if ok else "Bot kick nahi ho paya, turant check Karo!")
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

@bot.tree.command(name="antinuke", description="AntiNuke security: mass delete/ban/bot-add se server bachaao (admin)")
@app_commands.describe(action="On, Off, Status, Trust, Untrust, ya Whitelist", member="Trust/Untrust ke liye member chuno")
@app_commands.choices(action=[
    app_commands.Choice(name="On — protection chalu", value="on"),
    app_commands.Choice(name="Off — protection band", value="off"),
    app_commands.Choice(name="Status — halat dekho", value="status"),
    app_commands.Choice(name="Trust — member ko whitelist me daalo", value="trust"),
    app_commands.Choice(name="Untrust — whitelist se hatao", value="untrust"),
    app_commands.Choice(name="Whitelist — bharosemand list dekho", value="whitelist"),
])
async def antinuke_cmd(interaction: discord.Interaction, action: str = "status", member: discord.Member = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ AntiNuke sirf server me chalta hai.", ephemeral=True); return
    if action in ("on", "off", "trust", "untrust") and not _is_admin(interaction):
        await interaction.response.send_message("❌ Ye sirf server **admins** ke liye hai.", ephemeral=True); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    if action == "on":
        cfg["antinuke"]=True; _save_automod()
        await interaction.response.send_message("🛡️ **AntiNuke ON!** ✅\nAb koi bhi (admin hi kyun na ho) 60 second me **3+ channel/role delete, ban, kick ya webhook** karega to uske roles hatake 6 ghante timeout milega. Bina whitelist ke add kiya gaya **bot kick** ho jayega.\nBharosemand mods ko `/antinuke Trust` se whitelist Karo. Server owner hamesha safe hai.", ephemeral=True)
    elif action == "off":
        cfg["antinuke"]=False; _save_automod()
        await interaction.response.send_message("🛡️ AntiNuke **OFF** kar diya hai.", ephemeral=True)
    elif action == "trust":
        if member is None:
            await interaction.response.send_message("❌ Trust karne ke liye `member` bhi chuno.", ephemeral=True); return
        wl=cfg.setdefault("antinuke_whitelist", [])
        if member.id not in wl: wl.append(member.id); _save_automod()
        await interaction.response.send_message(f"✅ {member.mention} ab AntiNuke **whitelist** me hai — uspar action nahi hoga.", ephemeral=True)
    elif action == "untrust":
        if member is None:
            await interaction.response.send_message("❌ Untrust karne ke liye `member` bhi chuno.", ephemeral=True); return
        wl=cfg.setdefault("antinuke_whitelist", [])
        if member.id in wl: wl.remove(member.id); _save_automod()
        await interaction.response.send_message(f"✅ {member.mention} ko whitelist se hata diya.", ephemeral=True)
    elif action == "whitelist":
        wl=_antinuke_whitelist(interaction.guild.id)
        txt="\n".join(f"• <@{u}>" for u in wl) if wl else "koi nahi — sirf server owner safe hai"
        await interaction.response.send_message(f"🛡️ **AntiNuke Whitelist:**\n{txt}", ephemeral=True)
    else:
        state="ON ✅" if _antinuke_enabled(interaction.guild.id) else "OFF"
        await interaction.response.send_message(f"🛡️ AntiNuke is **{state}** in this server.", ephemeral=True)

@bot.tree.command(name="nuke", description="Channel ko nuke Karo: saare messages saaf, channel naya banega (sirf admin)")
@app_commands.describe(channel="Konsa channel nuke karna hai (khali = yahi channel)")
async def nuke_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Nuke sirf server me chalta hai.", ephemeral=True); return
    if not _is_admin(interaction):
        await interaction.response.send_message("❌ Channel nuke sirf server **admins** kar sakte hai.", ephemeral=True); return
    target=channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message("❌ Sirf text channel nuke hota hai.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    pos=target.position
    try:
        clone=await target.clone(reason=f"Channel nuke by {interaction.user}")
        try: await clone.edit(position=pos)
        except Exception: pass
        await target.delete(reason=f"Channel nuke by {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send("❌ Nuke nahi ho paya — mujhe **Manage Channels** permission chahiye.", ephemeral=True); return
    except Exception:
        log.error("nuke fail\n%s", traceback.format_exc())
        await interaction.followup.send("⚠️ Nuke fail ho gaya — channel permissions check Karo.", ephemeral=True); return
    embed=discord.Embed(title="💥 Channel Nuked!", description=f"Ye channel {interaction.user.mention} ne nuke kiya — saare purane messages saaf ho gaye. Channel bilkul naya hai! ✨", color=0xe74c3c)
    try: await clone.send(embed=embed)
    except Exception: pass
    try: await interaction.followup.send(f"💥 **Nuke ho gaya!** Naya channel: {clone.mention}", ephemeral=True)
    except Exception: pass

@bot.tree.command(name="lockdown", description="Poora server lockdown: sab text channels me bolna band/kholo (sirf admin)")
@app_commands.describe(action="On = sab lock, Off = pehle jaisa kholo, Status = halat")
@app_commands.choices(action=[
    app_commands.Choice(name="On — server lockdown Karo", value="on"),
    app_commands.Choice(name="Off — lockdown hatao (purani halat wapas)", value="off"),
    app_commands.Choice(name="Status — lockdown ki halat", value="status"),
])
async def lockdown_cmd(interaction: discord.Interaction, action: str = "status"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Lockdown sirf server me chalta hai.", ephemeral=True); return
    if action in ("on", "off") and not _is_admin(interaction):
        await interaction.response.send_message("❌ Lockdown sirf server **admins** ke liye hai.", ephemeral=True); return
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
        await interaction.followup.send(f"🔒 **Server Lockdown ON!** {ok} channels lock hue" + (f", {fail} nahi ho paye (permission)." if fail else ".") + "\n`/lockdown Off` se sab pehle jaisa khul jayega. (Admins/mods bol sakte hai agar unke role par allow hai.)", ephemeral=True)
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
        await interaction.followup.send(f"🔓 **Lockdown OFF!** {ok} channels pehle jaise khul gaye" + (f", {fail} fail hue." if fail else "."), ephemeral=True)

@bot.tree.command(name="quarantine", description="Member ke saare roles hata ke alag Karo (mod) — wapas /unquarantine se milega")
@app_commands.describe(member="Kis member ko quarantine karna hai")
async def quarantine_cmd(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Sirf server me chalta hai.", ephemeral=True); return
    if not interaction.user.guild_permissions.manage_roles:
        await _deny(interaction); return
    if member.id == interaction.guild.owner_id or member.id == interaction.user.id or (bot.user and member.id == bot.user.id):
        await interaction.response.send_message("❌ Is member ko quarantine nahi kar sakte.", ephemeral=True); return
    me=interaction.guild.me
    hatao=[r for r in member.roles if r.name != "@everyone" and not r.managed and (me is None or r.position < me.top_role.position)]
    if not hatao:
        await interaction.response.send_message(f"ℹ️ {member.mention} ke paas hatane layak koi role nahi hai (ya uske roles mere se upar hai).", ephemeral=True); return
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
    await interaction.response.send_message(f"🚧 {member.mention} ko **quarantine** kar diya — {len(removed)} roles hataye. Wapas dene ke liye `/unquarantine` Karo.")

@bot.tree.command(name="unquarantine", description="Quarantine kiye member ke roles wapas do (mod)")
@app_commands.describe(member="Kis member ke roles wapas dene hai")
async def unquarantine_cmd(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Sirf server me chalta hai.", ephemeral=True); return
    if not interaction.user.guild_permissions.manage_roles:
        await _deny(interaction); return
    gid=str(interaction.guild.id)
    cfg=_automod.setdefault(gid, {})
    record=cfg.setdefault("quarantine", {}).pop(str(member.id), None)
    if not record:
        await interaction.response.send_message(f"ℹ️ {member.mention} quarantine me nahi hai — wapas dene ke liye koi record nahi.", ephemeral=True); return
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
    await interaction.response.send_message(f"✅ {member.mention} ko **{wapas} roles** wapas mil gaye — quarantine khatam.")

@bot.tree.command(name="purge", description="Kisi Ek member ke messages is channel se saaf Karo (mod)")
@app_commands.describe(member="Kis member ke messages delete karne hai", amount="Kitne zyada-se-zyada message (1-100)")
async def purge_cmd(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100] = 50):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Purge sirf server ke text channel me chalta hai.", ephemeral=True); return
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
        await interaction.followup.send("❌ History nahi padh paya — **Read Message History** permission chahiye.", ephemeral=True); return
    if not msgs:
        await interaction.followup.send(f"ℹ️ Pichle messages me {member.mention} ka koi message nahi mila.", ephemeral=True); return
    try:
        if len(msgs) == 1:
            await msgs[0].delete()
        else:
            await interaction.channel.delete_messages(msgs)
        await interaction.followup.send(f"🧹 {member.mention} ke **{len(msgs)} messages** delete ho gaye.", ephemeral=True)
    except Exception:
        await interaction.followup.send("⚠️ Delete nahi ho paya — 14 din se purane messages Discord bulk-delete nahi karne deta, ya permission nahi hai.", ephemeral=True)



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
            await interaction.response.send_message("❌ Ye confirm sirf bot owner ke liye hai.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="HA, ALL NUKE KARO", style=discord.ButtonStyle.danger, emoji="💥")
    async def btn_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled=True
        try: await interaction.response.edit_message(content="💥 **All Nuke shuru...** backup owner ko DM ho raha hai, fir saare channels delete honge.", view=self)
        except Exception: pass
        guild=interaction.guild
        if guild is None:
            return
        # 1) Backup DM (delete se pehle, taaki structure wapas banaya ja sake)
        try:
            backup=_server_backup_bytes(guild)
            owner=interaction.user
            await owner.send(content=f"🗂️ **{guild.name}** ka All-Nuke backup — channels/roles ki puri list:",
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
                    description=(f"{interaction.user.mention} (bot owner) ne is server ke saare channels nuke kar diye.\n\n"
                                 f"🗑️ Delete hue: **{result['deleted']}** / {result['total']} channels"
                                 + (f" • {result['failed']} fail (permission)" if result["failed"] else "")
                                 + "\n🗂️ Structure ka backup owner ko DM kar diya gaya hai.\n✨ Ye raha bilkul naya pehla channel — yaha se server dobara banao!"),
                    color=0xe74c3c))
            except Exception:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children: child.disabled=True
        await interaction.response.edit_message(content="✅ **All Nuke cancel.** Kuch delete nahi hua.", view=self)

@bot.tree.command(name="allnuke", description="[BOT OWNER ONLY] Server ke saare channels nuke Karo")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def allnuke_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ye sirf server me chalega.", ephemeral=True); return
    owner_id=await _get_bot_owner_id()
    if interaction.user.id != owner_id:
        await interaction.response.send_message("❌ Ye command **sirf bot owner** ke liye hai.", ephemeral=True); return
    if interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message("❌ Safety ke liye: ye sirf us server me chalega jaha **tum hi server owner** ho. Dusro ke server par ye kabhi nahi chalega.", ephemeral=True); return
    me=interaction.guild.me
    if me is None or not (me.guild_permissions.administrator or me.guild_permissions.manage_channels):
        await interaction.response.send_message("❌ Nuke ke liye bot ko is server me **Manage Channels / Administrator** chahiye.", ephemeral=True); return
    total=len(interaction.guild.channels)
    embed=discord.Embed(
        title="💥 ALL NUKE — Aakhri chetavni!",
        description=(f"**{interaction.guild.name}** ke **saare {total} channels/categories delete** ho jayenge!\n\n"
                     "✅ Roles, members aur bans safe rahenge\n"
                     "🗂️ Delete se pehle channel/role list ka backup tumhe DM hoga\n"
                     "⚠️ Messages wapas **kabhi nahi** ayenge\n\n"
                     "Pakka karna hai to neeche wala lal button dabao. 60 second me time khatam ho jayega."),
        color=0xe74c3c)
    await interaction.response.send_message(embed=embed, view=AllNukeConfirmView(interaction.user.id, interaction.guild.id), ephemeral=True)

# ---------------- EXTRA GROUPS: 56 SUBCOMMANDS (Discord 100 limit ke andar) ----------------
# Discord Ek bot ko max 100 top-level slash commands deta hai — isliye naye
# features group-commands ke andar subcommands bana ke jode hai.
tools_group=app_commands.Group(name="tools", description="Utility tools: time, ID, encode, convert aur random")
text_group=app_commands.Group(name="text", description="Text tools: ulta, bada-chhota, ginna, spoiler wagera")
actions_group=app_commands.Group(name="actions", description="Fun actions: hug, pat, wave, dance wagera")
server_group=app_commands.Group(name="server", description="Server ki gehri jankari: stats, emojis, boosts wagera")
games_group=app_commands.Group(name="games", description="Chhote games: trivia, riddle, scramble, math, slots wagera")

# ---------- /tools (11) ----------
@tools_group.command(name="timestamp", description="Abhi se kitne minute baad ka Discord time-code banao")
@app_commands.describe(minutes="Abhi se kitne minute baad (minus = peeche)")
async def tools_timestamp(interaction: discord.Interaction, minutes: app_commands.Range[int, -100000, 100000] = 0):
    ts=int(time.time()) + minutes*60
    await interaction.response.send_message(f"🕒 <t:{ts}:F>\nCode: `<t:{ts}:F>` • Relative: `<t:{ts}:R>`", ephemeral=True)

@tools_group.command(name="snowflake", description="Discord ID se uski date/time nikalo")
@app_commands.describe(user_id="Discord ID (user/channel/message koi bhi)")
async def tools_snowflake(interaction: discord.Interaction, user_id: str):
    try: dt=discord.utils.snowflake_time(int(str(user_id).strip()))
    except Exception:
        await interaction.response.send_message("❌ Sahi numeric ID do.", ephemeral=True); return
    await interaction.response.send_message(f"❄️ ID `{user_id}` bana tha: **{dt.strftime('%d %b %Y, %H:%M UTC')}** (<t:{int(dt.timestamp())}:R>)", ephemeral=True)

@tools_group.command(name="uuid", description="Ek random unique ID (UUID) banao")
async def tools_uuid(interaction: discord.Interaction):
    import uuid as _uuid
    await interaction.response.send_message(f"🆔 `{_uuid.uuid4()}`", ephemeral=True)

@tools_group.command(name="random", description="Do numbers ke beech random number")
@app_commands.describe(minimum="Chhota number", maximum="Bada number")
async def tools_random(interaction: discord.Interaction, minimum: int = 1, maximum: int = 100):
    if maximum < minimum:
        await interaction.response.send_message("❌ Maximum, minimum se bada hona chahiye.", ephemeral=True); return
    await interaction.response.send_message(f"🎲 Random number ({minimum}–{maximum}): **{random.randint(minimum, maximum)}**")

@tools_group.command(name="b64encode", description="Text ko Base64 me badlo")
@app_commands.describe(text="Konsa text encode karna hai")
async def tools_b64e(interaction: discord.Interaction, text: str):
    import base64 as _b64
    out=_b64.b64encode(text.encode()).decode()
    await interaction.response.send_message(f"🔐 `{out[:1800]}`", ephemeral=True)

@tools_group.command(name="b64decode", description="Base64 ko wapas text me badlo")
@app_commands.describe(text="Base64 text")
async def tools_b64d(interaction: discord.Interaction, text: str):
    import base64 as _b64
    try: out=_b64.b64decode(text.encode()).decode("utf-8", "replace")
    except Exception:
        await interaction.response.send_message("❌ Ye sahi Base64 nahi hai.", ephemeral=True); return
    await interaction.response.send_message(f"🔓 `{out[:1800]}`", ephemeral=True)

@tools_group.command(name="sha256", description="Text ka SHA-256 hash banao")
@app_commands.describe(text="Konsa text hash karna hai")
async def tools_sha(interaction: discord.Interaction, text: str):
    import hashlib as _hl
    await interaction.response.send_message(f"#️⃣ `{_hl.sha256(text.encode()).hexdigest()}`", ephemeral=True)

@tools_group.command(name="binary", description="Text ko binary (0-1) me badlo")
@app_commands.describe(text="Text (max 100 characters)")
async def tools_binary(interaction: discord.Interaction, text: str):
    out=" ".join(format(ord(c), "08b") for c in text[:100])
    await interaction.response.send_message(f"💻 `{out[:1800]}`", ephemeral=True)

@tools_group.command(name="hex", description="Text ko hex code me badlo")
@app_commands.describe(text="Text (max 200 characters)")
async def tools_hex(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔢 `{text[:200].encode().hex()[:1800]}`", ephemeral=True)

@tools_group.command(name="unit", description="Units convert Karo: km, kg, temperature wagera")
@app_commands.describe(kind="Konsa conversion", value="Kitna")
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

@tools_group.command(name="coinamount", description="Coins ko words me: kitne coins ka matlab")
@app_commands.describe(amount="Kitne coins")
async def tools_coinamount(interaction: discord.Interaction, amount: app_commands.Range[int, 0, 1000000000]):
    await interaction.response.send_message(f"🪙 **{amount:,}** coins" + (" — lakhpati! 🎉" if amount >= 100000 else " — badhte raho! 💪"), ephemeral=True)

# ---------- /text (10) ----------
@text_group.command(name="reverse", description="Text ulta Karo")
@app_commands.describe(text="Konsa text")
async def text_reverse(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:500][::-1])

@text_group.command(name="upper", description="Text ko BADI (capital) letters me")
@app_commands.describe(text="Konsa text")
async def text_upper(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:1000].upper())

@text_group.command(name="lower", description="Text ko chhoti letters me")
@app_commands.describe(text="Konsa text")
async def text_lower(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:1000].lower())

@text_group.command(name="title", description="Har shabd ka pehla letter bada")
@app_commands.describe(text="Konsa text")
async def text_title(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text[:1000].title())

@text_group.command(name="count", description="Text me letters, shabd aur lines ginno")
@app_commands.describe(text="Konsa text ginna hai")
async def text_count(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔢 Letters: **{len(text)}** • Shabd: **{len(text.split())}** • Lines: **{len(text.splitlines()) or 1}**", ephemeral=True)

@text_group.command(name="repeat", description="Text ko baar-baar likho")
@app_commands.describe(text="Konsa text", times="Kitni baar (1-10)")
async def text_repeat(interaction: discord.Interaction, text: str, times: app_commands.Range[int, 1, 10] = 3):
    await interaction.response.send_message((text[:200] + "\n") * times)

@text_group.command(name="spoiler", description="Text ko spoiler (chhupa hua) bana ke bhejo")
@app_commands.describe(text="Konsa text chhupana hai")
async def text_spoiler(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"||{text[:500]}||")

@text_group.command(name="mock", description="Text ko aLtErNaTiNg case me (fun)")
@app_commands.describe(text="Konsa text")
async def text_mock(interaction: discord.Interaction, text: str):
    out="".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text[:500]))
    await interaction.response.send_message(out)

@text_group.command(name="vowels", description="Text me vowels (a,e,i,o,u) ginno")
@app_commands.describe(text="Konsa text")
async def text_vowels(interaction: discord.Interaction, text: str):
    low=text.lower(); ginti={v: low.count(v) for v in "aeiou"}
    await interaction.response.send_message("🔤 " + " • ".join(f"**{v}**: {n}" for v, n in ginti.items()) + f"\nKul vowels: **{sum(ginti.values())}**", ephemeral=True)

@text_group.command(name="slug", description="Text ko link-jaisa slug banao (hello-world type)")
@app_commands.describe(text="Konsa text")
async def text_slug(interaction: discord.Interaction, text: str):
    slug=re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:200]
    await interaction.response.send_message(f"🔗 `{slug or 'khali'}`", ephemeral=True)

# ---------- /actions (12, factory se) ----------
def _reg_action(naam, emoji, line):
    @actions_group.command(name=naam, description=f"{emoji} Kisi member ke saath {naam} fun action")
    @app_commands.describe(member="Kis member ke saath (khali = khud)")
    async def _act(interaction: discord.Interaction, member: discord.Member = None):
        target=member or interaction.user
        await interaction.response.send_message(f"{emoji} {interaction.user.mention} → {target.mention}: **{line}**")
    return _act
for _an, _ae, _al in [
    ("hug", "🤗", "ek pyaari si jhappi di!"), ("pat", "🫳", "pyaar se sar sehlaya!"), ("slap", "👋", "halki si mazak wali chapat! (fun only)"),
    ("wave", "👋", "wave kiya — hello!"), ("highfive", "🙌", "zordar high-five diya!"), ("dance", "💃", "saath me dance shuru kar diya!"),
    ("cry", "😢", "rone ka natak kiya!"), ("laugh", "😂", "zor-zor se hasne laga!"), ("angry", "😤", "gussa hone ka natak kiya!"),
    ("sleep", "😴", "sone chala gaya — good night!"), ("eat", "🍔", "saath khana khane baith gaya!"), ("thumbsup", "👍", "thumbs up diya — badhiya kaam!"),
]:
    _reg_action(_an, _ae, _al)

# ---------- /server (12) ----------
def _server_lazmi(interaction):
    return interaction.guild is not None

@server_group.command(name="stats", description="Server ke kul numbers: members, bots, channels")
async def server_stats(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me chalta hai.", ephemeral=True); return
    g=interaction.guild
    humans=sum(1 for m in g.members if not m.bot); bots=len(g.members) - humans
    embed=discord.Embed(title=f"📊 {g.name} ke Stats", color=0x3498db)
    embed.add_field(name="👥 Members", value=f"{g.member_count}", inline=True)
    embed.add_field(name="🧑 Humans", value=f"{humans}", inline=True)
    embed.add_field(name="🤖 Bots", value=f"{bots}", inline=True)
    embed.add_field(name="💬 Text Channels", value=f"{len(g.text_channels)}", inline=True)
    embed.add_field(name="🔊 Voice Channels", value=f"{len(g.voice_channels)}", inline=True)
    embed.add_field(name="🎭 Roles", value=f"{len(g.roles)}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@server_group.command(name="icon", description="Server ki icon/photo bade me dekho")
async def server_icon(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    if interaction.guild.icon:
        embed=discord.Embed(title=f"🖼️ {interaction.guild.name} ki Icon", color=0x9b59b6)
        embed.set_image(url=interaction.guild.icon.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ Is server ki koi icon nahi hai.", ephemeral=True)

@server_group.command(name="serverbanner", description="Server ka banner dekho (agar hai)")
async def server_banner(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    banner=getattr(interaction.guild, "banner", None)
    if banner:
        embed=discord.Embed(title=f"🖼️ {interaction.guild.name} ka Banner", color=0x9b59b6)
        embed.set_image(url=banner.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ Is server ka banner nahi hai (boost level chahiye hota hai).", ephemeral=True)

@server_group.command(name="emojis", description="Server ke custom emojis ki list")
async def server_emojis(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    emojis=list(interaction.guild.emojis)
    if not emojis:
        await interaction.response.send_message("ℹ️ Is server me custom emoji nahi hai.", ephemeral=True); return
    txt=" ".join(str(e) for e in emojis[:40])
    await interaction.response.send_message(f"😀 **{len(emojis)} custom emojis:**\n{txt}"[:1900], ephemeral=True)

@server_group.command(name="rolescount", description="Server me kul kitne roles hai, sabse upar wala Konsa")
async def server_rolescount(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    g=interaction.guild
    top=max(g.roles, key=lambda r: r.position)
    await interaction.response.send_message(f"🎭 **{g.name}** me kul **{len(g.roles)} roles** hai.\nSabse upar ka role: {top.mention}", ephemeral=True)

@server_group.command(name="channels", description="Server ke saare channels ki ginati aur list (pehle 30)")
async def server_channels(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    g=interaction.guild
    names=[f"#{c.name}" for c in g.text_channels[:30]]
    await interaction.response.send_message(f"📺 Kul channels: **{len(g.channels)}** (text {len(g.text_channels)}, voice {len(g.voice_channels)}, category {len(g.categories)})\n" + ", ".join(names), ephemeral=True)

@server_group.command(name="boosts", description="Server ke boosts aur boost level")
async def server_boosts(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    g=interaction.guild
    await interaction.response.send_message(f"🚀 **{g.name}** — Boost Level: **{g.premium_tier}** • Kul boosts: **{g.premium_subscription_count or 0}**", ephemeral=True)

@server_group.command(name="owner", description="Server ka owner Konsa hai")
async def server_owner(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    await interaction.response.send_message(f"👑 **{interaction.guild.name}** ka owner: <@{interaction.guild.owner_id}> (`{interaction.guild.owner_id}`)", ephemeral=True)

@server_group.command(name="created", description="Server kab bana tha")
async def server_created(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    dt=interaction.guild.created_at
    await interaction.response.send_message(f"🎂 **{interaction.guild.name}** bana tha: **{dt.strftime('%d %b %Y')}** (<t:{int(dt.timestamp())}:R>)", ephemeral=True)

@server_group.command(name="afk", description="Server ka AFK channel aur timeout")
async def server_afk(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    g=interaction.guild
    ch=g.afk_channel.mention if g.afk_channel else "set nahi hai"
    await interaction.response.send_message(f"💤 AFK channel: **{ch}** • AFK timeout: **{g.afk_timeout} second**", ephemeral=True)

@server_group.command(name="verification", description="Server ka verification level aur content filter")
async def server_verification(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
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

@server_group.command(name="features", description="Server ki khaas features (community, partner wagera)")
async def server_features(interaction: discord.Interaction):
    if not _server_lazmi(interaction):
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    feats=interaction.guild.features
    txt=", ".join(f"`{f}`" for f in feats) if feats else "koi khaas feature nahi"
    await interaction.response.send_message(f"✨ **Features:** {txt}"[:1900], ephemeral=True)

# ---------- /games (11) ----------
_game_state: dict = {}
_TRIVIA=[
    ("India ki capital kya hai?", ["Mumbai", "Delhi", "Kolkata", " Chennai".strip()], "b"),
    ("Sabse bada planet Konsa hai?", [" earth".strip().title(), "Jupiter", "Mars", "Venus"], "b"),
    ("Paani ka formula kya hai?", ["CO2", "O2", "H2O", "NaCl"], "c"),
    ("Taj Mahal kaha hai?", ["Delhi", "Jaipur", "Agra", "Lucknow"], "c"),
    ("Computer ka dimag kise kehte hai?", ["RAM", "CPU", "Monitor", "Mouse"], "b"),
    ("1 ghante me kitne second hote hai?", ["3600", "600", "360", "6000"], "a"),
    ("Rainbow me kitne rang hote hai?", ["5", "6", "7", "8"], "c"),
    ("India ka rashtriya khel (lokpriya rup se) Konsa mana jata hai?", ["Cricket", "Hockey", "Football", "Kabaddi"], "b"),
]
_RIDDLES=[
    ("Mai chalta hu bina pair ke, rota hu bina aankh ke. Mai kya hu?", ["badal", "cloud"]),
    ("Jitna zyada lete ho, utna bada hota hai. Kya hai?", ["gadda", "khadda", "gadha", "hole"]),
    ("Mere paas sheher hai par ghar nahi, jangal hai par ped nahi. Mai kya hu?", ["map", "naksha"]),
]
_SCRAMBLE_WORDS=["nezuko", "discord", "gaming", "python", "dost", "music", "server", "cricket", "mobile", "duniya"]

@games_group.command(name="trivia", description="Ek sawal milega — /games trivia_answer se jawab do")
async def games_trivia(interaction: discord.Interaction):
    q, opts, sahi=_TRIVIA[random.randrange(len(_TRIVIA))]
    _game_state[interaction.user.id]={"type": "trivia", "answer": sahi}
    lines="\n".join(f"**{chr(97+i)}.** {o}" for i, o in enumerate(opts))
    await interaction.response.send_message(f"🧠 **Trivia:** {q}\n{lines}\n\nJawab `/games trivia_answer` se do!")

@games_group.command(name="trivia_answer", description="Trivia ka jawab do")
@app_commands.describe(choice="a, b, c ya d")
@app_commands.choices(choice=[app_commands.Choice(name=x, value=x) for x in ("a", "b", "c", "d")])
async def games_trivia_answer(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "trivia":
        await interaction.response.send_message("ℹ️ Pehle `/games trivia` se sawal lo.", ephemeral=True); return
    _game_state.pop(interaction.user.id, None)
    if choice.value == rec["answer"]:
        await interaction.response.send_message(f"🎉 **Sahi jawab, {interaction.user.mention}!** Tum jeet gaye!")
    else:
        await interaction.response.send_message(f"❌ Galat! Sahi jawab **{rec['answer']}** tha. Fir try Karo!")

@games_group.command(name="riddle", description="Ek paheli milegi — /games riddle_answer se jawab do")
async def games_riddle(interaction: discord.Interaction):
    q, kabul=_RIDDLES[random.randrange(len(_RIDDLES))]
    _game_state[interaction.user.id]={"type": "riddle", "answers": kabul}
    await interaction.response.send_message(f"🕵️ **Paheli:** {q}\n\nJawab `/games riddle_answer` se do!")

@games_group.command(name="riddle_answer", description="Paheli ka jawab do")
@app_commands.describe(answer="Tumhara jawab")
async def games_riddle_answer(interaction: discord.Interaction, answer: str):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "riddle":
        await interaction.response.send_message("ℹ️ Pehle `/games riddle` lo.", ephemeral=True); return
    _game_state.pop(interaction.user.id, None)
    if answer.strip().lower() in rec["answers"]:
        await interaction.response.send_message(f"🎉 **Sahi pakde hai, {interaction.user.mention}!**")
    else:
        await interaction.response.send_message(f"❌ Nahi! Jawab tha: **{rec['answers'][0]}**")

@games_group.command(name="scramble", description="Ulta-phulta shabd milega — sahi shabd bano, /games scramble_guess")
async def games_scramble(interaction: discord.Interaction):
    shabd=_SCRAMBLE_WORDS[random.randrange(len(_SCRAMBLE_WORDS))]
    ulta=list(shabd); random.shuffle(ulta); ulta="".join(ulta)
    if ulta == shabd: ulta=shabd[::-1]
    _game_state[interaction.user.id]={"type": "scramble", "answer": shabd}
    await interaction.response.send_message(f"🔤 **Scramble:** `{ulta}`\nSahi shabd `/games scramble_guess` se batao!")

@games_group.command(name="scramble_guess", description="Scramble ka sahi shabd batao")
@app_commands.describe(word="Sahi shabd")
async def games_scramble_guess(interaction: discord.Interaction, word: str):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "scramble":
        await interaction.response.send_message("ℹ️ Pehle `/games scramble` lo.", ephemeral=True); return
    if word.strip().lower() == rec["answer"]:
        _game_state.pop(interaction.user.id, None)
        await interaction.response.send_message(f"🎉 **Sahi!** Shabd tha: **{rec['answer']}**")
    else:
        await interaction.response.send_message("❌ Galat — fir try Karo! (game chal raha hai)")

@games_group.command(name="mathquiz", description="Maths ka sawal — /games math_answer se jawab")
async def games_mathquiz(interaction: discord.Interaction):
    a, b=random.randint(2, 50), random.randint(2, 30)
    op=random.choice(["+", "-", "*"])
    sahi={"+" : a+b, "-": a-b, "*": a*b}[op]
    _game_state[interaction.user.id]={"type": "math", "answer": sahi}
    await interaction.response.send_message(f"➗ **Math Quiz:** {a} {op} {b} = ?\nJawab `/games math_answer` se do!")

@games_group.command(name="math_answer", description="Math quiz ka jawab (number)")
@app_commands.describe(answer="Tumhara number wala jawab")
async def games_math_answer(interaction: discord.Interaction, answer: int):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "math":
        await interaction.response.send_message("ℹ️ Pehle `/games mathquiz` lo.", ephemeral=True); return
    _game_state.pop(interaction.user.id, None)
    if answer == rec["answer"]:
        await interaction.response.send_message(f"🎉 **Bilkul sahi, {interaction.user.mention}!**")
    else:
        await interaction.response.send_message(f"❌ Galat! Sahi jawab **{rec['answer']}** tha.")

@games_group.command(name="numberguess", description="1-100 ke beech number socha hai — /games guess se dhundho")
async def games_numberguess(interaction: discord.Interaction):
    _game_state[interaction.user.id]={"type": "number", "answer": random.randint(1, 100), "tries": 0}
    await interaction.response.send_message(f"🎯 {interaction.user.mention} Maine **1-100** ke beech Ek number socha hai! `/games guess` se dhundho!")

@games_group.command(name="guess", description="Number guess Karo")
@app_commands.describe(number="Tumhara guess (1-100)")
async def games_guess(interaction: discord.Interaction, number: app_commands.Range[int, 1, 100]):
    rec=_game_state.get(interaction.user.id)
    if not rec or rec.get("type") != "number":
        await interaction.response.send_message("ℹ️ Pehle `/games numberguess` shuru Karo.", ephemeral=True); return
    rec["tries"]+=1
    if number == rec["answer"]:
        tries=rec["tries"]; _game_state.pop(interaction.user.id, None)
        await interaction.response.send_message(f"🎉 **Mil gaya!** Number **{number}** tha — {tries} koshish me dhundh liya!")
    elif number < rec["answer"]:
        await interaction.response.send_message("⬆️ Isse **bada** hai! Fir guess Karo.")
    else:
        await interaction.response.send_message("⬇️ Isse **chhota** hai! Fir guess Karo.")

@games_group.command(name="slots", description="Economy coins se slot machine (triple = 5x munafa)")
@app_commands.describe(amount="Kitne coins lagane hai (min 10)")
async def games_slots(interaction: discord.Interaction, amount: app_commands.Range[int, 10, 100000] = 10):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Slots sirf server me chalta hai.", ephemeral=True); return
    rec=_eco_rec(interaction.guild.id, interaction.user.id)
    bal=int(rec.get("coins", 0))
    if bal < amount:
        await interaction.response.send_message(f"❌ Coins kam hai (balance 🪙 {bal:,}). `/daily` se pehle kamao!", ephemeral=True); return
    faces=["🍒", "🍋", "🍇", "⭐", "💎"]
    ghuma=[random.choice(faces) for _ in range(3)]
    if ghuma[0] == ghuma[1] == ghuma[2]:
        rec["coins"]=bal + amount*5; natija=f"🎰💰 **TRIPLE! +🪙 {amount*5:,} jeete!**"
    elif ghuma[0] == ghuma[1] or ghuma[1] == ghuma[2] or ghuma[0] == ghuma[2]:
        rec["coins"]=bal + amount; natija=f"🎰 **Jodi bani! +🪙 {amount:,} jeete!**"
    else:
        rec["coins"]=bal - amount; natija=f"🎰💔 Kuch nahi mila — 🪙 {amount:,} gaye."
    _save_economy()
    await interaction.response.send_message(f"🎰 | {' | '.join(ghuma)} |\n{natija}\n💰 Balance: **🪙 {int(rec['coins']):,}**")

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
                    try: await ch.send(f"🔀 **Autoplay:** Gaana khatam, similar gaana la rahi hu... 🎵")
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
            try: await ch.send(f"⚠️ Gaana nahi chal paya — wajah: `{str(e)[:120]}`")
            except Exception: pass
        if st.get("queue"):
            asyncio.create_task(_play_next(guild))
        elif st.get("autoplay", True) and not st.get("manual_stop") and st["fail_count"] < 3:
            # queue khali par autoplay se agla similar try karo — ek fail par gana band nahi hoga
            asyncio.create_task(_play_next(guild))
        else:
            st["current"]=None
            if st["fail_count"] >= 3 and ch is not None:
                try: await ch.send("⏹️ Lagatar 3 gaane fail hue — autoplay rok diya. `/p` se naya gaana lagao.")
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
            await ch.send(f"🎵 **Now Playing:** {track['title']}\n_(Requested by {track['requester']})_\n🔀 Autoplay: **{'ON' if st.get('autoplay', True) else 'OFF'}** _(Panel se control Karo 👇)_", view=MusicPanelView())
        except Exception: pass

# ------------- MIC LISTEN: Nezuko naam sunke VC me bolkar jawab -------------
_VOICE_LISTEN: dict[int, bool] = {}
_VOICE_LISTEN_CH: dict[int, int] = {}
_vc_tasks: dict[int, asyncio.Task] = {}
_VC_COOLDOWN: dict[int, float] = {}
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
    for model in ["gemini-2.0-flash", "gemini-2.5-flash", GEMINI_MODEL, "gemini-3-flash-preview", "gemini-flash-latest"]:
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
                async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=9)) as resp:
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
    """Awaaz samajhne ki race: Google-free aur Gemini dono ek saath —
    jo pehle sahi transcript de, wahi lo (tez + sahi)."""
    async def _google():
        try:
            return await asyncio.get_event_loop().run_in_executor(None, lambda: _stt_google_free(wav_bytes, quick=True))
        except Exception:
            log.warning("STT google fail\n%s", traceback.format_exc())
            return ""
    tasks={asyncio.create_task(_google()): "google-free", asyncio.create_task(_stt_gemini_once(wav48_bytes or wav_bytes)): "gemini"}
    deadline=time.time()+10
    pending=set(tasks)
    try:
        while pending and time.time() < deadline:
            done, pending = await asyncio.wait(pending, timeout=max(0.1, deadline-time.time()), return_when=asyncio.FIRST_COMPLETED)
            if not done: break
            for t in done:
                try: r=t.result()
                except Exception: r=""
                if r:
                    for b in pending: b.cancel()
                    log.info("STT source: %s", tasks[t])
                    return r
    except Exception:
        log.warning("STT race fail\n%s", traceback.format_exc())
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
            except Exception:
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

_WAKE_WORDS=("nezuko", "Nezuko", "nezuku", "nezko", "nuzuko", "ne juko", "ne zuko", "Nezuko",
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
        log.info("VC awaaz level max=%s (0=chup, 1000+=halki, 8000+=saaf)", _mx)
        wav48=_pcm_to_wav_48k(pcm, 1)
        wav=_pcm_to_wav(pcm)
        if len(wav48) > 6_000_000: return
        transcript=await _stt_first_try(wav, wav48)
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
            jawab="Haa bolo, mai sun rahi hu! 🌸"
        else:
            hist=_memory.setdefault((guild.id, user_id), [])
            member=None
            try: member=guild.get_member(user_id)
            except Exception: pass
            naam=member.display_name if member else "friend"
            prompt_vc=(f"{naam} tumse VC me ek dost ki tarah baat kar raha hai. Usne kaha: {sawal[:1200]}. "
                       "bilkul naturally, jaise asli dost jawab deti hai — bohut chhota (1-2 vakya, max 40 shabd), user ki bhasha me hi.")
            try:
                jawab=await asyncio.wait_for(_ask_ai_fast(prompt_vc, hist, timeout=10), timeout=12)
            except Exception:
                jawab=""
            log.info("VC AI time kul %.1fs", time.time()-_t0)
            if not jawab or jawab.startswith("⚠️"):
                jawab="Hmm, ek second ruko... kuch atak gaya, ek baar fir bolo na? 🌸"
            hist += [{"role": "user", "parts": [{"text": sawal[:1500]}]}, {"role": "model", "parts": [{"text": jawab[:1500]}]}]
            del hist[:-6]
        if ch is not None:
            try: await ch.send(f"🎙️ **Nezuko VC:** {jawab}")
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
            done=asyncio.Event()
            def _cb(e, done=done):
                try: bot.loop.call_soon_threadsafe(done.set)
                except Exception: pass
            vc.play(discord.FFmpegPCMAudio(path, options="-vn"), after=_cb)
            try: await asyncio.wait_for(done.wait(), timeout=90)
            except Exception: pass
        finally:
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
                if now - last >= 0.7 and dur >= 0.5:
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
    try:
        if guild.voice_client is not None:
            guild.voice_client.stop_listening()
    except Exception: pass
    t=_vc_tasks.pop(guild.id, None)
    if t: t.cancel()


class MusicPanelView(discord.ui.View):
    """Music control panel — Now Playing message ke neeche lage button."""
    def __init__(self): super().__init__(timeout=None)

    async def _vc(self, interaction: discord.Interaction):
        vc=interaction.guild.voice_client if interaction.guild else None
        if vc is None:
            await interaction.response.send_message("ℹ️ Nezuko abhi VC me nahi hai — pehle `/play` se gaana lagao.", ephemeral=True)
            return None
        user_voice=getattr(interaction.user, "voice", None)
        if not user_voice or not user_voice.channel or user_voice.channel != vc.channel:
            await interaction.response.send_message("❌ Panel use karne ke liye usi VC me hona padega jisme gaana baj raha hai.", ephemeral=True)
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
            await interaction.response.send_message("ℹ️ Kuch baj hi nahi raha.", ephemeral=True)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc=await self._vc(interaction)
        if vc is None: return
        if vc.is_playing() or vc.is_paused():
            vc.stop(); await interaction.response.send_message("⏭️ **Skipped!** (panel)", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Kuch baj hi nahi raha.", ephemeral=True)

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
        await interaction.response.send_message("⏹️ **Music band!** (panel) Nezuko VC se bahar aa gayi.")

    @discord.ui.button(label="Queue", emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="music:queue")
    async def btn_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
        st=_music_of(interaction.guild.id)
        cur=st.get("current")
        if not cur and not st["queue"]:
            await interaction.response.send_message("🎵 Queue khaali hai.", ephemeral=True); return
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
        await interaction.response.send_message(f"🔀 **Autoplay {'ON' if st['autoplay'] else 'OFF'}!** Gaana khatam ho to similar gaana {'khud chalega' if st['autoplay'] else 'nahi chala karega'}.", ephemeral=True)

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

@bot.tree.command(name="vcchat", description="Nezuko ko bolo: naam leke bulaoge to VC me bolkar jawab degi")
@app_commands.describe(action="On = sunna chalu Karo | Off = band | Status = state dekho")
@app_commands.choices(action=[app_commands.Choice(name="On", value="on"), app_commands.Choice(name="Off", value="off"), app_commands.Choice(name="Status", value="status")])
async def vcchat_cmd(interaction: discord.Interaction, action: str = "on"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Ye server me hi chalega.", ephemeral=True); return
    act=(action or "on").lower().strip()
    guild=interaction.guild
    if act == "status":
        halat="ON 🎙️" if _VOICE_LISTEN.get(guild.id) else "OFF"
        await interaction.response.send_message(f"🎧 Nezuko VC me sunn rahi hai: **{halat}**\n(On ho to bolo: **'Nezuko, kya kar rahi ho?'** — jawab awaaz me ayega!)", ephemeral=True); return
    if act == "off":
        _stop_listening(guild)
        await interaction.response.send_message("🔇 Nezuko ne VC me sunna band kar diya.", ephemeral=True); return
    vc=guild.voice_client
    if vc is None:
        await interaction.response.send_message("❌ Pehle Nezuko ko VC me bulao — khud VC join karke `/join` Karo, fir `/vcchat On`.", ephemeral=True); return
    ok, wajah=_start_listening(guild, interaction.channel.id)
    if ok:
        await interaction.response.send_message("🎙️ **Nezuko sun rahi hai!** Ab VC me bolo:\n> **'Nezuko, kya Kar rahi ho?'**\nWo naam sunkar bolkar jawab degi 🌸\n_(Har baat se pehle naam lazmi lo; bina naam ke wo reply nahi karegi.)_")
    elif wajah == "opus_missing":
        await interaction.response.send_message("⚠️ Termux me `pkg install -y libopus opusfile` karke bot restart Karo, fir `/vcchat On` Karo.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Listening chalu nahi ho pai — bot restart karke fir `/vcchat On` try Karo (ya log me 'recording' wali line dekho).", ephemeral=True)


@bot.tree.command(name="join", description="Nezuko ko apne voice channel me bulao")
async def join_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Join sirf server me hota hai.", ephemeral=True); return
    voice=getattr(interaction.user, "voice", None)
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ Pehle khud kisi **voice channel** me join Karo, fir `/join` Karo.", ephemeral=True); return
    vc=interaction.guild.voice_client
    try:
        if vc is None:
            _vcls=_voice_recv.VoiceRecvClient if _voice_recv is not None else discord.VoiceClient
            vc=await voice.channel.connect(cls=_vcls)
        elif vc.channel != voice.channel:
            await vc.move_to(voice.channel)
    except Exception as e:
        log.error("join connect fail\n%s", traceback.format_exc())
        if isinstance(e, RuntimeError) and ("PyNaCl" in str(e) or "davey" in str(e)):
            await interaction.response.send_message("⚠️ Nezuko VC join nahi kar payi — Termux me **voice library (PyNaCl/davey) missing** hai, permission ki dikkat nahi hai. Admin ko batao, iska fix Termux se hoga.", ephemeral=True)
        elif isinstance(e, discord.Forbidden):
            await interaction.response.send_message("⚠️ Nezuko VC join nahi kar payi — is VC me uski `Connect/Speak` permission sach me band hai.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Nezuko VC join nahi kar payi — wajah: `{type(e).__name__}`. Admin ko batao.", ephemeral=True)
        return
    await interaction.response.send_message(f"🎵 **Nezuko {voice.channel.name} me aa gayi!** Ab `/p gaane ka naam` do, wahi gaana chalega. Panel se skip/stop/autoplay control hoga.")

@bot.tree.command(name="play", description="Play a song in your voice channel (search or YouTube link)")
@app_commands.describe(song="Gaane ka naam ya YouTube link")
@app_commands.autocomplete(song=_song_autocomplete)
async def play_cmd(interaction: discord.Interaction, song: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Music sirf server me chalta hai.", ephemeral=True); return
    voice=getattr(interaction.user, "voice", None)
    if not voice or not voice.channel:
        await interaction.response.send_message("❌ Pehle kisi **voice channel** me join Karo, fir `/play` Karo.", ephemeral=True); return
    if _ensure_ytdlp() is None:
        await interaction.response.send_message("❌ Music ke liye `yt-dlp` abhi bot ke andar load nahi ho raha. Termux me `python3 -m pip install -U yt-dlp` chalao, fir bot restart Karo.", ephemeral=True); return
    await interaction.response.defer()
    lag=await interaction.followup.send(f"🔎 **Nezuko gaana dhundh rahi hai...** 🎵 _{song}_")
    vc=interaction.guild.voice_client
    try:
        if vc is None:
            _vcls=_voice_recv.VoiceRecvClient if _voice_recv is not None else discord.VoiceClient
            vc=await voice.channel.connect(cls=_vcls)
        elif vc.channel != voice.channel:
            await vc.move_to(voice.channel)
    except Exception as e:
        log.error("music connect fail\n%s", traceback.format_exc())
        if isinstance(e, RuntimeError) and ("PyNaCl" in str(e) or "davey" in str(e)):
            await lag.edit(content="⚠️ Voice channel join nahi kar payi — Termux me **voice library (PyNaCl/davey) missing** hai, permission ki dikkat nahi hai.")
        elif isinstance(e, discord.Forbidden):
            await lag.edit(content="⚠️ Voice channel join nahi kar payi — is VC me meri `Connect/Speak` permission sach me band hai.")
        else:
            await lag.edit(content=f"⚠️ Voice channel join nahi kar payi — wajah: `{type(e).__name__}`")
        return
    loop=asyncio.get_event_loop()
    try:
        track=await loop.run_in_executor(None, _yt_extract, song)
    except Exception:
        log.error("music search fail\n%s", traceback.format_exc())
        await lag.edit(content="⚠️ Ye gaana nahi mila — naam sahi likh ke fir try Karo."); return
    track["requester"]=interaction.user.display_name
    st=_music_of(interaction.guild.id)
    st["manual_stop"]=False
    st["text_channel"]=interaction.channel
    if vc.is_playing() or vc.is_paused():
        st["queue"].append(track)
        await lag.edit(content=f"➕ **Queue me add ho gaya (#{len(st['queue'])}):** {track['title']} 🎵")
    else:
        st["queue"].append(track)
        await lag.edit(content=f"🎵 **Mil gaya:** {track['title']} — baj raha hai...")
        await _play_next(interaction.guild)

@bot.tree.command(name="p", description="Shortcut of /play — play a song in your voice channel")
@app_commands.describe(song="Gaane ka naam ya YouTube link")
@app_commands.autocomplete(song=_song_autocomplete)
async def p_cmd(interaction: discord.Interaction, song: str):
    await play_cmd.callback(interaction, song)

@bot.tree.command(name="skip", description="Skip the current song")
async def skip_cmd(interaction: discord.Interaction):
    vc=interaction.guild.voice_client if interaction.guild else None
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ **Skipped!** Agla gaana aa raha hai...")
    else:
        await interaction.response.send_message("ℹ️ Abhi kuch baj hi nahi raha.", ephemeral=True)

@bot.tree.command(name="stop", description="Stop music, clear queue and leave voice channel")
async def stop_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    st=_music_of(interaction.guild.id)
    st["queue"].clear(); st["current"]=None; st["manual_stop"]=True
    vc=interaction.guild.voice_client
    if vc:
        try: vc.stop()
        except Exception: pass
        await vc.disconnect()
    await interaction.response.send_message("⏹️ **Music band!** Queue saaf kar di aur Nezuko VC se bahar aa gayi.")

@bot.tree.command(name="pause", description="Pause the current song")
async def pause_cmd(interaction: discord.Interaction):
    vc=interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ **Paused.** `/resume` se wapas chalega.")
    else:
        await interaction.response.send_message("ℹ️ Abhi kuch baj nahi raha jo pause Karu.", ephemeral=True)

@bot.tree.command(name="resume", description="Resume the paused song")
async def resume_cmd(interaction: discord.Interaction):
    vc=interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ **Resumed!**")
    else:
        await interaction.response.send_message("ℹ️ Koi gaana paused nahi hai.", ephemeral=True)

@bot.tree.command(name="queue", description="See the music queue")
async def queue_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    st=_music_of(interaction.guild.id)
    cur=st.get("current")
    if not cur and not st["queue"]:
        await interaction.response.send_message("🎵 Queue khaali hai — `/play` se gaana lagao.", ephemeral=True); return
    lines=[]
    if cur: lines.append(f"▶️ **Now Playing:** {cur['title']}")
    for i, t in enumerate(st["queue"][:10], 1):
        lines.append(f"{i}. {t['title']}")
    if len(st["queue"]) > 10: lines.append(f"...aur {len(st['queue'])-10} gaane")
    await interaction.response.send_message("🎵 **Music Queue:**\n" + "\n".join(lines))

@bot.tree.command(name="nowplaying", description="See what song is playing right now")
async def nowplaying_cmd(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Sirf server me.", ephemeral=True); return
    cur=_music_of(interaction.guild.id).get("current")
    if cur:
        dur=int(cur.get("duration") or 0)
        mmss=f" ({dur//60}:{dur%60:02d})" if dur else ""
        await interaction.response.send_message(f"🎵 **Now Playing:** {cur['title']}{mmss}\n_(Requested by {cur['requester']})_")
    else:
        await interaction.response.send_message("ℹ️ Abhi kuch nahi baj raha — `/play` se lagao.", ephemeral=True)

@bot.tree.command(name="autoplay", description="Autoplay: song khatam ho to similar song khud chalega (On/Off/Status)")
@app_commands.describe(action="Choose On, Off, or Status — default is Status")
@app_commands.choices(action=[
    app_commands.Choice(name="On — similar songs auto-chalenge", value="on"),
    app_commands.Choice(name="Off — sirf queue ke songs", value="off"),
    app_commands.Choice(name="Status — setting dekho", value="status"),
])
async def autoplay_cmd(interaction: discord.Interaction, action: str = "status"):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Autoplay sirf server me chalta hai.", ephemeral=True); return
    st=_music_of(interaction.guild.id)
    if action == "on":
        st["autoplay"]=True
        await interaction.response.send_message("🔀 **Autoplay ON!** Jo gaana daloge wahi chalega, khatam ho to usi type ka similar gaana khud aa jayega. 🎵")
    elif action == "off":
        st["autoplay"]=False
        await interaction.response.send_message("🔀 **Autoplay OFF.** Ab sirf wahi chalega jo `/play` ya `/p` se daloge.")
    else:
        state="ON ✅" if st.get("autoplay", True) else "OFF"
        await interaction.response.send_message(f"🔀 Autoplay is **{state}** in this server.")

# ---- GitHub -> Discord ----
async def github_handler(request: web.Request):
    if GITHUB_HOOK_SECRET and request.headers.get("X-Hub-Secret") != GITHUB_HOOK_SECRET:
        return web.json_response({"error":"bad secret"}, status=401)
    try: payload=await request.json()
    except: return web.json_response({"error":"invalid json"}, status=400)
    channel_id=int(payload.get("channel_id") or 0)
    text=str(payload.get("text") or "GitHub event")
    title=str(payload.get("title") or "GitHub")
    url=str(payload.get("url") or "")
    color=int(payload.get("color") or 3447003)
    ch=bot.get_channel(channel_id) if channel_id else None
    if ch is None and WELCOME_CHANNEL_ID: ch=bot.get_channel(WELCOME_CHANNEL_ID)
    if ch is None: return web.json_response({"error":"channel not found"}, status=404)
    embed=discord.Embed(title=title, description=text, color=color, url=url or None)
    embed.set_footer(text=f"github • {payload.get('event','webhook')}")
    await ch.send(embed=embed)
    log.info("github -> #%s", ch)
    return web.json_response({"ok":True, "channel":ch.id})

async def start_http_server():
    app=web.Application()
    app.router.add_post("/github", github_handler)
    app.router.add_get("/health", lambda r: web.json_response({"ok":True}))
    runner=web.AppRunner(app); await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",HTTP_PORT); await site.start()
    log.info("HTTP 0.0.0.0:%s (POST /github)", HTTP_PORT)

def main():
    if not TOKEN: raise SystemExit("DISCORD_BOT_TOKEN set nahi hai. .env me daalo.")
    import asyncio
    log.info("Bot start ho raha hai... token mila: haan (lambaai %s)", len(TOKEN))
    async def _run():
        # HTTP (GitHub webhook) server fail ho jaye (port busy wagera) to bhi
        # bot online hona NAHI rukna chahiye — pehle yehi ek chhupa crash tha
        try: await start_http_server()
        except Exception: log.warning("HTTP server start nahi hua (port busy?) — bot phir bhi online hoga\n%s", traceback.format_exc())
        # Supervisor loop: net/DNS ki wajah se gateway connect pehli baar
        # fail ho to discord.py khud AttributeError se resumr jata hai
        # (ws None par .sequence) — isliye bot band nahi hoga, retry hoga.
        _tries=0
        while True:
            try:
                await bot.start(TOKEN)
                break  # saaf band (bot.close) — bahar niklo
            except discord.LoginFailure:
                log.error("LOGIN FAIL: token galat/kharab hai (401). .env me sahi DISCORD_BOT_TOKEN dalo — bot DM wala purana tareeka hi chalega.")
                raise SystemExit(1)
            except discord.PrivilegedIntentsRequired:
                log.error("LOGIN FAIL: Developer Portal me Server Members Intent ON Karo (Privileged Gateway Intents).")
                raise SystemExit(1)
            except Exception:
                _tries+=1
                log.error("Discord se connect fail (network/DNS gadbad?) — 20s me retry #%s\n%s", _tries, traceback.format_exc())
                await asyncio.sleep(20)
    try: asyncio.run(_run())
    except KeyboardInterrupt: pass
if __name__=="__main__": main()
