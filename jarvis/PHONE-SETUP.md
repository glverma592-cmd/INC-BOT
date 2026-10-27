# 📱 MacroDroid + Acode + Termux:Widget — teeno ka kaam

Jarvis Termux me rehta hai. Baaki apps uske haath-pair hai:

| App | Kaam |
|---|---|
| **Termux** | Jarvis ka ghar — AI, code, server, video editing |
| **Termux:API** | Torch, battery, camera, mic, volume jaise phone kaam |
| **MacroDroid** | Ek tap par taiyar kaam + phone-wide trigger (button, time, charger) |
| **Acode** | Phone par code/dimag edit karna — bug fix, training |
| **Termux:Widget** | Home screen par Jarvis ke button (Voice / Start / Stop) |

## 🎙️ Voice chat (VC jaisa)

```bash
python jarvis.py --voice
```

- Jarvis sunega — **3–4 second chup** rahoge to baat poori mani jayegi
- Jo suna, woh screen par likha dikhega: `📝 Tumne kaha: "..."`
- Phir jawab **awaaz me** dega. Text mode me awaaz band rehti hai.
- Sunai de par samajh na aaye to ghabrayega nahi — phir se bolega bolne ko.
- Aam awaaz, phone 20–30 cm door — yahi best setting hai. Bhool jaye to
  `purani baat batao`, bhasha badalni ho to `Hindi me baat Karo` bolo —
  Jarvis usi bhasha me jawab dene lagega.

## 🤖 MacroDroid ke ready kaam

Jarvis server chalu rakho (`bash start-24x7.sh`). MacroDroid me
**+ → Trigger → Action** aise banao:

### 1) Ek-tap Torch
- Trigger: Floating Button
- Action 1: HTTP GET `http://127.0.0.1:8765/cmd?text=torch%20on`
  Header: `X-Jarvis-Token: (config.json wali local_token)`
- Chaaho to Action 2: apna Launch App bhi jod sakte ho.

### 2) Good Night routine
- Trigger: Time (11:30 PM) ya Floating Button
- Actions: HTTP GET se 3 command — `brightness 20 karo`, `volume 20 karo`,
  `bolo good night` ki jagah sirf notification ke liye
  `/cmd?text=...` me apna text rakho.

### 3) App kholna pakka tareeke se
- Termux se app na khule to MacroDroid ka **Launch App** action sabse pakka hai.
- Macro: Trigger (button) → Launch App (YouTube). Jarvis ko khabar deni ho to
  saath me HTTP GET `/cmd?text=YouTube kholo` bhi rakho.

### 4) Subah ki report
- Trigger: Time (8:00 AM)
- Action: HTTP GET `/cmd?text=battery batao` — jawab notification/log me milega.

Note: MacroDroid se **bolkar** command dena hota hai to Termux voice mode
(`--voice`) hi asal raasta hai; MacroDroid taiyar (fixed) kaamo ke liye best hai.

## ✏️ Acode se edit/training

Acode me folder kholo: `INC-BOT/jarvis`

- Swabhaav badlo: `my_brain/persona.txt`
- Knowledge bharo: `my_brain/knowledge.md`
- Sikhe jawab dekho: `my_brain/qa.json`
- Code: `jarvis.py` (baat-cheet), `brain.py` (dimag), `actions.py` (phone kaam),
  `video_editor.py` (edit), `voice.py` (sunna)

Brain wali files save karte hi **bina restart** lagu ho jati hai.
Code wali file badlo to Jarvis band karke phir chalao, ya:

```bash
bash stop-24x7.sh
```

```bash
bash start-24x7.sh
```
