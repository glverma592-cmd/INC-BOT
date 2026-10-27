"""Jarvis ki awaaz sunna (STT) — pehle suno, phir jo suna woh dikhao.

Tareeka:
1) termux-speech-to-text ho to wahi (Android khud 3-4 sec ki chuppi par rukta hai,
   aam awaaz ke liye bana hai — zyada sensitive nahi).
2) Warna mic se recording karke Gemini se likhwao.
Sunai de par samajh na aaye to crash nahi — saaf khaali jawab, upar wala
ek baar phir koshish karega.
"""
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return False, "", str(e)


def _gemini_transcribe(path):
    try:
        import brain
        brain._load_local_env()
    except Exception:
        pass
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return ""
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    mime = {".m4a": "audio/mp4", ".mp3": "audio/mp3", ".wav": "audio/wav",
            ".ogg": "audio/ogg", ".aac": "audio/aac"}.get(Path(path).suffix.lower(), "audio/mp4")
    try:
        data = base64.b64encode(Path(path).read_bytes()).decode()
        body = {"contents": [{"parts": [
            {"text": "Transcribe this audio exactly as spoken (Hinglish/Hindi/English). "
                     "Return ONLY the transcript text, nothing else."},
            {"inline_data": {"mime_type": mime, "data": data}}]}]}
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read().decode("utf-8"))
        return out["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return ""


def listen_once(seconds=6):
    """Ek baari suno. (suna_hua_text, note) return Karo."""
    if shutil.which("termux-speech-to-text"):
        for _ in range(2):  # pehli baar na samjhe to ek aur mauka
            ok, out, _err = _run(["termux-speech-to-text"], timeout=30)
            if ok and out:
                return out, "phone-recognizer"
            time.sleep(0.5)
        return "", "sunai diya par samajh nahi aaya"
    if shutil.which("termux-microphone-record"):
        folder = ROOT / "recordings"
        folder.mkdir(exist_ok=True)
        path = folder / f"voice-{time.strftime('%H%M%S')}.m4a"
        ok, _o, _e = _run(["termux-microphone-record", "-f", str(path), "-l", str(int(seconds))],
                          timeout=int(seconds) + 30)
        if ok and path.exists():
            text = _gemini_transcribe(path)
            if text:
                return text, "gemini-audio"
        return "", "recording bani par samajh nahi aaya"
    return "", "voice ke liye Termux:API app chahiye"
