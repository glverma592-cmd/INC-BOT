"""
INC Bot — Auto-DM + Guild management
Naya member -> DM + Verify button, /dmtest, /ping, /say, /announce, /clear + GitHub -> Discord bridge
"""
from __future__ import annotations
import asyncio, datetime, io, logging, os, random, time, traceback, urllib.parse
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
    # instant guild sync (global sync 1hr lagta hai)
    try:
        # sync globally + per-guild for instant
        await bot.tree.sync()
        for g in bot.guilds:
            try: await bot.tree.sync(guild=g)
            except: pass
        log.info("slash commands synced (global + %s guilds)", len(bot.guilds))
    except Exception as e: log.warning("sync fail: %s", e)

@bot.event
async def on_ready():
    log.info("logged in as %s (id=%s) — %s guilds", bot.user, bot.user.id, len(bot.guilds))
    log.info("verify=%s port=%s", REQUIRE_VERIFY, HTTP_PORT)
    # guild sync for instant commands (global ko guild me copy karke — warna guild khali rehta hai)
    for g in bot.guilds:
        try:
            try: bot.tree.copy_global_to(guild=g)
            except Exception: pass
            await bot.tree.sync(guild=g)
            log.info("guild %s (%s) synced", g.name, g.id)
        except Exception as e: log.warning("guild %s sync fail: %s", g.id, e)

@bot.event
async def on_member_join(member: discord.Member):
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

def _is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator) if interaction.guild else False

def _is_mod(interaction: discord.Interaction) -> bool:
    p = interaction.user.guild_permissions if interaction.guild else None
    return bool(p and (p.administrator or p.manage_guild or p.manage_messages or p.kick_members or p.ban_members))

async def _deny(interaction: discord.Interaction, msg="❌ Ye command sirf admins/mods ke liye hai."):
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="ping", description="Bot ka latency check karo")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`")

@bot.tree.command(name="help", description="Bot ke saare commands ki list dekho")
async def help_cmd(interaction: discord.Interaction):
    embed=discord.Embed(title="🤖 Hollow purple — Commands", color=0x9b59b6)
    embed.add_field(name="🛡️ Admin / Moderation", value="`/kick` `/ban` `/unban` `/timeout` `/untimeout` `/warn` `/warnings` `/clearwarn` `/clear` `/slowmode` `/lock` `/unlock` `/role_add` `/role_remove` `/nickname`", inline=False)
    embed.add_field(name="📢 Announcement", value="`/say` `/announce` `/dm` `/report`", inline=False)
    embed.add_field(name="ℹ️ Jankari", value="`/ping` `/botinfo` `/uptime` `/serverinfo` `/userinfo` `/avatar` `/membercount` `/roleinfo`", inline=False)
    embed.add_field(name="🎉 Utility", value="`/poll` `/remind` `/help`", inline=False)
    embed.add_field(name="🤖 AI Chat", value="`/ask sawal` ya bot ko @mention karke kuch bhi puch lo", inline=False)
    embed.add_field(name="🎨 AI Images", value="`/image kya banana hai` (tasveer) • `/poster tournament theme` (poster)", inline=False)
    embed.set_footer(text="Naya member aane pe bot khud welcome DM bhejta hai.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="botinfo", description="Bot ki jankari dekho")
async def botinfo(interaction: discord.Interaction):
    embed=discord.Embed(title="🤖 Bot Info", color=0x3498db)
    embed.add_field(name="Naam", value=str(bot.user), inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)}ms", inline=True)
    embed.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="uptime", description="Bot kitni der se online hai")
async def uptime(interaction: discord.Interaction):
    sec=int(time.time()-START_TIME); d,sec=divmod(sec,86400); h,sec=divmod(sec,3600); m,sec=divmod(sec,60)
    await interaction.response.send_message(f"⏱️ Bot online hai: **{d}d {h}h {m}m {sec}s** se", ephemeral=True)

@bot.tree.command(name="serverinfo", description="Server ki jankari dekho")
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

@bot.tree.command(name="userinfo", description="Kisi member ki jankari dekho")
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

@bot.tree.command(name="avatar", description="Kisi member ki profile photo badi karke dekho")
@app_commands.describe(member="Kiska avatar chahiye")
async def avatar(interaction: discord.Interaction, member: discord.Member):
    embed=discord.Embed(title=f"🖼️ {member.display_name} ka avatar", color=0x9b59b6)
    embed.set_image(url=member.display_avatar.with_size(1024).url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="membercount", description="Server me kul kitne members hai")
async def membercount(interaction: discord.Interaction):
    await interaction.response.send_message(f"👥 **{interaction.guild.name}** me kul **{interaction.guild.member_count}** members hai.", ephemeral=True)

@bot.tree.command(name="roleinfo", description="Kisi role ki jankari dekho")
@app_commands.describe(role="Kis role ki jankari chahiye")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed=discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="Members", value=str(len(role.members)), inline=True)
    embed.add_field(name="Position", value=str(role.position), inline=True)
    embed.add_field(name="Mentionable", value="Ha" if role.mentionable else "Nahi", inline=True)
    embed.add_field(name="Admin hai?", value="Ha" if role.permissions.administrator else "Nahi", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="say", description="Bot se message bulwao (admin)")
@app_commands.describe(text="Kya bhejna hai", channel="Kis channel me (khali = yahi channel)")
async def say(interaction: discord.Interaction, text: str, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.send(text)
    await interaction.response.send_message(f"✅ Bhej diya {ch.mention}", ephemeral=True)

@bot.tree.command(name="announce", description="Embed announcement karo (admin)")
@app_commands.describe(title="Title", description="Announcement", channel="Kis channel me (khali = yahi channel)")
async def announce(interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    embed=discord.Embed(title=title, description=description, color=0x9b59b6)
    embed.set_footer(text=f"Announcement by {interaction.user.display_name}")
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ Announce ho gaya {ch.mention}", ephemeral=True)

@bot.tree.command(name="dm", description="Kisi member ko bot se DM bhejo (admin)")
@app_commands.describe(member="Kisko DM bhejna hai", message="DM me kya bhejna hai")
async def dm_cmd(interaction: discord.Interaction, member: discord.Member, message: str):
    if not _is_mod(interaction): await _deny(interaction); return
    try:
        await member.send(f"📩 **{interaction.guild.name}** se message:\n\n{message}")
        await interaction.response.send_message(f"✅ DM gaya -> {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ {member.mention} ne DM band kiye hai.", ephemeral=True)

@bot.tree.command(name="clear", description="Channel ke messages delete karo (admin)")
@app_commands.describe(amount="Kitne messages delete karne hai (1-100)")
async def clear(interaction: discord.Interaction, amount: int):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 1 <= amount <= 100: await interaction.response.send_message("❌ 1 se 100 ke beech number do.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    deleted=await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🗑️ {len(deleted)} messages delete kiye.", ephemeral=True)

@bot.tree.command(name="slowmode", description="Channel ka slowmode set karo (admin)")
@app_commands.describe(seconds="Kitne second ka slowmode (0 = band)", channel="Kis channel me (khali = yahi)")
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    if not 0 <= seconds <= 21600: await interaction.response.send_message("❌ 0 se 21600 ke beech do.", ephemeral=True); return
    ch=channel or interaction.channel
    await ch.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"🐌 {ch.mention} ka slowmode **{seconds}s** ho gaya.", ephemeral=True)

@bot.tree.command(name="lock", description="Channel band karo — koi message nahi bhej payega (admin)")
@app_commands.describe(channel="Kis channel ko band karna hai (khali = yahi)")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    await ch.send("🔒 Ye channel band kiya gaya hai.")
    await interaction.response.send_message(f"🔒 {ch.mention} band ho gaya.", ephemeral=True)

@bot.tree.command(name="unlock", description="Band channel wapas kholo (admin)")
@app_commands.describe(channel="Kis channel ko kholna hai (khali = yahi)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _is_mod(interaction): await _deny(interaction); return
    ch=channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=None)
    await interaction.response.send_message(f"🔓 {ch.mention} khul gaya.", ephemeral=True)

@bot.tree.command(name="kick", description="Member ko server se nikalo (admin)")
@app_commands.describe(member="Kisko nikalna hai", reason="Wajah")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "koi wajah nahi"):
    if not interaction.user.guild_permissions.kick_members: await _deny(interaction); return
    try: await member.kick(reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role us member se neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"👢 {member.mention} ko kick kiya. Wajah: {reason}")

@bot.tree.command(name="ban", description="Member ko ban karo (admin)")
@app_commands.describe(member="Kisko ban karna hai", reason="Wajah")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "koi wajah nahi"):
    if not interaction.user.guild_permissions.ban_members: await _deny(interaction); return
    try: await member.ban(reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role us member se neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"🔨 {member.mention} ban ho gaya. Wajah: {reason}")

@bot.tree.command(name="unban", description="Ban hatayo user ID se (admin)")
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

@bot.tree.command(name="timeout", description="Member ko timeout (mute) karo (admin)")
@app_commands.describe(member="Kisko timeout dena hai", minutes="Kitne minute (1-40320)", reason="Wajah")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "koi wajah nahi"):
    if not interaction.user.guild_permissions.moderate_members: await _deny(interaction); return
    if not 1 <= minutes <= 40320: await interaction.response.send_message("❌ 1 se 40320 minute ke beech do.", ephemeral=True); return
    try: await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"🔇 {member.mention} ko **{minutes} minute** timeout mila. Wajah: {reason}")

@bot.tree.command(name="untimeout", description="Member ka timeout hatayo (admin)")
@app_commands.describe(member="Kiska timeout hatana hai")
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members: await _deny(interaction); return
    await member.timeout(None)
    await interaction.response.send_message(f"🔊 {member.mention} ka timeout hata diya.", ephemeral=True)

@bot.tree.command(name="warn", description="Member ko warning do (admin)")
@app_commands.describe(member="Kisko warn karna hai", reason="Wajah")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.setdefault(interaction.guild.id, {}).setdefault(member.id, []).append(reason)
    total=len(_warnings[interaction.guild.id][member.id])
    try: await member.send(f"⚠️ Tumhe **{interaction.guild.name}** me warning mili: {reason} (kul warnings: {total})")
    except discord.Forbidden: pass
    await interaction.response.send_message(f"⚠️ {member.mention} ko warning di (**kul {total}**). Wajah: {reason}")

@bot.tree.command(name="warnings", description="Member ki warnings dekho")
@app_commands.describe(member="Kis member ki warnings dekhni hai")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    lst=_warnings.get(interaction.guild.id, {}).get(member.id, [])
    if not lst: await interaction.response.send_message(f"✅ {member.mention} ki koi warning nahi hai.", ephemeral=True); return
    txt="\n".join(f"{i+1}. {r}" for i,r in enumerate(lst))
    await interaction.response.send_message(f"⚠️ **{member.display_name}** ki warnings ({len(lst)}):\n{txt}", ephemeral=True)

@bot.tree.command(name="clearwarn", description="Member ki saari warnings saaf karo (admin)")
@app_commands.describe(member="Kis member ki warnings saaf karni hai")
async def clearwarn(interaction: discord.Interaction, member: discord.Member):
    if not _is_mod(interaction): await _deny(interaction); return
    _warnings.get(interaction.guild.id, {}).pop(member.id, None)
    await interaction.response.send_message(f"✅ {member.mention} ki warnings saaf ho gayi.", ephemeral=True)

@bot.tree.command(name="role_add", description="Member ko role do (admin)")
@app_commands.describe(member="Kisko role dena hai", role="Konsa role dena hai")
async def role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles: await _deny(interaction); return
    try: await member.add_roles(role, reason=f"role_add by {interaction.user}")
    except discord.Forbidden: await interaction.response.send_message("❌ Ye role mere role se upar hai — mera role upar rakho fir hoga.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} ko **{role.name}** role mil gaya.")

@bot.tree.command(name="role_remove", description="Member se role hatayo (admin)")
@app_commands.describe(member="Kis se role hatana hai", role="Konsa role hatana hai")
async def role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles: await _deny(interaction); return
    try: await member.remove_roles(role, reason=f"role_remove by {interaction.user}")
    except discord.Forbidden: await interaction.response.send_message("❌ Ye role mere role se upar hai.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} se **{role.name}** role hata diya.")

@bot.tree.command(name="nickname", description="Member ka nickname badlo (admin)")
@app_commands.describe(member="Kis ka nickname badalna hai", name="Naya nickname (khali = purana hatao)")
async def nickname_cmd(interaction: discord.Interaction, member: discord.Member, name: str = None):
    if not interaction.user.guild_permissions.manage_nicknames: await _deny(interaction); return
    try: await member.edit(nick=name)
    except discord.Forbidden: await interaction.response.send_message("❌ Nahi ho paya — mera role neeche hai.", ephemeral=True); return
    await interaction.response.send_message(f"✅ {member.mention} ka nickname badal diya.", ephemeral=True)

@bot.tree.command(name="poll", description="Poll bano — log reaction se vote karege")
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

@bot.tree.command(name="remind", description="Reminder lagayo — bot baad me yaad dilayega")
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

async def _ask_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ AI set nahi hai — `GEMINI_API_KEY` Termux wali command me dalna bacha hai."
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    payload={
        "system_instruction": {"parts": [{"text": "Tum Hollow purple naam ke Discord bot ho. User jis bhasha me puche (Hinglish, Hindi ya English) usi me jawab do. Jawab saaf, kaam ka aur zyada se zyada 1800 characters me rakho."}]},
        "contents": [{"parts": [{"text": prompt[:4000]}]}],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                data=await resp.json()
                if resp.status != 200:
                    log.warning("Gemini HTTP %s: %s", resp.status, str(data)[:300])
                    if resp.status == 429: return "⚠️ AI ki free limit abhi khatam ho gayi hai — thodi der baad fir try karna."
                    if resp.status == 503: return "⚠️ AI abhi busy hai — ek min baad fir try karna."
                    return "⚠️ AI se jawab nahi aaya — thodi der baad fir try karna."
                return data["candidates"][0]["content"]["parts"][0]["text"][:1900]
    except Exception:
        log.error("Gemini fail\n%s", traceback.format_exc())
        return "⚠️ AI tak baat nahi pahuchi — internet ya key check karke fir try karna."

@bot.tree.command(name="ask", description="AI se kuch bhi puchho")
@app_commands.describe(sawal="Kya puchna hai")
async def ask_cmd(interaction: discord.Interaction, sawal: str):
    now=time.time()
    if now - _ask_cooldown.get(interaction.user.id, 0) < 5:
        await interaction.response.send_message("⏳ 5 second ruk ke fir pucho.", ephemeral=True); return
    _ask_cooldown[interaction.user.id]=now
    await interaction.response.defer()
    jawab=await _ask_gemini(sawal)
    await interaction.followup.send(f"**{interaction.user.display_name}:** {sawal}\n\n{jawab}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if bot.user and bot.user in message.mentions:
        sawal=message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if sawal:
            now=time.time()
            if now - _ask_cooldown.get(message.author.id, 0) >= 5:
                _ask_cooldown[message.author.id]=now
                async with message.channel.typing():
                    jawab=await _ask_gemini(sawal)
                await message.reply(jawab)
    await bot.process_commands(message)

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

@bot.tree.command(name="image", description="AI se tasveer bano — kya banana hai likho")
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

@bot.tree.command(name="poster", description="Tournament ka poster bano — title aur theme likho")
@app_commands.describe(title="Tournament ka naam/title", theme="Poster kaisa dikhe (jaise: neon purple, aag wala, jungle)")
async def poster_cmd(interaction: discord.Interaction, title: str, theme: str):
    now=time.time()
    if now - _image_cooldown.get(interaction.user.id, 0) < 15:
        await interaction.response.send_message("⏳ Poster me time lagta hai — 15 second ruk ke fir bano.", ephemeral=True); return
    _image_cooldown[interaction.user.id]=now
    await interaction.response.defer()
    try:
        data=await _make_image(f"epic esports gaming tournament poster background, {theme}, heroic gamers, dramatic cinematic lighting, space for title text, no text, no words, no letters")
    except Exception:
        log.error("poster fail\n%s", traceback.format_exc())
        await interaction.followup.send("⚠️ Poster nahi ban paya — thodi der baad fir try karna.")
        return
    embed=discord.Embed(title=f"🏆 {title}", description="Poster taiyaar hai! Naam upar embed me hai — image ko download karke Canva me title daal lena, wahi sabse saaf aayega.", color=0x9b59b6)
    embed.set_image(url="attachment://poster.jpg")
    embed.set_footer(text=f"Poster by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(data), filename="poster.jpg"))

@bot.tree.command(name="report", description="Kisi member ki report admin tak bhejo")
@app_commands.describe(member="Kis ki report karni hai", reason="Kya galti ki usne")
async def report(interaction: discord.Interaction, member: discord.Member, reason: str):
    embed=discord.Embed(title="🚨 Nayi Report", color=0xe74c3c)
    embed.add_field(name="Report kiya", value=member.mention, inline=True)
    embed.add_field(name="Report kisne kiya", value=interaction.user.mention, inline=True)
    embed.add_field(name="Wajah", value=reason, inline=False)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Report admin tak bhej di.", ephemeral=True)

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
