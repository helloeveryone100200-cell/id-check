"""Entry point for Render deployment — forwards to telegram-bot/main.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'telegram-bot'))
os.chdir(os.path.join(os.path.dirname(__file__), 'telegram-bot'))
import main  # noqa: F401 (runs the bot on import via __main__ block)
