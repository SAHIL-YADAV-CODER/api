import os
import asyncio
import logging
import threading
import time
import httpx
from datetime import datetime
from flask import Flask, request, jsonify
from telebot.async_telebot import AsyncTeleBot
from telebot import types
import libsql_experimental as libsql

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "8915358086:AAELvKimuQQLc9GfO7pSp-dv2eD7cEfZeRw")
TURSO_URL   = os.environ.get("TURSO_URL",  "libsql://escrow-escrow.aws-ap-south-1.turso.io")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODAzMzQ1NjEsImlkIjoiMDE5ZTg0MzQtYWUwMS03NWJmLTgyZTMtNTZiODBhNGVhMTBkIiwicmlkIjoiMDY2NzJhNDMtNjNiYy00YTg3LWFkZDEtZDIyNmMyZDJlNTc3In0.qVH0T7oJ_ZO7xDnC48LwBqGM-0C7edjuKS3sf_0jT2oySiaVgxKSlO0UiIHwiZFD-sf94anNbBrHeCNtJfo7Cw")
ADMIN_IDS   = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
RENDER_URL  = os.environ.get("RENDER_URL", "https://api-ccit.onrender.com")   # e.g. https://yourapp.onrender.com
PORT        = int(os.environ.get("PORT", 5000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

bot  = AsyncTeleBot(BOT_TOKEN)
app  = Flask(__name__)

# ─────────────────────────────────────────────
#  DATABASE  (Turso / libSQL)
# ─────────────────────────────────────────────
def get_db():
    return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_url TEXT    NOT NULL UNIQUE,
            channel_id  TEXT,
            added_at    TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    log.info("✅ Database initialised")

def db_add_channel(url: str, cid: str = None):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO channels (channel_url, channel_id) VALUES (?, ?)",
        (url, cid)
    )
    conn.commit()
    conn.close()

def db_remove_channel(url: str):
    conn = get_db()
    conn.execute("DELETE FROM channels WHERE channel_url = ?", (url,))
    conn.commit()
    conn.close()

def db_get_channels():
    conn  = get_db()
    rows  = conn.execute("SELECT channel_url, channel_id FROM channels").fetchall()
    conn.close()
    return rows   # list of (url, channel_id)

def db_update_channel_id(url: str, cid: str):
    conn = get_db()
    conn.execute("UPDATE channels SET channel_id = ? WHERE channel_url = ?", (cid, url))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
TICK  = "✅"
CROSS = "❌"
LOCK  = "🔒"
KEY   = "🔑"
BELL  = "🔔"
STAR  = "⭐"
CHAIN = "🔗"
INFO  = "ℹ️"
WARN  = "⚠️"
CROWN = "👑"
ROCKET= "🚀"

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

async def resolve_channel_id(invite_url: str) -> str | None:
    """
    Try to get a numeric channel ID from an invite link.
    For private channels the bot must be admin there already.
    """
    try:
        # Extract username if it's a public link like t.me/username
        if "t.me/+" in invite_url or "t.me/joinchat" in invite_url:
            # Private invite → we can't resolve without joining; store None for now
            return None
        part = invite_url.rstrip("/").split("/")[-1]
        if not part.startswith("+"):
            chat = await bot.get_chat(f"@{part}")
            return str(chat.id)
    except Exception as e:
        log.warning(f"Could not resolve channel ID for {invite_url}: {e}")
    return None

async def check_user_in_channels(user_id: int) -> tuple[bool, list]:
    """
    Returns (all_joined, list_of_not_joined_urls).
    For private invite links we check if user is a member via get_chat_member.
    """
    channels = db_get_channels()
    if not channels:
        return True, []

    not_joined = []
    for url, cid in channels:
        if not cid:
            # Try to resolve now
            cid = await resolve_channel_id(url)
            if cid:
                db_update_channel_id(url, cid)

        if not cid:
            # Cannot verify → skip (assume ok)
            continue

        try:
            member = await bot.get_chat_member(cid, user_id)
            status = member.status
            # "left" / "kicked" means not in channel
            # For request-based: "restricted" with pending might appear; treat as ok
            if status in ("left", "kicked"):
                not_joined.append(url)
        except Exception as e:
            log.warning(f"get_chat_member error for {cid}: {e}")
            not_joined.append(url)

    return len(not_joined) == 0, not_joined

def build_join_keyboard(not_joined: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, url in enumerate(not_joined, 1):
        kb.add(types.InlineKeyboardButton(
            text=f"📢  Join Channel {idx}",
            url=url
        ))
    kb.add(types.InlineKeyboardButton(
        text=f"{TICK}  I've Requested — Verify Me",
        callback_data="verify_me"
    ))
    return kb

# ─────────────────────────────────────────────
#  GATE  — call this before any user action
# ─────────────────────────────────────────────
async def gate(message: types.Message) -> bool:
    """
    Returns True if user passes the force-sub check, False otherwise.
    Sends the join prompt automatically if they fail.
    """
    uid = message.from_user.id
    if is_admin(uid):
        return True

    ok, missing = await check_user_in_channels(uid)
    if ok:
        return True

    # Build channel list text
    lines = "\n".join(
        [f"  {CHAIN} Channel {i+1}" for i, _ in enumerate(missing)]
    )
    text = (
        f"{LOCK} *Access Restricted!*\n\n"
        f"To use this bot you must *request to join* our channel(s):\n\n"
        f"{lines}\n\n"
        f"{INFO} _Tap the button below → send a join request → then press_ *Verify Me*.\n"
        f"{WARN} _You do not need to be accepted — just sending the request is enough!_"
    )
    await bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=build_join_keyboard(missing)
    )
    return False

# ─────────────────────────────────────────────
#  BOT COMMANDS
# ─────────────────────────────────────────────

@bot.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    if not await gate(msg):
        return

    name = msg.from_user.first_name or "Friend"
    text = (
        f"{ROCKET} *Welcome, {name}!*\n\n"
        f"{STAR} You have been *verified* and granted full access.\n\n"
        f"{KEY} *Available Commands:*\n"
        f"  `/start`   — Show this welcome message\n"
        f"  `/help`    — Get help & info\n"
        f"  `/verify`  — Re-check your membership\n\n"
        f"_Enjoy using the bot!_ {BELL}"
    )
    await bot.send_message(msg.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=["help"])
async def cmd_help(msg: types.Message):
    if not await gate(msg):
        return

    text = (
        f"{INFO} *Help & Information*\n\n"
        f"This bot uses *Force Subscribe* — you must join our channel(s) "
        f"before using any features.\n\n"
        f"{CROWN} *Admin Commands:*\n"
        f"  `/addchn <invite_url>` — Add a channel\n"
        f"  `/rmchn  <invite_url>` — Remove a channel\n"
        f"  `/listchn`             — List all channels\n\n"
        f"{STAR} *User Commands:*\n"
        f"  `/start`  — Start the bot\n"
        f"  `/verify` — Check your membership status\n"
    )
    await bot.send_message(msg.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=["verify"])
async def cmd_verify(msg: types.Message):
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "User"

    ok, missing = await check_user_in_channels(uid)
    if ok:
        text = (
            f"{TICK} *Verification Successful!*\n\n"
            f"Hello *{name}*, you are a member of all required channels.\n"
            f"{ROCKET} Full access granted!"
        )
        await bot.send_message(msg.chat.id, text, parse_mode="Markdown")
    else:
        lines = "\n".join([f"  {CHAIN} Channel {i+1}" for i, _ in enumerate(missing)])
        text = (
            f"{CROSS} *Verification Failed!*\n\n"
            f"You still need to request access to:\n{lines}\n\n"
            f"{WARN} _Send a join request, then press Verify Me._"
        )
        await bot.send_message(
            msg.chat.id, text,
            parse_mode="Markdown",
            reply_markup=build_join_keyboard(missing)
        )


# ── ADMIN: /addchn ──────────────────────────
@bot.message_handler(commands=["addchn"])
async def cmd_addchn(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await bot.send_message(msg.chat.id, f"{CROSS} *Admin only command!*", parse_mode="Markdown")
        return

    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(
            msg.chat.id,
            f"{WARN} *Usage:* `/addchn <invite_url>`\n\n"
            f"Example: `/addchn https://t.me/+AbCdEfGhIjK`",
            parse_mode="Markdown"
        )
        return

    url = parts[1].strip()
    cid = await resolve_channel_id(url)
    db_add_channel(url, cid)

    resolved = f"\n{INFO} Channel ID resolved: `{cid}`" if cid else f"\n{WARN} _Channel ID could not be resolved yet. Make the bot an admin in the channel._"
    await bot.send_message(
        msg.chat.id,
        f"{TICK} *Channel Added!*\n\n{CHAIN} `{url}`{resolved}",
        parse_mode="Markdown"
    )


# ── ADMIN: /rmchn ───────────────────────────
@bot.message_handler(commands=["rmchn"])
async def cmd_rmchn(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await bot.send_message(msg.chat.id, f"{CROSS} *Admin only command!*", parse_mode="Markdown")
        return

    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(
            msg.chat.id,
            f"{WARN} *Usage:* `/rmchn <invite_url>`",
            parse_mode="Markdown"
        )
        return

    url = parts[1].strip()
    db_remove_channel(url)
    await bot.send_message(
        msg.chat.id,
        f"{TICK} *Channel Removed!*\n\n{CHAIN} `{url}`",
        parse_mode="Markdown"
    )


# ── ADMIN: /listchn ─────────────────────────
@bot.message_handler(commands=["listchn"])
async def cmd_listchn(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await bot.send_message(msg.chat.id, f"{CROSS} *Admin only command!*", parse_mode="Markdown")
        return

    rows = db_get_channels()
    if not rows:
        await bot.send_message(
            msg.chat.id,
            f"{INFO} *No channels added yet.*\n\nUse `/addchn <url>` to add one.",
            parse_mode="Markdown"
        )
        return

    lines = []
    for i, (url, cid) in enumerate(rows, 1):
        cid_txt = f"`{cid}`" if cid else "_not resolved_"
        lines.append(f"{i}\\. {CHAIN} {url}\n    ID: {cid_txt}")

    text = f"{STAR} *Registered Channels ({len(rows)}):*\n\n" + "\n\n".join(lines)
    await bot.send_message(msg.chat.id, text, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  CALLBACK: Verify button
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "verify_me")
async def cb_verify(call: types.CallbackQuery):
    uid  = call.from_user.id
    name = call.from_user.first_name or "User"

    await bot.answer_callback_query(call.id, "⏳ Checking your membership…")

    ok, missing = await check_user_in_channels(uid)
    if ok:
        text = (
            f"{TICK} *Verification Successful!*\n\n"
            f"Welcome *{name}*! You have access to all channels.\n"
            f"{ROCKET} Full access granted!"
        )
        await bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
    else:
        lines = "\n".join([f"  {CHAIN} Channel {i+1}" for i, _ in enumerate(missing)])
        text = (
            f"{CROSS} *Still Not Verified!*\n\n"
            f"You haven't requested access to:\n{lines}\n\n"
            f"{WARN} _Please send a join request first, then press Verify Me._"
        )
        await bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=build_join_keyboard(missing)
        )


# ─────────────────────────────────────────────
#  CATCH-ALL  (demo echo — replace with your features)
# ─────────────────────────────────────────────
@bot.message_handler(func=lambda m: True)
async def catch_all(msg: types.Message):
    if not await gate(msg):
        return
    await bot.send_message(
        msg.chat.id,
        f"{TICK} *You are verified!*\n\n_Your message:_ `{msg.text}`",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  FLASK API  (used by Telebot Creator / TPY)
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "status": "online",
        "service": "ForceSub API",
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"pong": True})


@app.route("/api/check", methods=["GET"])
def api_check():
    """
    GET /api/check?user_id=123456789
    Returns whether the user has joined all channels.
    Use this endpoint in Telebot Creator (TPY) as an HTTP request node.
    """
    uid = request.args.get("user_id", "")
    if not uid or not uid.isdigit():
        return jsonify({"error": "Missing or invalid user_id"}), 400

    # Run async function in a new event loop (Flask is sync)
    loop   = asyncio.new_event_loop()
    ok, missing = loop.run_until_complete(check_user_in_channels(int(uid)))
    loop.close()

    channels = db_get_channels()
    return jsonify({
        "user_id":    int(uid),
        "verified":   ok,
        "missing":    missing,
        "channels":   [{"url": u, "id": c} for u, c in channels]
    })


@app.route("/api/channels", methods=["GET"])
def api_channels():
    """GET /api/channels — list all registered channels"""
    rows = db_get_channels()
    return jsonify({
        "count":    len(rows),
        "channels": [{"url": u, "id": c} for u, c in rows]
    })


@app.route("/api/addchn", methods=["POST"])
def api_addchn():
    """
    POST /api/addchn
    Body JSON: {"url": "https://t.me/+xxx", "admin_id": 12345}
    """
    data     = request.get_json(force=True) or {}
    url      = data.get("url", "").strip()
    admin_id = int(data.get("admin_id", 0))

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not is_admin(admin_id):
        return jsonify({"error": "Unauthorized"}), 403

    loop = asyncio.new_event_loop()
    cid  = loop.run_until_complete(resolve_channel_id(url))
    loop.close()

    db_add_channel(url, cid)
    return jsonify({"success": True, "url": url, "channel_id": cid})


@app.route("/api/rmchn", methods=["POST"])
def api_rmchn():
    """
    POST /api/rmchn
    Body JSON: {"url": "https://t.me/+xxx", "admin_id": 12345}
    """
    data     = request.get_json(force=True) or {}
    url      = data.get("url", "").strip()
    admin_id = int(data.get("admin_id", 0))

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not is_admin(admin_id):
        return jsonify({"error": "Unauthorized"}), 403

    db_remove_channel(url)
    return jsonify({"success": True, "url": url})


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """Telegram webhook endpoint"""
    json_data = request.get_json(force=True)
    update    = types.Update.de_json(json_data)
    loop      = asyncio.new_event_loop()
    loop.run_until_complete(bot.process_new_updates([update]))
    loop.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  AUTO-PING  (keeps Render free tier awake)
# ─────────────────────────────────────────────
def auto_ping():
    if not RENDER_URL:
        log.info("RENDER_URL not set — auto-ping disabled")
        return
    while True:
        try:
            r = httpx.get(f"{RENDER_URL}/ping", timeout=10)
            log.info(f"Auto-ping → {r.status_code}")
        except Exception as e:
            log.warning(f"Auto-ping failed: {e}")
        time.sleep(300)   # every 5 minutes


# ─────────────────────────────────────────────
#  STARTUP
# ─────────────────────────────────────────────
async def set_webhook():
    if not RENDER_URL:
        log.warning("RENDER_URL not set — running in polling mode")
        return False
    wh_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot.set_webhook(wh_url)
    log.info(f"Webhook set → {wh_url}")
    return True


def run_bot_polling():
    """Fallback polling (local dev)"""
    async def _poll():
        await bot.infinity_polling()
    asyncio.run(_poll())


if __name__ == "__main__":
    init_db()

    # Start auto-ping in background
    threading.Thread(target=auto_ping, daemon=True).start()

    loop = asyncio.new_event_loop()
    use_webhook = loop.run_until_complete(set_webhook())
    loop.close()

    if use_webhook:
        log.info(f"Starting Flask on port {PORT}")
        app.run(host="0.0.0.0", port=PORT)
    else:
        log.info("Starting in polling mode")
        run_bot_polling()
