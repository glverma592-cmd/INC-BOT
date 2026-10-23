"""
Real bot.py ke code paths ko actually exercise karta hai (bina Discord login ke).

Coverage:
  1. module import
  2. VerifyButton() construct + persistent custom_id
  3. DM_TEXT placeholder formatting
  4. main() -> no token -> SystemExit with helpful message
  5. aiohttp handlers: GET /health, POST /github (bad secret / bad json / no channel)
  6. on_member_join -> DM blocked -> _fallback_post call hota hai ya nahi (mock)
"""
import asyncio
import json
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GITHUB_HOOK_SECRET", "s3cret")
os.environ.setdefault("VERIFY_ROLE_ID", "0")

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

import bot as botmod
import discord
from discord.ext import commands


class TestBotCode(unittest.TestCase):

    def test_1_module_imports_and_config(self):
        self.assertTrue(hasattr(botmod, "bot"))
        self.assertIsInstance(botmod.bot, commands.Bot)
        self.assertTrue(botmod.bot.intents.members, "members intent ON hona chahiye (DM ke liye zaroori)")
        self.assertTrue(botmod.bot.intents.message_content)

    def test_2_verify_button_persistent(self):
        v = botmod.VerifyButton()
        self.assertIsNone(v.timeout, "persistent view ka timeout None hona chahiye")
        ids = [c.custom_id for c in v.children]
        self.assertEqual(ids, ["autodm:verify"])
        self.assertEqual(v.children[0].label, "Verify karo")

    def test_3_dm_text_formatting(self):
        txt = botmod.DM_TEXT.format(user="<@123>", guild="MyServer", count=42)
        self.assertIn("<@123>", txt)
        self.assertIn("MyServer", txt)
        self.assertNotIn("{user}", txt)
        self.assertNotIn("{guild}", txt)

    def test_4_main_requires_token(self):
        with mock.patch.dict(os.environ, {"DISCORD_BOT_TOKEN": ""}, clear=False):
            botmod.TOKEN = ""
            with self.assertRaises(SystemExit) as ctx:
                botmod.main()
            self.assertIn("DISCORD_BOT_TOKEN", str(ctx.exception))

    def test_5_github_handler_rejects_bad_secret(self):
        self._run(self._github_cases())

    async def _github_cases(self):
        app = aiohttp.web.Application()
        app.router.add_post("/github", botmod.github_handler)
        app.router.add_get("/health", lambda r: aiohttp.web.json_response({"ok": True}))
        async with TestClient(TestServer(app)) as client:
            # health
            r = await client.get("/health")
            self.assertEqual(r.status, 200)
            self.assertEqual((await r.json())["ok"], True)

            import hashlib as _hl, hmac as _hm
            def _sign(body: bytes) -> str:
                return "sha256=" + _hm.new(b"s3cret", body, _hl.sha256).hexdigest()

            # galat signature -> andar nahi
            r = await client.post("/github", json={"text": "x"},
                                  headers={"X-Hub-Signature-256": "sha256=galat"})
            self.assertEqual(r.status, 403)

            # sahi signature, par kharab json
            _bad = b"not-json{{{"
            r = await client.post("/github", data=_bad,
                                  headers={"X-Hub-Signature-256": _sign(_bad)})
            self.assertEqual(r.status, 400)

            # sahi signature, but bot logged in nahi -> channel nahi milega
            _good = json.dumps({"text": "push to main", "title": "Push",
                                "channel_id": 1560004874580533338,
                                "event": "push"}).encode()
            r = await client.post("/github", data=_good,
                                  headers={"X-Hub-Signature-256": _sign(_good),
                                           "Content-Type": "application/json"})
            self.assertEqual(r.status, 404)
            body = await r.json()
            self.assertEqual(body["error"], "channel not found")
            print("    /github 404 body:", json.dumps(body))

    def test_6_on_member_join_fallback_when_dm_blocked(self):
        """DM Forbidden aaye -> _fallback_post call hona chahiye."""
        self._run(self._join_case())

    async def _join_case(self):
        member = mock.AsyncMock(spec=discord.Member)
        member.mention = "<@999>"
        member.id = 999
        member.guild.name = "TestGuild"
        member.guild.member_count = 7
        member.send = mock.AsyncMock(side_effect=discord.Forbidden(
            mock.MagicMock(status=403), "Cannot send messages to this user"))

        calls = []

        async def fake_fallback(m, reason):
            calls.append((m.id, reason))

        with mock.patch.object(botmod, "_fallback_post", side_effect=fake_fallback):
            await botmod.on_member_join(member)

        member.send.assert_awaited_once()
        self.assertEqual(len(calls), 1, "DM block par fallback zaroor call ho")
        self.assertEqual(calls[0][0], 999)
        self.assertIn("DM nahi", calls[0][1])
        print("    fallback reason:", calls[0][1])

    def test_7_dm_success_path(self):
        self._run(self._join_ok())

    async def _join_ok(self):
        member = mock.AsyncMock(spec=discord.Member)
        member.mention = "<@1>"
        member.id = 1
        member.guild.name = "G"
        member.guild.member_count = 2
        sent = {}

        async def fake_send(text, view=None):
            sent["text"] = text
            sent["view"] = view

        member.send = fake_send
        await botmod.on_member_join(member)
        self.assertIn("G", sent["text"])
        # VERIFY_ROLE_ID set nahi hai -> tuta hua button nahi jana chahiye (zero-setup)
        self.assertIsNone(sent["view"])
        print("    DM text preview:", sent["text"].splitlines()[0])

    def _run(self, coro):
        asyncio.run(coro)
if __name__ == "__main__":
    unittest.main(verbosity=2)
