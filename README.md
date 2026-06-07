# 🔒 ForceSub Channel API

A **pure REST API** — no bot token, no Telegram polling.
It only stores/retrieves channels in Turso DB.
Your **Telebot Creator (TPY) bot** handles all Telegram logic.

---

## 🚀 Deploy to Render

1. Push this folder to GitHub
2. Render → New Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `python main.py`
5. Set environment variables (see below)

### Environment Variables
| Key | Value |
|-----|-------|
| `TURSO_URL` | Already set in render.yaml |
| `TURSO_TOKEN` | Already set in render.yaml |
| `API_SECRET` | A strong password YOU choose (used to protect add/remove) |
| `RENDER_URL` | Your actual Render URL e.g. `https://forcesub-api.onrender.com` |
| `PORT` | `5000` |

---

## 📡 API Endpoints

### `GET /api/channels` — List all channels (no auth needed)
TPY calls this to get the list of channels to check.

**Response:**
```json
{
  "success": true,
  "count": 2,
  "channels": [
    { "url": "https://t.me/+AbCdEfGhIjK", "channel_id": "-1001234567890", "label": "Channel 1" },
    { "url": "https://t.me/+XyZaBcDeFgH", "channel_id": "-1009876543210", "label": "Channel 2" }
  ]
}
```

---

### `POST /api/addchn` — Add a channel (admin only)
**Header:** `X-API-Secret: your_secret`

**Body:**
```json
{
  "url": "https://t.me/+AbCdEfGhIjK",
  "channel_id": "-1001234567890",
  "label": "My VIP Channel"
}
```

> **How to get channel_id?**
> Forward any message from your channel to @userinfobot — it shows the ID.

---

### `POST /api/rmchn` — Remove a channel (admin only)
**Header:** `X-API-Secret: your_secret`

**Body:**
```json
{ "url": "https://t.me/+AbCdEfGhIjK" }
```

---

## 🤖 TPY / Telebot Creator Setup

### Step 1 — On /start or any command, fetch channels:
```
HTTP GET: https://your-api.onrender.com/api/channels
```
Store `channels` array in a variable.

### Step 2 — For each channel, call getChatMember:
```
Telegram API: getChatMember
  chat_id = {channel_id from array}
  user_id = {user_id}
```
Check if `status` is NOT "left" or "kicked".

### Step 3 — If not verified, send join buttons:
Show each channel URL as an inline button + a "✅ Verify Me" callback button.

### Step 4 — On "Verify Me" callback:
Re-run Step 2. If all passed → proceed. Else → show join prompt again.

---

## 🔐 Security
- `GET /api/channels` is public (TPY needs it without auth)
- `POST /api/addchn` and `POST /api/rmchn` require `X-API-Secret` header
- Set a strong `API_SECRET` in Render env vars
