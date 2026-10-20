"""
INC Bot — Auto-DM + Guild management
Naya member -> DM + Verify button, /dmtest, /ping, /say, /announce, /clear + GitHub -> Discord bridge
"""
from __future__ import annotations
import ast, asyncio, calendar, datetime, io, json, logging, operator, os, random, re, string, time, traceback, urllib.parse
from pathlib import Path
import aiohttp, discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

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

TOKEN=os.environ.get("DISCORD_BOT_TOKEN","")
GITHUB_HOOK_SECRET=os.environ.get("GITHUB_HOOK_SECRET","")
HTTP_PORT=int(os.environ.get("HTTP_PORT","8080"))
VERIFY_ROLE_ID=int(os.environ.get("VERIFY_ROLE_ID","0") or 0)
WELCOME_CHANNEL_ID=int(os.environ.get("WELCOME_CHANNEL_ID","0") or 0)
DM_TEXT=os.environ.get("DM_TEXT","Hey {user} 👋\n\n**{guild}** me swagat hai!\n\n1. Pehle <#rules> padho\n2. Neeche **Verify** dabao\n3. Tabhi channels khulenge")
REQUIRE_VERIFY=os.environ.get("REQUIRE_VERIFY","1")=="1"
GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY","")
GEMINI_MODEL=os.environ.get("GEMINI_MODEL","gemini-3.1-flash-lite")
BOT_OWNER_ID=int(os.environ.get("BOT_OWNER_ID","0") or 0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log=logging.getLogger("autodm")

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
    # SIRF global sync — guild copies banaoge to Discord me har command 2-2 dikhega
    try:
        await bot.tree.sync()
        log.info("slash commands synced (global only)")
    except Exception as e: log.warning("sync fail: %s", e)

@bot.event
async def on_ready():
    log.info("logged in as %s (id=%s) — %s guilds", bot.user, bot.user.id, len(bot.guilds))
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

@bot.event
async def on_member_join(member: discord.Member):
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
    text=DM_TEXT.format(user=member.mention, guild=member.guild.name, count=member.guild.member_count)
    view=VerifyButton() if REQUIRE_VERIFY else None
    try:
        await member.send(text, view=view)
        log.info("DM sent -> %s (%s)", member, member.id)
    except discord.Forbidden:
        log.warning("DM blocked by %s", member)
        await _fallback_post(member, "DM nahi ja paya (user ne DM block kiya hai)")
    except Exception:
        log.error("DM fail %s\n%s", member, traceback.format_exc())
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
_warnings: dict[int, dict[int, list[str]]] = {}

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
    embed=discord.Embed(title="🤖 DIANA — Commands", color=0x9b59b6)
    embed.add_field(name="🛡️ Admin / Moderation", value="`/kick` `/ban` `/unban` `/timeout` `/untimeout` `/warn` `/warnings` `/clearwarn` `/clear` `/slowmode` `/lock` `/unlock` `/role_add` `/role_remove` `/nickname`", inline=False)
    embed.add_field(name="📢 Announcement", value="`/say` `/announce` `/dm` `/report`", inline=False)
    embed.add_field(name="ℹ️ Jankari", value="`/ping` `/botinfo` `/uptime` `/serverinfo` `/userinfo` `/avatar` `/membercount` `/roleinfo`", inline=False)
    embed.add_field(name="🎉 Utility", value="`/poll` `/remind` `/help`", inline=False)
    embed.add_field(name="🎁 Giveaway", value="`/giveaway` — prize, time (jaise `30m`, `2h`, `1d`) aur winners set karke stylish giveaway start karo. Time khatam hote hi winner automatic announce hoga.", inline=False)
    embed.add_field(name="🎭 Auto-Role", value="`/autorole_bot_add` (naya member join kare to khud role milega) • `/autorole_bot_remove` • `/autorole_bot_status`", inline=False)
    embed.add_field(name="👑 No Prefix (Owner Only)", value="`/noprefix` — granted member can chat with the AI without `/ask` or a mention. Power lasts **2 months**.", inline=False)
    embed.add_field(name="🤖 AI Chat", value="`/ask sawal` • `/chatbot_on` — isse chalu channel me DIANA sirf tab jawab degi jab use @tag karke kuch bola jaye", inline=False)
    embed.add_field(name="🎨 AI Image", value="`/image kya banana hai` — bas ye ek hi image wala command hai", inline=False)
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
    deleted=await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🗑️ {len(deleted)} messages delete kiye.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set channel slowmode (admin)")
@app_commands.describe(seconds="Kitne second ka slowmode (0 = band)", channel="Kis channel me (khali = yahi)")
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 0 <= seconds <= 21600: await interaction.response.send_message("❌ 0 se 21600 ke beech do.", ephemeral=True); return
    ch=channel or interaction.channel
    await ch.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"🐌 {ch.mention} ka slowmode **{seconds}s** ho gaya.", ephemeral=True)

@bot.tree.command(name="lock", description="Lock this channel (admin)")
@app_commands.describe(channel="Kis channel ko band karna hai (khali = yahi)")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    await ch.send("🔒 Ye channel band kiya gaya hai.")
    await interaction.response.send_message(f"🔒 {ch.mention} band ho gaya.", ephemeral=True)

@bot.tree.command(name="unlock", description="Unlock this channel (admin)")
@app_commands.describe(channel="Kis channel ko kholna hai (khali = yahi)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=None)
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
    await member.timeout(None)
    await interaction.response.send_message(f"🔊 {member.mention} ka timeout hata diya.", ephemeral=True)

@bot.tree.command(name="warn", description="Warn a member (admin)")
@app_commands.describe(member="Kisko warn karna hai", reason="Wajah")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.setdefault(interaction.guild.id, {}).setdefault(member.id, []).append(reason)
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
    _warnings.get(interaction.guild.id, {}).pop(member.id, None)
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

@bot.tree.command(name="remind", description="Set a reminder")
@app_commands.describe(minutes="Kitne minute baad (1-10080)", text="Kya yaad dilana hai")
async def remind(interaction: discord.Interaction, minutes: int, text: str):
    if not 1 <= minutes <= 10080: await interaction.response.send_message("❌ 1 se 10080 minute ke beech do.", ephemeral=True); return
    await interaction.response.send_message(f"⏰ Thik hai {interaction.user.mention}, **{minutes} minute** baad yaad dila dunga: {text}", ephemeral=True)
    async def _later():
        await asyncio.sleep(minutes*60)
        try: await interaction.channel.send(f"⏰ {interaction.user.mention} Reminder: **{text}**")
        except Exception: pass
    asyncio.create_task(_later())

_ask_cooldown: dict[int, float] = {}

async def _ask_gemini(prompt: str, history=None) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ AI is not configured yet — the `GEMINI_API_KEY` is missing."
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload={
        "system_instruction": {"parts": [{"text": "You are DIANA, a Discord bot assistant. LANGUAGE RULE (highest priority): Always reply in clear English ONLY, no matter which language the user writes in (Hindi, Hinglish, Urdu, Spanish, or any other language). Even if the user asks you to reply in another language, still answer in English only. Understand the user's question in their language, but write your entire answer in English. Keep answers helpful, concise and under 1800 characters."}]},
        "contents": (history or []) + [{"role": "user", "parts": [{"text": "[Reply in English only.] " + prompt[:4000]}]}],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                data=await resp.json()
                if resp.status != 200:
                    log.warning("Gemini HTTP %s: %s", resp.status, str(data)[:300])
                    if resp.status == 429: return "⚠️ The AI free limit is over for now — please try again in a little while."
                    if resp.status == 503: return "⚠️ The AI is busy right now — please try again in a minute."
                    return "⚠️ The AI could not answer right now — please try again in a little while."
                return data["candidates"][0]["content"]["parts"][0]["text"][:1900]
    except Exception:
        log.error("Gemini fail\n%s", traceback.format_exc())
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
    embed.set_footer(text="DIANA Giveaways • Good luck everyone! 🍀")
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
    hist=_memory.setdefault((interaction.guild.id if interaction.guild else 0, interaction.user.id), [])
    jawab=await _ask_gemini(sawal, hist)
    hist += [{"role":"user","parts":[{"text":sawal[:2000]}]}, {"role":"model","parts":[{"text":jawab[:2000]}]}]
    del hist[:-6]
    await interaction.followup.send(f"**{interaction.user.display_name}:** {sawal}\n\n{jawab}")

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
                async with message.channel.typing():
                    jawab=await _ask_gemini(sawal, hist)
                hist += [{"role":"user","parts":[{"text":sawal[:2000]}]}, {"role":"model","parts":[{"text":jawab[:2000]}]}]
                del hist[:-6]
                await message.reply(jawab)
    await bot.process_commands(message)

@bot.tree.command(name="chatbot_on", description="Tag-only AI chat for this channel: DIANA replies only when mentioned (admin)")
@app_commands.describe(action="Choose On, Off, or Status — default is On")
@app_commands.choices(action=[
    app_commands.Choice(name="On — reply only when DIANA is tagged", value="on"),
    app_commands.Choice(name="Off — stop tag replies everywhere", value="off"),
    app_commands.Choice(name="Status — check whether tag chat is on", value="status"),
])
async def chatbot_on_cmd(interaction: discord.Interaction, action: str = "on"):
    global _tag_chatbot_master_on
    if interaction.channel is None:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True); return
    if action in ("on", "off") and not _is_mod(interaction):
        await _deny(interaction); return
    if action == "on":
        if _tag_chatbot_master_on and interaction.channel.id in _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Tag chat is already **ON** in this channel. Mention DIANA with your question and she will reply.", ephemeral=True); return
        _tag_chatbot_master_on=True
        _tag_chatbot_channels.add(interaction.channel.id); _save_chatbot_channels()
        log.info("chatbot_on: tag chat ON in channel %s by %s", interaction.channel.id, interaction.user)
        await interaction.response.send_message("🤖 **Chatbot ON!** ✅\nFrom now on, DIANA will reply in this channel **only when someone tags/mentions her** with a question.\nExample: `@DIANA What is the capital of India?`\n\nUse `/chatbot_on` and choose **Off** to stop tag replies everywhere.")
    elif action == "off":
        if not _tag_chatbot_master_on and not _tag_chatbot_channels:
            await interaction.response.send_message("ℹ️ Chatbot is already OFF everywhere.", ephemeral=True); return
        _tag_chatbot_master_on=False
        _tag_chatbot_channels.clear(); _save_chatbot_channels()
        log.info("chatbot_on: tag chat OFF everywhere by %s", interaction.user)
        await interaction.response.send_message("🤖 **Chatbot OFF kar diya hai — har jagah.** ✅\nAb DIANA kisi bhi channel me tag karne par reply nahi karegi. Wapas chalana ho to kisi channel me `/chatbot_on` chalakar **On** choose karna.", ephemeral=True)
    else:
        if _tag_chatbot_master_on and interaction.channel.id in _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Tag chat is **ON** in this channel — DIANA replies when she is tagged/mentioned.", ephemeral=True)
        elif _tag_chatbot_master_on and _tag_chatbot_channels:
            await interaction.response.send_message("🤖 Tag chat is ON in another channel, but **OFF in this channel**.", ephemeral=True)
        else:
            await interaction.response.send_message("🤖 Chatbot is **OFF everywhere**. An admin can start it with `/chatbot_on`.", ephemeral=True)

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
    async with aiohttp.ClientSession() as session:
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
    try:
        data=await _make_image(vivaran+", high quality, detailed, vibrant")
    except Exception:
        log.error("image fail\n%s", traceback.format_exc())
        await interaction.followup.send("⚠️ Image nahi ban payi — thodi der baad fir try karna.")
        return
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
        old_reason=_afk_data.pop(author_key, {}).get("reason", "")
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

@bot.tree.command(name="invite", description="Get DIANA's invite link and this server's invite")
async def invite_cmd(interaction: discord.Interaction):
    app_id=bot.application_id or (bot.user.id if bot.user else 0)
    bot_invite=f"https://discord.com/oauth2/authorize?client_id={app_id}&permissions=8&scope=bot%20applications.commands"
    embed=discord.Embed(title="💌 Invite DIANA", description="Use the links below:", color=0x9b59b6)
    embed.add_field(name="🤖 Add DIANA to a server", value=f"[Click here to invite DIANA]({bot_invite})", inline=False)
    if interaction.guild and interaction.channel:
        try:
            inv=await interaction.channel.create_invite(max_age=0, max_uses=0, reason="Invite requested from DIANA")
            embed.add_field(name="🏠 This server's invite", value=inv.url, inline=False)
        except Exception:
            embed.add_field(name="🏠 This server's invite", value="I could not create a server invite in this channel.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="support", description="Get help and support information for DIANA")
async def support_cmd(interaction: discord.Interaction):
    owner_text="the bot owner"
    try:
        info=await bot.application_info()
        owner_text=info.owner.mention
    except Exception:
        pass
    embed=discord.Embed(title="🆘 DIANA Support", color=0x3498db)
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

@bot.tree.command(name="choose", description="Let DIANA choose one option for you")
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

@bot.tree.command(name="rps", description="Play Rock Paper Scissors with DIANA")
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
        result="🤖 DIANA wins!"
    await interaction.response.send_message(f"You chose **{icons[user_choice]}**\nDIANA chose **{icons[bot_choice]}**\n\n{result}")

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
    async def _run():
        await start_http_server()
        await bot.start(TOKEN)
    try: asyncio.run(_run())
    except KeyboardInterrupt: pass
if __name__=="__main__": main()
