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


ALLOWED_ACTIONS = {
    "speak", "toast", "notify", "torch_on", "torch_off", "battery",
    "wifi", "location", "vibrate", "brightness", "open_url", "open_app",
    "photo", "device_info", "time", "date", "remember", "none",
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


def gemini_plan(text, memory_facts=None):
    """Gemini se soch-wala jawab/action. Key na ho to None."""
    _load_local_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    facts = "; ".join(memory_facts or []) or "koi saved fact nahi"
    own = load_own_brain()
    persona = own.get("persona") or "You are Jarvis, ARISE's personal phone assistant."
    extra = ""
    if own.get("knowledge"):
        extra += "\n\nJarvis ki apni knowledge (isi ko sabse pehle mano):\n" + own["knowledge"]
    if own.get("qa"):
        examples = []
        for item in own["qa"][:12]:
            for q in item.get("questions", [])[:1]:
                examples.append(f"Q: {q} -> A: {item.get('answer', '')}")
        if examples:
            extra += "\n\nSikhe hue jawab:\n" + "\n".join(examples)
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


def plan(text, memory_facts=None):
    """Pehle rule, phir Gemini, phir seedha fallback."""
    p = rule_plan(text)
    if p:
        return p
    q = qa_plan(text)  # apna sikha hua dimag — internet se pehle
    if q:
        return q
    g = gemini_plan(text, memory_facts)
    if g:
        return g
    return {"action": "none", "args": {},
            "reply": "Samajh nahi aaya. Example: 'torch on', 'battery batao', "
                     "'YouTube kholo', '10 minute baad yaad dilana ki match hai'."}
