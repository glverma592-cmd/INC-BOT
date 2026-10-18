"""
INC Bot — Auto-DM + Guild management
Naya member -> DM + Verify button, /dmtest, /ping, /say, /announce, /clear + GitHub -> Discord bridge
"""
from __future__ import annotations
import logging, os, traceback
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
    # guild sync for instant commands
    for g in bot.guilds:
        try:
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
@bot.tree.command(name="ping", description="Bot ka latency check karo")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`")

@bot.tree.command(name="dmtest", description="Kisi member ko test DM bhejo (admin)")
@app_commands.describe(member="Jisko DM bhejna hai")
async def dmtest(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Sirf admins ke liye.", ephemeral=True); return
    try:
        await member.send(DM_TEXT.format(user=member.mention, guild=member.guild.name, count=member.guild.member_count), view=VerifyButton() if REQUIRE_VERIFY else None)
        await interaction.response.send_message(f"✅ DM gaya -> {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"❌ {member.mention} ne DMs block kiye hai.", ephemeral=True)

@bot.tree.command(name="say", description="Bot se kuch bulwao (admin)")
@app_commands.describe(text="Kya bhejna hai", channel="Kis channel me (khali = yahi)")
async def say(interaction: discord.Interaction, text: str, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("Permission nahi hai.", ephemeral=True); return
    ch=channel or interaction.channel
    await ch.send(text)
    await interaction.response.send_message(f"✅ Bhej diya {ch.mention}", ephemeral=True)

@bot.tree.command(name="announce", description="Embed announce karo (admin)")
@app_commands.describe(title="Title", description="Description", channel="Channel")
async def announce(interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Sirf admins.", ephemeral=True); return
    ch=channel or interaction.channel
    embed=discord.Embed(title=title, description=description, color=0x9b59b6)
    embed.set_footer(text=f"by {interaction.user.display_name}")
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ Announce ho gaya {ch.mention}", ephemeral=True)

@bot.tree.command(name="clear", description="Messages delete karo (admin)")
@app_commands.describe(amount="Kitne messages (1-100)")
async def clear(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("Permission nahi.", ephemeral=True); return
    if not 1 <= amount <= 100:
        await interaction.response.send_message("1-100 ke beech do.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    deleted=await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🗑️ {len(deleted)} messages delete kiye.", ephemeral=True)

@bot.tree.command(name="welcome_test", description="Apne aap ko welcome DM test bhejo")
async def welcome_test(interaction: discord.Interaction):
    text=DM_TEXT.format(user=interaction.user.mention, guild=interaction.guild.name, count=interaction.guild.member_count)
    try:
        await interaction.user.send(text, view=VerifyButton() if REQUIRE_VERIFY else None)
        await interaction.response.send_message("✅ DM bhej diya — check karo!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Tera DM block hai — Privacy me `Allow direct messages from server members` ON kar.", ephemeral=True)

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
