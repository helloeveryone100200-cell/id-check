"""Entry point for Render deployment — runs telegram-bot/main.py as __main__."""
import runpy, sys, os

# Change into telegram-bot/ so relative paths (pickle, json files) work correctly
bot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'telegram-bot')
os.chdir(bot_dir)
sys.path.insert(0, bot_dir)

# Run main.py with __name__ == '__main__' so the keep_alive + polling loop executes
runpy.run_path(os.path.join(bot_dir, 'main.py'), run_name='__main__')
