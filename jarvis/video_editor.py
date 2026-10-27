"""Jarvis ka video editing module — ffmpeg se automatic edit.

Safe operations hi hai: trim, 9:16 Shorts, compress, audio nikalo,
mute, speed, thumbnail, subtitle burn (agar ffmpeg support kare).
"""
import re
import shutil
import subprocess
from pathlib import Path


def _ffmpeg():
    p = shutil.which("ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg nahi mila — Termux me: pkg install -y ffmpeg")
    return p


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg error")[-600:])
    return True


def probe(path):
    """Video hai ya nahi + basic info (ffprobe agar ho)."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"File nahi mili: {path}"}
    info = {"ok": True, "path": str(p), "size_mb": round(p.stat().st_size / 1048576, 2)}
    if shutil.which("ffprobe"):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(p)],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            info["probe"] = r.stdout[:800]
    return info


def make_shorts(inp, outp, start=0, duration=None):
    """9:16 vertical Shorts/Reels version (1080x1920, beech se crop)."""
    cmd = [_ffmpeg(), "-y", "-ss", str(start), "-i", str(inp)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-vf", "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", str(outp)]
    _run(cmd)
    return str(outp)


def trim(inp, outp, start=0, duration=None):
    cmd = [_ffmpeg(), "-y", "-ss", str(start), "-i", str(inp)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", str(outp)]
    _run(cmd)
    return str(outp)


def compress(inp, outp, crf=28):
    _run([_ffmpeg(), "-y", "-i", str(inp), "-vcodec", "libx264",
          "-crf", str(crf), "-preset", "veryfast", "-acodec", "aac",
          "-b:a", "128k", str(outp)])
    return str(outp)


def extract_audio(inp, outp):
    _run([_ffmpeg(), "-y", "-i", str(inp), "-vn", "-acodec", "libmp3lame",
          "-q:a", "2", str(outp)])
    return str(outp)


def mute(inp, outp):
    _run([_ffmpeg(), "-y", "-i", str(inp), "-an", "-c:v", "copy", str(outp)])
    return str(outp)


def speed(inp, outp, factor=1.5):
    factor = max(0.25, min(4.0, float(factor)))
    _run([_ffmpeg(), "-y", "-i", str(inp),
          "-filter:v", f"setpts={1/factor}*PTS",
          "-filter:a", f"atempo={factor}", str(outp)])
    return str(outp)


def thumbnail(inp, outp, at=1):
    _run([_ffmpeg(), "-y", "-ss", str(at), "-i", str(inp),
          "-frames:v", "1", str(outp)])
    return str(outp)


def burn_subtitles(inp, srt, outp):
    srt_esc = str(srt).replace("'", "'\\''").replace(":", "\\:")
    _run([_ffmpeg(), "-y", "-i", str(inp), "-vf", f"subtitles='{srt_esc}'",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-c:a", "aac", str(outp)])
    return str(outp)


def auto_edit(inp, out_folder, shorts=True, start=0, duration=None, do_compress=False):
    """Ek command me pura kaam: (optional) cut + Shorts + compress."""
    inp = Path(inp)
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", inp.stem)
    work = inp
    made = []
    if duration or start:
        tmp = out_folder / f"{stem}_cut.mp4"
        trim(inp, tmp, start=start, duration=duration)
        work = tmp
        made.append(str(tmp))
    if shorts:
        out = out_folder / f"{stem}_shorts.mp4"
        make_shorts(work, out)
        work = out
        made.append(str(out))
    if do_compress:
        out = out_folder / f"{stem}_small.mp4"
        compress(work, out)
        work = out
        made.append(str(out))
    if not made:  # koi operation nahi manga — saaf copy bana do
        out = out_folder / f"{stem}_edited.mp4"
        trim(inp, out)
        made.append(str(out))
    return made
