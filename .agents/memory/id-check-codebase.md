---
name: id-check codebase overview
description: Full breakdown of the id-check GitHub repo — Telegram bot with duplicate check system + MongoDB + Flask keep-alive + pnpm monorepo scaffold.
---

# id-check Codebase — Complete Reference

## Repo Purpose
A **Telegram bot** that monitors group messages and flags duplicate submissions (phone number, WhatsApp, ID, username).
Built on top of a Replit pnpm monorepo scaffold.

---

## Core: bot/ folder (Python)

### bot/bot.py — Main Bot (861 lines)

**Tech stack:** python-telegram-bot, Flask (keep-alive), asyncio

**Env vars required:**
- `BOT_TOKEN` — Telegram Bot API token
- `ADMIN_IDS` — comma-separated Telegram user IDs with admin access
- `MONGO_URI` — MongoDB connection string
- `PORT` — port for Flask keep-alive server (default: 8080)

**Startup flow:**
1. Flask keep-alive server starts in a background thread (responds GET / → "OK")
2. Telegram bot starts polling via asyncio

**Form parsing — parse_submission(text):**
Parses text/caption messages using 4 regex patterns:
- `RE_USERNAME` → `username - value`
- `RE_PHONE` → `client number - value` OR `phone number - value`
- `RE_WHATSAPP` → `whatsapp number - value`
- `RE_ID` → `id - value`
Only triggers if `phone_number` is found (required field). Returns None otherwise.

**Group message handler — handle_group_message:**
1. Parses the message
2. Calls check_duplicate() in DB
3. If duplicate found → reply with formatted warning
4. If not duplicate → save to MongoDB

**Duplicate reply format — format_duplicate_reply:**
Template placeholders: `{user_mention}`, `{original_user}`, `{matched_fields}`, `{matched_field}`
Each match shows field emoji + field name + value in `<code>` tags (HTML parse mode).
Animated emoji supported via `<tg-emoji emoji-id="...">fallback</tg-emoji>` tags.

**Commands registered:**
- `/start` — welcome message with inline keyboard
- `/setmsg dup|welcome <msg>` — set duplicate or welcome message (admin, private)
- `/getmsg` — show current messages (admin, private)
- `/resetmsg dup|welcome` — reset to default (admin, private)
- `/setemoji <field>` — 2-step conversation to set animated emoji (admin, private)
- `/getemoji` — show emoji settings (admin, private)
- `/resetemoji <field>` — reset emoji to default (admin, private)
- `/addbutton Label | URL` — add custom start button (admin, private)
- `/listbuttons` — list buttons (admin, private)
- `/removebutton <n>` — remove button (admin, private)
- `/resetbuttons` — remove all custom buttons (admin, private)
- Owner Panel inline button → shows command list (admin callback only)

**Start keyboard:**
Always shows 3 default buttons: "Add me to group", "Share bot", "Author (https://t.me/yasha_sangi)"
+ any custom buttons from DB
+ "Owner Panel" button visible only to ADMIN_IDS users

---

### bot/database.py — MongoDB Helpers (347 lines)

**Connection:** lazy singleton via get_db()
- Uses `MONGO_URI` env var
- DB name from `MONGO_DB_NAME` env var (default: "telegram_bot")
- 5 second connection timeout
- Returns None if unavailable (graceful degradation)

**Collections:**
1. `submissions` — stores every non-duplicate submission
2. `settings` — stores customizable messages, emoji configs, start buttons

**submissions collection indexes:**
- `idx_phone` — phone_number ASC
- `idx_whatsapp` — whatsapp_number ASC (sparse)
- `idx_id` — id_number ASC (sparse)
- `idx_username` — username ASC (sparse)
- `idx_created_at` — created_at ASC

**Duplicate check — check_duplicate():**
Normalizes phone/whatsapp (strips spaces, dashes, dots, parens).
Normalizes id/username (strip + lowercase).
Queries each field separately, collects ALL matching fields.
Returns: `{ found: bool, doc: first_matching_doc, matches: [{field, value}, ...] }`

**save_submission():** Inserts normalized doc with telegram_id, telegram_username, username, phone_number, whatsapp_number, id_number, created_at (UTC).

**settings collection — key/value store:**
- `duplicate_msg` — custom duplicate warning template
- `start_msg` — custom /start welcome template
- `emoji_<field>` — per-field animated emoji config `{emoji_id, fallback}`
- `start_buttons` — list of `{text, url}` custom inline buttons

**Default duplicate message:**
`{user_mention} ⚠️ Duplicate detected!\n\n{matched_fields}\n\nOriginally submitted by: {original_user}`

**Default field emojis:** 📞 phone, 💬 whatsapp, 🪪 id, 👤 username

---

## Scaffold: monorepo (TypeScript/Node.js)

Mostly unused template scaffold. Key parts:

### artifacts/api-server — Express 5 backend
- Only endpoint: GET /api/healthz → { status: "ok" }
- Port from PORT env var
- Logger: pino (pretty in dev, JSON in prod)
- Build: esbuild → dist/index.mjs

### artifacts/mockup-sandbox — Design preview tool
- Vite+React server for Replit Canvas iframe previews
- shadcn/ui component library pre-installed
- NOT a user-facing app

### lib/api-spec — OpenAPI contract + Orval codegen
- Only defines /healthz endpoint
- Codegen: `pnpm --filter @workspace/api-spec run codegen`

### lib/db — Drizzle ORM + PostgreSQL
- Schema is empty (no tables)
- DATABASE_URL env required

---

## Data flow (bot)

```
Telegram Group Message
  ↓
bot.py: handle_group_message()
  ↓
parse_submission(text) — regex extract phone/whatsapp/id/username
  ↓ (if phone found)
database.py: check_duplicate()
  ↓ normalize all fields
  ↓ query MongoDB submissions collection
  ├─ DUPLICATE FOUND → format_duplicate_reply() → reply_text (HTML, pino log)
  └─ NOT FOUND → save_submission() → insert to MongoDB
```

---

## What's missing / not yet built
- No frontend React app (react-vite artifact)
- No database tables in Drizzle (schema/index.ts empty — unused by bot, bot uses MongoDB)
- No Python requirements.txt or Dockerfile in the repo
- The Node.js Express server and bot are completely separate systems (bot uses MongoDB, Express uses PostgreSQL via Drizzle)
