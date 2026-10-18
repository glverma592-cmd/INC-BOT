"""
Auto-DM Discord bot  +  GitHub -> Discord bridge

Kya karta hai:
  1. Naya member join  -> DM me welcome + rules + [Verify] button
  2. Verify click      -> role milta hai (button hat jata hai, DM confirm)
  3. /dmtest @user     -> kisi ko manually DM bhejne ka command
  4. POST /github      -> GitHub webhook ya Actions is endpoint pe POST kare to channel me embed
  5. DM fail hone par  -> #welcome-channel me fallback post (privacy settings ki wajah se DM block ho)

Setup: https://github.com/... (README.md dekho) ya neeche .env.example
"""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

# --------------------------------------------------------------------------- #
#  CONFIG  (env vars / .env file)
# --------------------------------------------------------------------------- #
def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GITHUB_HOOK_SECRET = os.environ.get("GITHUB_HOOK_SECRET", "")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
VERIFY_ROLE_ID = int(os.environ.get("VERIFY_ROLE_ID", "0") or 0)
WELCOME_CHANNEL_ID = int(os.environ.get("WELCOME_CHANNEL_ID", "0") or 0)
DM_TEXT = os.environ.get(
    "DM_TEXT",
    "Hey {user} 👋\n\n**{guild}** me swagat hai!\n\n"
    "1. Pehle <#rules> padho\n2. Neeche **Verify** button dabao\n3. Tabhi baaki channels khulenge\n\n"
    "Koi dikkat? Mods ko ping kar do.",
)
REQUIRE_VERIFY = os.environ.get("REQUIRE_VERIFY", "1") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("autodm")

# --------------------------------------------------------------------------- #
#  BOT
# --------------------------------------------------------------------------- #
intents = discord.Intents.default()
intents.members = True          # PRIVILEGED — Developer Portal me ON karna zaroori
intents.message_content = True  # PRIVILEGED — ON karna zaroori

bot = commands.Bot(command_prefix="!", intents=intents)


class VerifyButton(discord.ui.View):
    """DM me bheja jata hai; click par role milta hai."""

    def __init__(self) -> None:
        super().__init__(timeout=None)  # persistent: bot restart ke baad bhi chale

    @discord.ui.button(label="Verify karo", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="autodm:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not VERIFY_ROLE_ID:
            await interaction.response.send_message(
                "⚠️ `VERIFY_ROLE_ID` set nahi hai — bot owner ko bolo.", ephemeral=True)
            return
        role = interaction.guild.get_role(VERIFY_ROLE_ID) if interaction.guild else None
        if role is None:
            await interaction.response.send_message("⚠️ Role nahi mila.", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Auto-DM verify button")
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Mere paas role dene ki permission nahi hai. Mera role **Verified** se upar hona chahiye.",
                ephemeral=True)
            return
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass
        await interaction.response.send_message(f"✅ Verified! **{role.name}** mil gaya.", ephemeral=True)


@bot.event
async def setup_hook() -> None:
    bot.add_view(VerifyButton())          # restart ke baad purane buttons zinda rakho
    await bot.tree.sync()
    log.info("slash commands synced")


@bot.event
async def on_ready() -> None:
    log.info("logged in as %s (id=%s) — %s guilds", bot.user, bot.user.id, len(bot.guilds))
    log.info("verify button enabled: %s | web port: %s", REQUIRE_VERIFY, HTTP_PORT)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """THE AUTO-DM. Naya member aate hi DM."""
    text = DM_TEXT.format(user=member.mention, guild=member.guild.name, count=member.guild.member_count)
    view = VerifyButton() if REQUIRE_VERIFY else None
    try:
        await member.send(text, view=view)
        log.info("DM sent -> %s (%s)", member, member.id)
    except discord.Forbidden:
        log.warning("DM blocked by %s — fallback channel post", member)
        await _fallback_post(member, "DM nahi bhej paya (user ne server DMs band kiye hue hain)")
    except discord.HTTPException:
        log.error("DM failed for %s:\n%s", member, traceback.format_exc())
        await _fallback_post(member, "DM bhejte waqt error aaya")


async def _fallback_post(member: discord.Member, reason: str) -> None:
    if not WELCOME_CHANNEL_ID:
        return
    ch = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if ch is None:
        return
    text = DM_TEXT.format(user=member.mention, guild=member.guild.name, count=member.guild.member_count)
    try:
        await ch.send(text, view=VerifyButton() if REQUIRE_VERIFY else None)
    except discord.HTTPException:
        log.error("fallback post failed: %s", reason)


@bot.tree.command(name="dmtest", description="Kisi member ko test DM bhejo (admin)")
@app_commands.describe(member="Jisko DM bhejna hai")
async def dmtest(interaction: discord.Interaction, member: discord.Member) -> None:
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Sirf admins ke liye.", ephemeral=True)
        return
    try:
        await member.send(DM_TEXT.format(user=member.mention, guild=member.guild.name,
                                         count=member.guild.member_count),
                          view=VerifyButton() if REQUIRE_VERIFY else None)
        await interaction.response.send_message(f"✅ DM gaya -> {member.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ {member.mention} ne DMs block kiye hue hain.", ephemeral=True)


# --------------------------------------------------------------------------- #
#  GitHub -> Discord  (POST /github)
# --------------------------------------------------------------------------- #
async def github_handler(request: web.Request) -> web.Response:
    if GITHUB_HOOK_SECRET and request.headers.get("X-Hub-Secret") != GITHUB_HOOK_SECRET:
        return web.json_response({"error": "bad secret"}, status=401)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    guild_id = int(payload.get("guild_id") or 0)
    channel_id = int(payload.get("channel_id") or 0)
    text = str(payload.get("text") or "GitHub event")
    title = str(payload.get("title") or "GitHub")
    url = str(payload.get("url") or "")
    color = int(payload.get("color") or 3447003)

    ch = bot.get_channel(channel_id) if channel_id else None
    if ch is None and guild_id:
        g = bot.get_guild(guild_id)
        ch = g.get_channel(WELCOME_CHANNEL_ID) if g else None
    if ch is None:
        return web.json_response({"error": "channel not found"}, status=404)

    embed = discord.Embed(title=title, description=text, color=color, url=url or None)
    embed.set_footer(text=f"github • {payload.get('event', 'webhook')}")
    await ch.send(embed=embed)
    log.info("github -> #%s", ch)
    return web.json_response({"ok": True, "channel": ch.id})


async def start_http_server() -> None:
    app = web.Application()
    app.router.add_post("/github", github_handler)
    app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    log.info("HTTP server listening on 0.0.0.0:%s  (POST /github, GET /health)", HTTP_PORT)


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN set nahi hai.\n"
            "  cp .env.example .env  -> token daalo  ->  python bot.py\n"
            "Token yahan se: https://discord.com/developers/applications -> app -> Bot -> Reset Token"
        )

    async def _run() -> None:
        await start_http_server()
        await bot.start(TOKEN)

    import asyncio
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
