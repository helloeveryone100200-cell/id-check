# Telegram Data Bot

Deposit/WhatsApp report tracking Telegram bot with MongoDB persistence, scheduled messages, plus/minus counters, and a Flask keep-alive server for Render deployment.

## Features

- **Deposit Reports** — Parses and aggregates daily deposit data per group chat
- **WhatsApp Reports** — Tracks 进粉数量 / 转化到电报 / register stats
- **Plus/Minus Counter** — Reply-based counter system per user
- **Scheduled Messages** — Admin-configurable timed broadcasts to groups
- **MongoDB Persistence** — All bot data backed by MongoDB
- **Keep-alive Server** — Flask web server to keep the Render service alive

## Environment Variables (Render)

| Key | Description |
|-----|-------------|
| `BOT_TOKEN` | Telegram Bot Token from @BotFather |
| `MONGO_URI` | MongoDB connection string (e.g. `mongodb+srv://...`) |
| `MONGO_DB_NAME` | MongoDB database name (default: `telegram_bot`) |
| `PORT` | Port for the keep-alive Flask server (Render sets this to `10000`) |

## Deploy on Render

1. Fork / push this repo to GitHub
2. Create a new **Web Service** on [Render](https://render.com)
3. Connect this repo
4. Render auto-detects `render.yaml` and configures the service
5. Set `BOT_TOKEN` and `MONGO_URI` as secret environment variables in the Render dashboard

## Local Development

```bash
# Install dependencies
pip install -r telegram-bot/requirements.txt

# Create .env
cp .env.example .env
# Edit .env with your values

# Run
python3 telegram-bot/main.py
```

## Structure

```
telegram-bot/
├── main.py          # Bot logic (all handlers)
├── web_server.py    # Flask keep-alive server
├── requirements.txt # Python dependencies
└── pyproject.toml   # Project metadata
render.yaml          # Render deployment config
.env.example         # Example environment file
```
