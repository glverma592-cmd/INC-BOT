# Nezuko all-in-one self-check: ek baar chalao, pura report ek saath milta hai.
import sys, subprocess, importlib.util
def check(naam, fn):
    try:
        r = fn()
        print(f"  {naam:35s} -> {r}")
        return True
    except Exception as e:
        print(f"  {naam:35s} -> FAIL: {type(e).__name__}: {str(e)[:90]}")
        return False
def _mod(name):
    return __import__(name)
def _ver(mod):
    return getattr(mod, "version", None) or getattr(mod, "__version__", "?")
def modchk(name):
    def f():
        m = importlib.import_module(name)
        return "OK (" + str(_ver(m)) + ")"
    return f
def _opus():
    import ctypes.util
    try:
        import discord
        if not discord.opus.is_loaded():
            discord.opus.load_opus(ctypes.util.find_library("opus") or "libopus.so.0")
        return "OK (load hua)"
    except Exception as e:
        return None
def _opus_w():
    r = _opus()
    if r: return r
    raise RuntimeError("opus load fail - pkg install -y libopus opusfile")
def _cmd(name):
    def f():
        p = subprocess.run([name, "-version"], capture_output=True, text=True, timeout=20)
        line = (p.stdout or p.stderr or "").splitlines()[0] if (p.stdout or p.stderr) else ""
        return line[:60] + (" OK" if p.returncode == 0 else "")
    return f
def _rec():
    try:
        from discord.ext import voice_recv
        return "OK (voice_recv hai)"
    except Exception as e:
        raise RuntimeError("voice_recv missing — pip install discord-ext-voice-recv Karo")

import importlib
print("=== Nezuko CHECK ALL ===")
print("[1] Core modules:")
check("discord", modchk("discord"))
check("aiohttp", modchk("aiohttp"))
check("nacl (PyNaCl)", modchk("nacl"))
check("davey (Davey)", modchk("davey"))
check("yt_dlp", modchk("yt_dlp"))
check("edge_tts", modchk("edge_tts"))
check("speech_recognition (STT)", modchk("speech_recognition"))
print("[2] System tools:")
check("ffmpeg", _cmd("ffmpeg"))
print("[3] Voice bolna (opus):")
check("opus", _opus_w)
print("[4] VC me sunna (voice_recv):")
check("voice_recv sunna", _rec)
print("[5] Bot code (bot.py syntax + imports):")
try:
    src = open("bot/bot.py" if __import__('os').path.exists("bot/bot.py") else "bot.py", encoding="utf-8").read()
    py_compile_ok = compile(src, "bot.py", "exec")
    n_cmd = src.count("@bot.tree.command")
    print(f"  bot.py compile -> OK, slash commands code me: {n_cmd}")
    for k in ("_speak_in_vc", "_handle_voice_utterance", "_start_listening", "_stt_gemini", "_stereo_to_mono", "_prefetch_track_async"):
        print(f"  {k:35s} -> {'OK hai' if k in src else 'MISSING!'}")
except SyntaxError as e:
    print(f"  bot.py compile -> SYNTAX ERROR line {e.lineno}: {e.msg}")
except Exception as e:
    print(f"  bot.py check -> FAIL: {type(e).__name__}: {str(e)[:90]}")
print("=== CHECK KHATM ===")
print("Report screenshot / text mujhe bhej dena - mai har FAIL ka fix dunga.")
