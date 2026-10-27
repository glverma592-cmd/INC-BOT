#!/usr/bin/env python3
"""Nezu-style personal Jarvis — Termux me chalane ke liye.

Use:
  python jarvis.py                  -> type karke baat Karo
  python jarvis.py --once "torch on" -> ek command (MacroDroid ke liye)
  python jarvis.py --serve           -> background server (phone-wide triggers)
  python jarvis.py --self-check      -> system check report
  python jarvis.py video in.mp4 --shorts --duration 30

GEMINI_API_KEY env me ho to soch-wale sawal Gemini se; warna rule-engine.
"""
import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import actions
import brain
import video_editor

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
MEMORY_FILE = ROOT / "memory.json"
REMINDERS_FILE = ROOT / "reminders.json"
LOG_FILE = ROOT / "jarvis.log"


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


_reminders = load_json(REMINDERS_FILE, [])


def check_reminders():
    now = time.time()
    due = [r for r in _reminders if r.get("at", 0) <= now]
    for r in due:
        _reminders.remove(r)
        actions.notify("⏰ Jarvis Reminder", r.get("text", "Reminder"))
        actions.speak(f"ARISE, reminder: {r.get('text', '')}")
    if due:
        save_json(REMINDERS_FILE, _reminders)


def execute(plan):
    act = plan.get("action", "none")
    args = plan.get("args") or {}
    reply = plan.get("reply", "")
    try:
        if act == "torch_on":
            return actions.torch(True)
        if act == "torch_off":
            return actions.torch(False)
        if act == "battery":
            return actions.battery()
        if act == "wifi":
            return actions.wifi_info()
        if act == "location":
            return actions.location()
        if act == "vibrate":
            return actions.vibrate()
        if act == "brightness":
            return actions.brightness(int(args.get("level", 128)))
        if act == "open_url":
            return actions.open_url(args.get("url", ""))
        if act == "open_app":
            return actions.open_app(args.get("name", ""))
        if act == "photo":
            return actions.take_photo(args.get("path"))
        if act == "device_info":
            return actions.device_info()
        if act == "time":
            return "🕐 Abhi " + datetime.now().strftime("%I:%M %p") + " hai."
        if act == "date":
            return "📅 Aaj " + datetime.now().strftime("%A, %d %B %Y") + " hai."
        if act == "notify":
            return actions.notify(args.get("title", "Jarvis"), args.get("text", ""))
        if act == "toast":
            return actions.toast(args.get("text", reply))
        if act == "reminder":
            secs = int(args.get("seconds", 60))
            _reminders.append({"at": time.time() + secs, "text": args.get("text", "Reminder")})
            save_json(REMINDERS_FILE, _reminders)
            return reply or f"⏰ {secs} second baad yaad dila dunga."
        if act == "learn_qa":
            cfg = brain._brain_config()
            qa_path = ROOT / cfg.get("qa_file", "my_brain/qa.json")
            data = load_json(qa_path, [])
            q = str(args.get("question", "")).strip()
            a = str(args.get("answer", "")).strip()
            if q and a:
                data.append({"questions": [q], "answer": a})
                qa_path.parent.mkdir(parents=True, exist_ok=True)
                save_json(qa_path, data)
                return f"🧠 Seekh liya: “{q}” ka jawab yaad kar liya."
            return "⚠️ Seekhane ke liye aise likho: seekho: sawaal => jawab"
        if act == "remember":
            mem = load_json(MEMORY_FILE, {"facts": []})
            fact = str(args.get("fact", "")).strip()
            if fact and fact not in mem["facts"]:
                mem["facts"].append(fact)
                save_json(MEMORY_FILE, mem)
            return reply or "Yaad kar liya."
    except Exception as e:
        log(f"action fail {act}: {e}")
        return f"⚠️ Kaam fail hua: {e}"
    return reply or "Ho gaya."


def handle(text):
    text = (text or "").strip()
    if not text:
        return "Kuch bolo ARISE."
    check_reminders()
    # video command: video /path/file.mp4 shorts / trim ...
    if text.lower().startswith("video"):
        return handle_video_text(text)
    mem = load_json(MEMORY_FILE, {"facts": []})
    plan = brain.plan(text, mem.get("facts", []))
    out = execute(plan)
    log(f"CMD: {text} -> {plan.get('action')} -> {out[:120]}")
    return out


def handle_video_text(text):
    import re
    m = re.search(r"(/[\w./\- ]+\.mp4|[\w./\-]+\.mp4)", text)
    if not m:
        return ("🎬 Video ke liye file ka path do. Example: "
                "video /sdcard/Download/game.mp4 shorts bana do")
    path = m.group(1).strip()
    out_folder = str(_out_folder())
    low = text.lower()
    try:
        made = video_editor.auto_edit(
            path, out_folder,
            shorts=("short" in low or "9:16" in low or "reel" in low),
            duration=(30 if "30" in low else None),
            do_compress=("compress" in low or "chhota" in low))
        return "🎬 Video tayyar:\n" + "\n".join(made)
    except Exception as e:
        return f"⚠️ Video edit fail: {e}"


def speak_out(text):
    if CONFIG.get("speak_replies"):
        actions.speak(text)


def self_check():
    import shutil
    # Nezu ki .env se key load Karo — warna report galat dikhayegi
    try:
        brain._load_local_env()
    except Exception:
        pass
    lines = ["🩺 Jarvis Self-Check"]
    lines.append(f"📂 Folder: {ROOT}")
    lines.append(f"🐍 Python: {sys.version.split()[0]}")
    lines.append("🎬 ffmpeg: " + ("✅ hai" if shutil.which("ffmpeg") else "❌ nahi (pkg install -y ffmpeg)"))
    for cmd in ["termux-tts-speak", "termux-battery-status", "termux-torch",
                "termux-notification", "termux-camera-photo"]:
        lines.append(f"📱 {cmd}: " + ("✅" if shutil.which(cmd) else "❌ (Termux:API app chahiye)"))
    lines.append("🤖 Gemini key: " + ("✅ set" if os.environ.get("GEMINI_API_KEY") else "⚠️ nahi — sirf rule commands chalenge"))
    lines.append(f"🧠 Memory facts: {len(load_json(MEMORY_FILE, {'facts': []}).get('facts', []))}")
    lines.append(f"⏰ Pending reminders: {len(_reminders)}")
    try:
        own = brain.load_own_brain()
        lines.append(f"🧠 Apna dimag: persona {'✅' if own.get('persona') else '❌'}, "
                     f"knowledge {len(own.get('knowledge', ''))} akshar, "
                     f"sikhe jawab {len(own.get('qa', []))}")
    except Exception:
        lines.append("🧠 Apna dimag: ❌ load nahi hua")
    report = "\n".join(lines)
    (ROOT / "self-check.txt").write_text(report, encoding="utf-8")
    return report


def serve():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def _ok(self, txt):
            body = json.dumps({"reply": txt}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            if self.headers.get("X-Jarvis-Token") != CONFIG.get("local_token"):
                self._ok("⚠️ Token galat hai.")
                return
            if u.path == "/cmd":
                q = parse_qs(u.query).get("text", [""])[0]
                self._ok(handle(q))
            elif u.path == "/ping":
                self._ok("Jarvis online hai.")
            else:
                self._ok("Use /cmd?text=...")

        def log_message(self, *a):
            pass

    host = CONFIG.get("server_host", "127.0.0.1")
    port = int(CONFIG.get("server_port", 8765))
    print(f"🤖 Jarvis server: http://{host}:{port}  (sirf isi phone se chalega)")
    log("server start")
    ThreadingHTTPServer((host, port), H).serve_forever()


def _out_folder():
    """Config wala folder writable ho to wahi, warna project ke andar videos/."""
    want = Path(CONFIG.get("video_output_folder", str(ROOT / "videos")))
    try:
        want.mkdir(parents=True, exist_ok=True)
        return want
    except Exception:
        alt = ROOT / "videos"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def video_cli(argv):
    p = argparse.ArgumentParser(prog="jarvis video")
    p.add_argument("input")
    p.add_argument("--shorts", action="store_true", help="9:16 Shorts version")
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, default=None)
    p.add_argument("--compress", action="store_true")
    p.add_argument("--audio", action="store_true", help="sirf audio MP3 nikalo")
    p.add_argument("--mute", action="store_true")
    p.add_argument("--speed", type=float, default=None)
    p.add_argument("--thumb", action="store_true", help="thumbnail JPG")
    a = p.parse_args(argv)
    out_folder = _out_folder()
    stem = Path(a.input).stem
    try:
        if a.audio:
            print("🎵", video_editor.extract_audio(a.input, out_folder / f"{stem}.mp3"))
        elif a.mute:
            print("🔇", video_editor.mute(a.input, out_folder / f"{stem}_mute.mp4"))
        elif a.speed:
            print("⏩", video_editor.speed(a.input, out_folder / f"{stem}_speed.mp4", a.speed))
        elif a.thumb:
            print("🖼️", video_editor.thumbnail(a.input, out_folder / f"{stem}.jpg"))
        else:
            made = video_editor.auto_edit(a.input, out_folder, shorts=a.shorts,
                                          start=a.start, duration=a.duration,
                                          do_compress=a.compress)
            print("🎬 Tayyar:")
            for f in made:
                print(" -", f)
    except Exception as e:
        print(f"⚠️ {e}")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(prog="jarvis")
    ap.add_argument("--once", help="ek command chala ke band")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--speak", action="store_true", help="jawab awaaz me bhi bolo")
    args, rest = ap.parse_known_args()

    if rest and rest[0] == "video":
        return video_cli(rest[1:])
    if args.self_check:
        print(self_check())
        return 0
    if args.serve:
        serve()
        return 0
    if args.once:
        out = handle(args.once)
        print(out)
        if args.speak:
            speak_out(out)
        return 0

    name = CONFIG.get("assistant_name", "Jarvis")
    user = CONFIG.get("user_name", "ARISE")
    print(f"🤖 {name} online hai, {user}. Kuch bolo (band karne ke liye: exit)")
    while True:
        try:
            text = input("🧑 ARISE: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Jarvis band ho raha hai.")
            return 0
        if text.lower() in {"exit", "quit", "band karo", "band kar"}:
            print("👋 Thik hai, milte hai.")
            return 0
        out = handle(text)
        print(f"🤖 {name}: {out}")
        speak_out(out)


if __name__ == "__main__":
    raise SystemExit(main())
