"""VC awaaz chain ka Termux self-test — bina Discord VC join kiye.

Ek jaani-pehchani line ko awaaz banakar bilkul waisi hi chain chalata hai
jaisi asli VC me chalti hai: opus encode -> decode -> mono/WAV -> STT.
Output padh ke pata chalega dikkat kaha hai (STT net? Gemini key? decode?).

Chalao:  python vc_selftest.py
"""
import asyncio, io, os, struct, subprocess, sys, tempfile, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_BOT_TOKEN", "x")

LINE = "Nezuko tum kaisi ho, aaj mausam kaisa hai"

async def main():
    import bot as b
    print("=== VC SELF TEST ===")
    print("1) Gemini key set hai:", bool(b.GEMINI_API_KEY))
    # 2) Ek jaani hui awaaz banao (edge-tts, ladki wali Hindi awaaz)
    tmp = tempfile.mkdtemp()
    mp3 = os.path.join(tmp, "line.mp3"); wav = os.path.join(tmp, "line.wav")
    import edge_tts
    await edge_tts.Communicate(LINE, voice="hi-IN-SwaraNeural").save(mp3)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp3, "-ar", "48000", "-ac", "2", wav], check=True)
    with wave.open(wav, "rb") as w:
        pcm_stereo = w.readframes(w.getnframes())
    print("2) Test awaaz bani: %.1f sec stereo PCM" % (len(pcm_stereo) / (48000 * 4)))
    # 3) Jaise Discord se aata hai: 20ms ke opus packet banao, fir bot wala decoder chalao
    import discord
    if not discord.opus.is_loaded():
        discord.opus.load_opus("libopus.so.0") if os.path.exists("/data/data/com.termux/files/usr/lib/libopus.so.0") else None
    enc = discord.opus.Encoder(); dec = discord.opus.Decoder()
    frame = 960 * 2 * 2  # 20ms, 48k, stereo, 16-bit
    decoded = bytearray()
    for i in range(0, len(pcm_stereo) - frame + 1, frame):
        chunk = pcm_stereo[i:i + frame]
        try: packet = enc.encode(chunk, 960)
        except Exception as e: print("   encode fail:", e); break
        out = dec.decode(packet, fec=False)
        if out: decoded.extend(out)
    print("3) Opus round-trip: %.1f sec PCM wapas mila" % (len(decoded) / (48000 * 4)))
    if not decoded:
        print("RESULT: opus decode hi fail — yehi asli dikkat hai."); return
    vals = struct.unpack("<%dh" % (len(decoded) // 2), bytes(decoded[:len(decoded)//2*2]))
    rms = int((sum(v * v for v in vals) / max(1, len(vals))) ** 0.5)
    print("   awaaz level rms =", rms, "(300-4000 saaf boli hoti hai)")
    # 4) Bot wale STT chalao — Google free aur Gemini dono
    wav16 = b._pcm_to_wav(bytes(decoded)); wav48 = b._pcm_to_wav_48k(bytes(decoded), 1)
    g = ""
    try: g = b._stt_google_free(wav16, quick=True)
    except Exception as e: print("   Google STT error:", type(e).__name__, e)
    print("4) Google-free STT ne suna:", repr(g))
    gm = ""
    try: gm = await b._stt_gemini(wav48)
    except Exception as e: print("   Gemini STT error:", type(e).__name__, e)
    print("5) Gemini STT ne suna:", repr(gm))
    print()
    if g or gm:
        print("RESULT: STT chain SAHI hai — asli VC me dikkat awaaz ke packets/decode ki hai.")
    else:
        print("RESULT: STT hi nahi sun pa raha — upar wali lines bata dengi wajah (key? net? awaaz gadbad?).")
    print("Bolna tha:", repr(LINE))

asyncio.run(main())
