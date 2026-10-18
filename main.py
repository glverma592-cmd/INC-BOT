"""Hosting entrypoint — repo root se `python main.py` chalega (BotKeep/Render/Procfile)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot"))
import bot as _botmod

if __name__ == "__main__":
    _botmod.main()
