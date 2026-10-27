"""Jarvis <-> Nezu (Discord) bridge — Jarvis ek NASHE me Nezu se baat karta hai.

2 raaste:
1) Discord -> Jarvis: Nezu ka /tools jarvis command shared/jarvis_inbox.jsonl
   me line likhta hai; Jarvis use padhkar kaam karke jarvis_outbox.jsonl me
   jawab likhta hai, Nezu wahi jawab Discord me bhejti hai.
2) Jarvis -> Discord: JARVIS_DISCORD_WEBHOOK_URL (.env me ho to) par
   Jarvis seedha Discord channel me message bhej sakta hai.
   Webhook URL kabhi GitHub/chat me mat daalna — sirf Nezu ki .env me.

Suraksha: inbox sirf Nezu owner-command se bhar sakta hai, aur Jarvis
sirf allowlist wale kaam karta hai.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHARED = ROOT.parent / "shared"
INBOX = SHARED / "jarvis_inbox.jsonl"
OUTBOX = SHARED / "jarvis_outbox.jsonl"


def send_to_discord(text):
    """Webhook ho to Discord channel me Jarvis ke naam se message."""
    url = os.environ.get("JARVIS_DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return False
    try:
        body = json.dumps({"content": str(text)[:1900], "username": "Jarvis"}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _read_lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def watch_inbox(handle, poll=2):
    """Background thread: Nezu se aaye command chalao, jawab outbox me likho."""
    SHARED.mkdir(parents=True, exist_ok=True)
    seen = set()
    for line in _read_lines(INBOX):  # purane command dobara mat chalao
        try:
            seen.add(json.loads(line).get("id"))
        except Exception:
            pass
    while True:
        try:
            for line in _read_lines(INBOX):
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                rid = item.get("id")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                cmd = str(item.get("cmd", "")).strip()
                try:
                    result = handle(cmd) if cmd else "Khaali command aaya."
                except Exception as e:
                    result = f"⚠️ Jarvis error: {e}"
                with OUTBOX.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": rid, "result": str(result)[:1800],
                                        "ts": time.time()}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        time.sleep(poll)
