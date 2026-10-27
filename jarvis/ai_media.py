"""Jarvis se AI image + AI video banana (Pollinations + ffmpeg).

Prompt kisi bhi bhasha me ho sakta hai — pehle English me badla jata hai
(Gemini ho to), kyunki image model English behtar samajhta hai.
"""
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import video_editor


def _english_prompt(prompt):
    try:
        import brain
        brain._load_local_env()
    except Exception:
        pass
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return prompt
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    try:
        body = {"contents": [{"parts": [{"text":
            "Convert this into one detailed English image-generation prompt. "
            "Return ONLY the prompt:\n" + prompt}]}]}
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=25) as r:
            out = json.loads(r.read().decode("utf-8"))
        txt = out["candidates"][0]["content"]["parts"][0]["text"].strip()
        return txt or prompt
    except Exception:
        return prompt


def _download(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def generate_image(prompt, out_folder):
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    eng = _english_prompt(prompt)
    seed = random.randint(1, 999999)
    q = urllib.parse.quote(eng, safe="")
    last = None
    for model in ("flux", "turbo"):
        for attempt in range(2):
            try:
                data = _download(f"https://image.pollinations.ai/prompt/{q}"
                                 f"?width=1024&height=1024&seed={seed}&model={model}&nologo=true")
                if data[:3] == b"\xff\xd8\xff" or data[:8].startswith(b"\x89PNG") or data[:4] == b"RIFF":
                    path = out_folder / f"ai-image-{time.strftime('%H%M%S')}.jpg"
                    path.write_bytes(data)
                    return path
                last = "image sahi nahi bani"
                break
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code == 402 and attempt == 0:
                    time.sleep(46)  # free limit — thoda ruk kar dobara
                    continue
                break
            except Exception as e:
                last = str(e)
                break
    raise RuntimeError(last or "image fail")


def generate_video(prompt, out_folder, seconds=5):
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    img = generate_image(prompt, out_folder)
    out = out_folder / f"ai-video-{time.strftime('%H%M%S')}.mp4"
    video_editor.image_to_video(img, out, seconds=seconds)
    return out
