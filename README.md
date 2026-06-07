# 🔒 ForceSub Bot + API

A Telegram Force-Subscribe bot with a REST API, persistent Turso (libSQL) storage,
Render webhook deployment, and auto-ping to keep the free tier alive.

---

## 🚀 Deploy to Render

### Step 1 — Push to GitHub
```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/forcesub-bot.git
git push -u origin main
```

### Step 2 — Create Render Web Service
1. Go to https://dashboard.render.com → **New → Web Service**
2. Connect your GitHub repo
3. Set **Runtime** = Python 3
4. **Build Command**: `pip install -r requirements.txt`
5. **Start Command**: `python main.py`

### Step 3 — Environment Variables (in Render Dashboard)
| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your bot token |
| `TURSO_URL` | Your Turso DB URL |
| `TURSO_TOKEN` | Your Turso auth token |
| `ADMIN_IDS` | Your Telegram user ID (get from @userinfobot) |
| `RENDER_URL` | `https://YOUR-APP-NAME.onrender.com` |
| `PORT` | `5000` |

### Step 4 — Make bot admin in your channel
Add your bot as **admin** in the private channel so it can read member status.

---

## 📡 API Endpoints (for Telebot Creator / TPY)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/ping` | Auto-ping endpoint |
| GET | `/api/check?user_id=123` | Check if user is verified |
| GET | `/api/channels` | List all channels |
| POST | `/api/addchn` | Add a channel (admin) |
| POST | `/api/rmchn` | Remove a channel (admin) |

### Example: Check user in TPY
```
URL: https://YOUR-APP.onrender.com/api/check?user_id={{user_id}}
Method: GET
```
Response:
```json
{
  "user_id": 123456789,
  "verified": true,
  "missing": [],
  "channels": [{"url": "https://t.me/+xxx", "id": "-100..."}]
}
```
Use `verified` field in your TPY condition node.

---

## 🤖 Bot Commands

### User Commands
| Command | Description |
|---------|-------------|
| `/start` | Welcome message (gated) |
| `/verify` | Check membership status |
| `/help` | Show help |

### Admin Commands
| Command | Description |
|---------|-------------|
| `/addchn <url>` | Add a channel (private invite link) |
| `/rmchn <url>` | Remove a channel |
| `/listchn` | List all channels |

---

## 🔗 How Force-Sub Works
1. User starts bot or sends any message
2. Bot checks if user is member of all registered channels
3. If **not verified** → sends buttons with invite links + **Verify Me** button
4. User taps channel link → sends **join request** (doesn't need to be accepted!)
5. User taps **Verify Me** → bot rechecks → grants access ✅

> The bot verifies that a join **request was sent**, NOT that the user was accepted.
> Admins still manually approve members in the channel.
