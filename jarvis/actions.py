"""Phone actions for Jarvis — sirf allowlist wale safe kaam.

Har action Termux:API command try karta hai. Termux nahi hai to
saaf message deta hai, crash nahi karta.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(cmd, timeout=20):
    """Command chalao; (ok, output) return Karo."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or r.stderr or "").strip()
        return r.returncode == 0, out
    except Exception as e:
        return False, str(e)


def _has(name):
    return shutil.which(name) is not None


def speak(text):
    text = str(text)[:400]
    if _has("termux-tts-speak"):
        ok, _ = _run(["termux-tts-speak", text], timeout=30)
        if ok:
            return "🔊 Bola gaya."
    return f"🤖 {text}"


def toast(text):
    if _has("termux-toast"):
        _run(["termux-toast", str(text)[:200]])
    return f"💬 {text}"


def notify(title, text):
    if _has("termux-notification"):
        _run(["termux-notification", "-t", str(title)[:60], "-c", str(text)[:300]])
        return "🔔 Notification bhej diya."
    return f"🔔 {title}: {text}"


def torch(on: bool):
    if _has("termux-torch"):
        ok, out = _run(["termux-torch", "on" if on else "off"])
        return "🔦 Torch ON." if ok and on else ("🔦 Torch OFF." if ok else f"⚠️ Torch fail: {out}")
    return "⚠️ termux-torch nahi mila — Termux:API app install Karo."


def battery():
    if _has("termux-battery-status"):
        ok, out = _run(["termux-battery-status"])
        if ok:
            try:
                d = json.loads(out)
                return f"🔋 Battery {d.get('percentage', '?')}% — {d.get('status', '')}, {d.get('plugged', '')}"
            except Exception:
                return out
    return "⚠️ Battery info ke liye Termux:API chahiye."


def wifi_info():
    if _has("termux-wifi-connectioninfo"):
        ok, out = _run(["termux-wifi-connectioninfo"])
        if ok:
            try:
                d = json.loads(out)
                return f"📶 WiFi: {d.get('ssid', '?')} | IP: {d.get('ip', '?')} | Speed: {d.get('link_speed_mbps', '?')} Mbps"
            except Exception:
                return out
    return "⚠️ WiFi info ke liye Termux:API chahiye."


def location():
    if _has("termux-location"):
        ok, out = _run(["termux-location", "-p", "gps", "-r", "once"], timeout=40)
        if ok:
            try:
                d = json.loads(out)
                return f"📍 Lat {d.get('latitude')}, Lon {d.get('longitude')} (±{d.get('accuracy', '?')}m)"
            except Exception:
                return out
    return "⚠️ Location ke liye Termux:API + location permission chahiye."


def vibrate(seconds=1):
    if _has("termux-vibrate"):
        _run(["termux-vibrate", "-d", str(int(seconds * 1000))])
    return "📳 Vibrate kiya."


def brightness(level):
    level = max(0, min(255, int(level)))
    if _has("termux-brightness"):
        ok, out = _run(["termux-brightness", str(level)])
        return f"💡 Brightness {level} set." if ok else f"⚠️ {out}"
    return "⚠️ Brightness ke liye Termux:API chahiye."


def open_url(url):
    url = str(url).strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    if _has("termux-open"):
        _run(["termux-open", url])
        return f"🌐 Khol raha hu: {url}"
    import webbrowser
    webbrowser.open(url)
    return f"🌐 Khol raha hu: {url}"


# Sirf ye apps khul sakte hai (package naam ke saath)
APP_PACKAGES = {
    "youtube": "com.google.android.youtube",
    "whatsapp": "com.whatsapp",
    "instagram": "com.instagram.android",
    "chrome": "com.android.chrome",
    "camera": "com.android.camera",
    "settings": "com.android.settings",
    "play store": "com.android.vending",
    "playstore": "com.android.vending",
    "telegram": "org.telegram.messenger",
    "spotify": "com.spotify.music",
    "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "gallery": "com.google.android.apps.photos",
    "photos": "com.google.android.apps.photos",
    "file manager": "com.google.android.documentsui",
    "clock": "com.google.android.deskclock",
    "calculator": "com.google.android.calculator",
}


def open_app(name):
    key = str(name).lower().strip()
    pkg = APP_PACKAGES.get(key)
    if not pkg:
        return f"⚠️ '{name}' meri app list me nahi hai. List: {', '.join(sorted(APP_PACKAGES))}"
    # Termux se app kholne ke 2 tareeqe try Karo
    ok, out = _run(["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], timeout=15)
    if ok:
        return f"📱 {name} khol raha hu."
    ok, out = _run(["am", "start", "-n", pkg], timeout=15)
    if ok:
        return f"📱 {name} khol raha hu."
    return ("⚠️ Termux se app nahi khula. MacroDroid trigger use Karo "
            f"(README dekho) — package: {pkg}")


def take_photo(path=None):
    if not _has("termux-camera-photo"):
        return "⚠️ Camera ke liye Termux:API chahiye."
    if not path:
        folder = ROOT / "photos"
        folder.mkdir(exist_ok=True)
        path = str(folder / f"photo-{time.strftime('%Y%m%d-%H%M%S')}.jpg")
    ok, out = _run(["termux-camera-photo", path], timeout=30)
    return f"📸 Photo save: {path}" if ok else f"⚠️ Photo fail: {out}"


def device_info():
    if _has("termux-telephony-deviceinfo"):
        ok, out = _run(["termux-telephony-deviceinfo"])
        if ok:
            return out[:500]
    import platform
    return f"🖥️ {platform.system()} {platform.machine()} | Python chalu hai."


def volume_percent(pct):
    """Volume 0-100% set Karo (music stream)."""
    pct = max(0, min(100, int(pct)))
    if not _has("termux-volume"):
        return "⚠️ Volume ke liye Termux:API chahiye."
    ok, out = _run(["termux-volume"])
    maxv = 15
    if ok:
        try:
            for s in json.loads(out):
                if s.get("stream") == "music":
                    maxv = int(s.get("max_volume", 15))
        except Exception:
            pass
    level = round(maxv * pct / 100)
    ok, out = _run(["termux-volume", "music", str(level)])
    return f"🔊 Volume {pct}% kar diya." if ok else f"⚠️ Volume fail: {out}"


def wifi_enable(on: bool):
    if _has("termux-wifi-enable"):
        ok, out = _run(["termux-wifi-enable", "true" if on else "false"])
        return ("📶 WiFi ON." if on else "📶 WiFi OFF.") if ok else f"⚠️ WiFi fail: {out}"
    return "⚠️ WiFi on/off ke liye Termux:API chahiye."


def media_play(path):
    if _has("termux-media-player"):
        ok, out = _run(["termux-media-player", "play", str(path)])
        return f"🎵 Chal raha hai: {path}" if ok else f"⚠️ Play fail: {out}"
    return "⚠️ Media player ke liye Termux:API chahiye."


def media_control(what):
    if _has("termux-media-player"):
        ok, out = _run(["termux-media-player", what])
        return f"🎵 Media {what}." if ok else f"⚠️ {out}"
    return "⚠️ Media player ke liye Termux:API chahiye."


def clipboard_set(text):
    if _has("termux-clipboard-set"):
        ok, out = _run(["termux-clipboard-set", str(text)[:2000]])
        return "📋 Clipboard me copy kar diya." if ok else f"⚠️ {out}"
    return "⚠️ Clipboard ke liye Termux:API chahiye."


def clipboard_get():
    if _has("termux-clipboard-get"):
        ok, out = _run(["termux-clipboard-get"])
        return f"📋 Clipboard: {out[:500]}" if ok else "📋 Clipboard khaali hai ya permission nahi."
    return "⚠️ Clipboard ke liye Termux:API chahiye."


def download_file(url):
    url = str(url).strip()
    if not re.match(r"^https?://", url):
        return "⚠️ Sahi link do (https:// se shuru)."
    if _has("termux-download"):
        ok, out = _run(["termux-download", url], timeout=120)
        return "⬇️ Download shuru — Download folder me milega." if ok else f"⚠️ Download fail: {out}"
    try:
        import urllib.request
        folder = ROOT / "downloads"
        folder.mkdir(exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1] or "file.bin")
        path = folder / name
        urllib.request.urlretrieve(url, path)
        return f"⬇️ Download ho gaya: {path}"
    except Exception as e:
        return f"⚠️ Download fail: {e}"


def set_wallpaper(path):
    if _has("termux-wallpaper"):
        ok, out = _run(["termux-wallpaper", "-f", str(path)], timeout=60)
        return "🖼️ Wallpaper lag gaya." if ok else f"⚠️ Wallpaper fail: {out}"
    return "⚠️ Wallpaper ke liye Termux:API chahiye."


def record_audio(seconds=5):
    seconds = max(1, min(120, int(seconds)))
    if not _has("termux-microphone-record"):
        return "⚠️ Recording ke liye Termux:API chahiye."
    folder = ROOT / "recordings"
    folder.mkdir(exist_ok=True)
    path = str(folder / f"rec-{time.strftime('%Y%m%d-%H%M%S')}.m4a")
    ok, out = _run(["termux-microphone-record", "-f", path, "-l", str(seconds)],
                   timeout=seconds + 30)
    return f"🎙️ Recording save: {path}" if ok else f"⚠️ Recording fail: {out}"
