"""Jarvis ka dimag: rule-parser (turant kaam) + Gemini AI (soch wala kaam).

Rule pehle chalta hai taaki torch/battery jaise kaam bina internet ke ho.
Gemini sirf tab jab GEMINI_API_KEY env me ho aur rule na mile.
Gemini se sirf allowlist action hi chal sakta hai — kuch bhi ulta nahi.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

def _load_local_env():
    """Key chat/code me nahi — Nezu ki .env se khud le lo (ek hi Termux me hai).

    Jarvis agar INC-BOT/jarvis me hai to Nezu ki key ../bot/.env me milti hai.
    Pehle se set env ko kabhi badla nahi jata.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent
    candidates = [root / ".env", root.parent / "bot" / ".env",
                  root.parent / ".env", Path.cwd() / ".env"]
    for p in candidates:
        try:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            continue

# ---------------- Jarvis ka apna (editable) dimag ----------------
from pathlib import Path as _Path

_BRAIN_ROOT = _Path(__file__).resolve().parent


def _brain_config():
    try:
        return json.loads((_BRAIN_ROOT / "brain_config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_own_brain():
    """persona + knowledge + sikhe hue Q&A padho (har baar taza, edit turant lagu)."""
    cfg = _brain_config()
    out = {"persona": "", "knowledge": "", "qa": []}
    if not cfg.get("own_brain_enabled", True):
        return out
    try:
        out["persona"] = (_BRAIN_ROOT / cfg.get("persona_file", "my_brain/persona.txt")).read_text(encoding="utf-8").strip()
    except Exception:
        pass
    try:
        limit = int(cfg.get("knowledge_char_limit", 6000))
        out["knowledge"] = (_BRAIN_ROOT / cfg.get("knowledge_file", "my_brain/knowledge.md")).read_text(encoding="utf-8")[:limit]
    except Exception:
        pass
    try:
        out["qa"] = json.loads((_BRAIN_ROOT / cfg.get("qa_file", "my_brain/qa.json")).read_text(encoding="utf-8"))
    except Exception:
        pass
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", str(s).lower()).split()


def qa_plan(text):
    """Pehle sikha hua jawab dhundo — mile to bina internet turant jawab."""
    own = load_own_brain()
    t = _norm(text)
    if not t:
        return None
    tset = set(t)
    for item in own.get("qa", []):
        ans = str(item.get("answer", "")).strip()
        if not ans:
            continue
        for q in item.get("questions", []):
            qt = _norm(q)
            if not qt:
                continue
            if qt == t or set(qt).issubset(tset):
                return {"action": "none", "args": {}, "reply": ans}
            overlap = len(set(qt) & tset) / max(1, len(set(qt)))
            if overlap >= 0.85:
                return {"action": "none", "args": {}, "reply": ans}
    return None


def load_language():
    """ARISE ne jo bhasha chuni hai (language.json), warna Hinglish."""
    try:
        return json.loads((_BRAIN_ROOT / "language.json").read_text(encoding="utf-8")).get("language", "Hinglish")
    except Exception:
        return "Hinglish"


ALLOWED_ACTIONS = {
    "speak", "toast", "notify", "torch_on", "torch_off", "battery",
    "wifi", "wifi_on", "wifi_off", "location", "vibrate", "brightness", "open_url", "open_app",
    "photo", "device_info", "time", "date", "remember", "none",
    "volume", "media_play", "media_pause", "media_resume", "media_stop",
    "clipboard_get", "clipboard_set", "download", "wallpaper", "record",
    "set_language", "ai_image", "ai_video",
}

APP_HINTS = {
    "youtube": "YouTube", "whatsapp": "WhatsApp", "instagram": "Instagram",
    "chrome": "Chrome", "camera": "Camera", "settings": "Settings",
    "telegram": "Telegram", "spotify": "Spotify", "gmail": "Gmail",
    "maps": "Maps", "gallery": "Gallery", "photos": "Photos",
    "clock": "Clock", "calculator": "Calculator", "play store": "Play Store",
}


def rule_plan(text):
    """Hinglish/English aam command pehchano. Mile to dict, warna None."""
    t = " " + text.lower().strip() + " "

    # sikhana: "seekho: sawaal => jawab" — apna dimag yahin banta hai
    m = re.match(r"(?:seekho|sikhado|train)\s*:\s*(.+?)\s*=>\s*(.+)", text.strip(), re.I | re.S)
    if m and _brain_config().get("allow_learning_commands", True):
        return {"action": "learn_qa",
                "args": {"question": m.group(1).strip(), "answer": m.group(2).strip()},
                "reply": "Seekh liya."}

    # purani baatein dekhna / sab bhoolna
    if any(w in t for w in [" sab bhool jao", " sab kuch bhool", " memory saaf", " baatein bhool"]):
        return {"action": "forget_all", "args": {}, "reply": "Thik hai, purani saari baatein aur yaadein saaf kar raha hu."}
    if any(w in t for w in [" purani baat", " pehle kya kaha", " last baat", " Pichli baat"]):
        return {"action": "history", "args": {}, "reply": ""}

    # bhasha badlo: "Hindi me baat Karo" / "English me bolo"
    m = re.search(r"(hindi|english|hinglish|japanese|marathi|bengali|tamil|telugu|punjabi|urdu)\s+(?:me|mein)\s+(?:baat|bolo|jawab)", text, re.I)
    if m:
        return {"action": "set_language", "args": {"language": m.group(1).capitalize()},
                "reply": ""}
    m = re.search(r"(?:speak|talk|reply)\s+in\s+(hindi|english|hinglish|japanese|marathi|bengali|tamil|telugu|punjabi|urdu)", text, re.I)
    if m:
        return {"action": "set_language", "args": {"language": m.group(1).capitalize()},
                "reply": ""}

    # AI image / AI video banana
    m = re.match(r"(?:ai\s+)?(?:image|photo|tasveer|picture)\s+(?:banao|bana do|bana|generate|create)\s*[:\-]?\s*(.+)", text.strip(), re.I | re.S)
    if m:
        return {"action": "ai_image", "args": {"prompt": m.group(1).strip()}, "reply": "Image bana raha hu."}
    m = re.match(r"(?:ai\s+)?video\s+(?:banao|bana do|bana|generate|create)\s*[:\-]?\s*(.+)", text.strip(), re.I | re.S)
    if m:
        return {"action": "ai_video", "args": {"prompt": m.group(1).strip()}, "reply": "Video bana raha hu."}

    # yaad rakho
    m = re.search(r"yaad rakho[:\-]?\s*(.+)", text, re.I)
    if m:
        return {"action": "remember", "args": {"fact": m.group(1).strip()},
                "reply": "Yaad kar liya."}
    m = re.search(r"mera naam\s+(\w+)\s+hai", text, re.I)
    if m:
        return {"action": "remember", "args": {"fact": f"User ka naam {m.group(1)} hai"},
                "reply": f"Samajh gaya, {m.group(1)}!"}

    # reminder: "10 minute baad yaad dilana ki ..."
    m = re.search(r"(\d+)\s*(minute|min|second|sec|ghante|hour)\s*(baad|later).*?(yaad dilana|remind|bata dena)[:\-]?\s*(.*)", text, re.I)
    if m:
        num = int(m.group(1))
        unit = m.group(2).lower()
        secs = num * (3600 if unit.startswith(("gh", "hour")) else 60 if unit.startswith("min") else 1)
        return {"action": "reminder", "args": {"seconds": secs, "text": m.group(5).strip() or "Reminder!"},
                "reply": f"Thik hai, {num} {unit} baad yaad dila dunga."}

    if any(w in t for w in [" torch on", " flashlight on", " torch chalu", " light chalu", " light on"]):
        return {"action": "torch_on", "args": {}, "reply": "Torch on kar raha hu."}
    if any(w in t for w in [" torch off", " flashlight off", " torch band", " light band", " light off"]):
        return {"action": "torch_off", "args": {}, "reply": "Torch off kar raha hu."}
    if "battery" in t:
        return {"action": "battery", "args": {}, "reply": ""}
    if any(w in t for w in [" wifi on", " wifi chalu"]):
        return {"action": "wifi_on", "args": {}, "reply": "WiFi on kar raha hu."}
    if any(w in t for w in [" wifi off", " wifi band"]):
        return {"action": "wifi_off", "args": {}, "reply": "WiFi off kar raha hu."}
    if "wifi" in t:
        return {"action": "wifi", "args": {}, "reply": ""}
    if "location" in t or " main kahan" in t or " mai kahan" in t:
        return {"action": "location", "args": {}, "reply": ""}
    if "vibrate" in t:
        return {"action": "vibrate", "args": {}, "reply": "Vibrate kar raha hu."}
    m = re.search(r"brightness\s*(?:ko\s*)?(\d+)", text, re.I)
    if m:
        return {"action": "brightness", "args": {"level": int(m.group(1)) * 255 // 100},
                "reply": "Brightness set kar raha hu."}
    if "photo" in t and any(w in t for w in [" lo", " kheecho", " click", " le "]):
        return {"action": "photo", "args": {}, "reply": "Photo le raha hu."}
    if "device info" in t or "phone ki jankari" in t:
        return {"action": "device_info", "args": {}, "reply": ""}
    if re.search(r"\b(time|samay|baj[ae])\b", t) and "video" not in t:
        return {"action": "time", "args": {}, "reply": ""}
    if re.search(r"\b(date|tarikh|aaj ka din)\b", t):
        return {"action": "date", "args": {}, "reply": ""}

    # --- naye permissions wale kaam ---
    m = re.match(r"(?:bolo|bol|speak)\s*[:\-]?\s+(.+)", text.strip(), re.I | re.S)
    if m:
        return {"action": "speak", "args": {"text": m.group(1).strip()}, "reply": ""}
    if any(w in t for w in [" wifi on", " wifi chalu"]):
        return {"action": "wifi_on", "args": {}, "reply": "WiFi on kar raha hu."}
    if any(w in t for w in [" wifi off", " wifi band"]):
        return {"action": "wifi_off", "args": {}, "reply": "WiFi off kar raha hu."}
    m = re.search(r"(?:volume|awaz|aawaz)\s*(?:ko\s*)?(\d+)", text, re.I)
    if m:
        return {"action": "volume", "args": {"level": int(m.group(1))},
                "reply": "Volume set kar raha hu."}
    if any(w in t for w in [" gaana roko", " music roko", " music band", " media stop"]):
        return {"action": "media_stop", "args": {}, "reply": "Music rok raha hu."}
    if any(w in t for w in [" gaana roko", " music roko", " pause karo", " media pause"]):
        return {"action": "media_pause", "args": {}, "reply": "Music roka."}
    if any(w in t for w in [" gaana chalao wapas", " resume karo", " media resume"]):
        return {"action": "media_resume", "args": {}, "reply": "Music wapas chalu."}
    m = re.search(r"(/[\w./\- ]+\.(?:mp3|m4a|wav|mp4))", text)
    if m and any(w in t for w in ["chalao", " play", " bajao"]):
        return {"action": "media_play", "args": {"path": m.group(1).strip()},
                "reply": "Chala raha hu."}
    m = re.search(r"(?:copy karo|clipboard me dalo|clipboard me daalo)[:\-]?\s*(.+)", text, re.I | re.S)
    if m:
        return {"action": "clipboard_set", "args": {"text": m.group(1).strip()}, "reply": "Copy kar raha hu."}
    if "clipboard" in t and any(w in t for w in ["batao", "kya hai", "dikhao", "padho"]):
        return {"action": "clipboard_get", "args": {}, "reply": ""}
    m = re.search(r"(https?://\S+)", text)
    if m and "download" in t:
        return {"action": "download", "args": {"url": m.group(1)}, "reply": "Download kar raha hu."}
    m = re.search(r"wallpaper\s*(?:lagao|set karo|badlo)?\s*[:\-]?\s*(/[\w./\- ]+\.(?:jpg|jpeg|png))", text, re.I)
    if m:
        return {"action": "wallpaper", "args": {"path": m.group(1).strip()}, "reply": "Wallpaper laga raha hu."}
    m = re.search(r"(\d+)\s*(?:second|sec)\s*(?:ki\s*)?(?:recording|record)", text, re.I)
    if m or "voice record" in t:
        return {"action": "record", "args": {"seconds": int(m.group(1)) if m else 5},
                "reply": "Recording shuru kar raha hu."}

    # app kholo — "YouTube kholo" bhi, "kholo YouTube" bhi
    if any(w in t for w in [" kholo", " open", " chalao", " start"]):
        for key, label in APP_HINTS.items():
            if key in t:
                return {"action": "open_app", "args": {"name": label},
                        "reply": f"{label} khol raha hu."}
    m = re.search(r"(?:kholo|open|chalao|start karo)\s+(.+)", text, re.I)
    if m:
        what = m.group(1).strip().lower().rstrip(" .")
        for key, label in APP_HINTS.items():
            if key in what:
                return {"action": "open_app", "args": {"name": label},
                        "reply": f"{label} khol raha hu."}
        if "." in what:  # website lagi
            return {"action": "open_url", "args": {"url": what.split()[0]},
                    "reply": "Website khol raha hu."}

    # website kholo
    m = re.search(r"([\w-]+\.(?:com|in|org|net|io)[\w/.-]*)", text)
    if m and any(w in t for w in ["kholo", "open", " khol"]):
        return {"action": "open_url", "args": {"url": m.group(1)}, "reply": "Khol raha hu."}

    return None


def gemini_plan(text, memory_facts=None, history=None):
    """Gemini se soch-wala jawab/action. Key na ho to None."""
    _load_local_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    facts = "; ".join(memory_facts or []) or "koi saved fact nahi"
    own = load_own_brain()
    persona = own.get("persona") or "You are Jarvis, ARISE's personal phone assistant."
    extra = f"\n\nReply language: {load_language()}. Hamesha isi bhasha me jawab do, " \
            "jab tak user dusri bhasha na mange. Commands ke naam waise hi rakho."
    if own.get("knowledge"):
        extra += "\n\nJarvis ki apni knowledge (isi ko sabse pehle mano):\n" + own["knowledge"]
    if own.get("qa"):
        examples = []
        for item in own["qa"][:12]:
            for q in item.get("questions", [])[:1]:
                examples.append(f"Q: {q} -> A: {item.get('answer', '')}")
        if examples:
            extra += "\n\nSikhe hue jawab:\n" + "\n".join(examples)
    if history:
        lines = []
        for h in history[-24:]:
            who = "ARISE" if h.get("role") == "user" else "Jarvis"
            lines.append(f"{who}: {str(h.get('text', ''))[:300]}")
        if lines:
            extra += ("\n\nPichli baat-cheet (purani baatein yaad rakhna — user purani "
                      "baat ka zikr kare to yahi use karna):\n" + "\n".join(lines))
    system = (
        persona + extra + "\n\nSaved facts: " + facts + ". "
        "Agar kaam phone action hai to SIRF in actions me se ek chuno: "
        + ", ".join(sorted(ALLOWED_ACTIONS)) +
        ". Warna action 'none' rakho aur sirf jawab do. "
        "Jawab SIRF is JSON me do, aur kuch nahi: "
        '{"reply": "...", "action": "none", "args": {}} '
        "args examples: open_app -> {\"name\": \"YouTube\"}, open_url -> {\"url\": \"...\"}, "
        "brightness -> {\"level\": 0-255}, notify -> {\"title\": \"..\", \"text\": \"..\"}, "
        "speak/toast -> {\"text\": \"..\"}. "
        "Password, OTP, payment, delete, message bhejna — in par action hamesha 'none' "
        "aur reply me batao ki pehle user confirm karega."
    )
    body = {"contents": [{"parts": [{"text": system + "\n\nUser: " + text}]}]}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        plan = json.loads(raw)
        if plan.get("action") not in ALLOWED_ACTIONS:
            plan["action"] = "none"
        plan.setdefault("args", {})
        plan.setdefault("reply", "")
        return plan
    except Exception:
        return None


def plan(text, memory_facts=None, history=None):
    """Pehle rule, phir apna Q&A, phir Gemini (purani baato ke saath)."""
    p = rule_plan(text)
    if p:
        return p
    q = qa_plan(text)  # apna sikha hua dimag — internet se pehle
    if q:
        return q
    g = gemini_plan(text, memory_facts, history)
    if g:
        return g
    return {"action": "none", "args": {},
            "reply": "Samajh nahi aaya. Example: 'torch on', 'battery batao', "
                     "'YouTube kholo', '10 minute baad yaad dilana ki match hai'."}
