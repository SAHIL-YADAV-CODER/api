import os
import asyncio
import logging
import threading
import time
import json
import httpx
from datetime import datetime
from flask import Flask, request, jsonify
from telebot.async_telebot import AsyncTeleBot
from telebot import types

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "8915358086:AAELvKimuQQLc9GfO7pSp-dv2eD7cEfZeRw")
TURSO_URL   = os.environ.get("TURSO_URL",  "libsql://escrow-escrow.aws-ap-south-1.turso.io")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODAzMzQ1NjEsImlkIjoiMDE5ZTg0MzQtYWUwMS03NWJmLTgyZTMtNTZiODBhNGVhMTBkIiwicmlkIjoiMDY2NzJhNDMtNjNiYy00YTg3LWFkZDEtZDIyNmMyZDJlNTc3In0.qVH0T7oJ_ZO7xDnC48LwBqGM-0C7edjuKS3sf_0jT2oySiaVgxKSlO0UiIHwiZFD-sf94anNbBrHeCNtJfo7Cw")
ADMIN_IDS   = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
RENDER_URL  = os.environ.get("RENDER_URL", "")
PORT        = int(os.environ.get("PORT", 5000))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

bot = AsyncTeleBot(BOT_TOKEN)
app = Flask(__name__)

# ─────────────────────────────────────────────
#  TURSO HTTP API  (no libsql driver needed)
# ─────────────────────────────────────────────
TURSO_HTTP  = TURSO_URL.replace("libsql://", "https://")
TURSO_HDRS  = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type":  "application/json"
}

def turso_execute(statements: list) -> list:
    """
    Execute a list of SQL statements via Turso HTTP API.
    Each statement: {"q": "SQL", "params": [...]}  or just {"q": "SQL"}
    Returns list of result objects.
    """
    payload = {"requests": []}
    for s in statements:
        if isinstance(s, str):
            payload["requests"].append({"type": "execute", "stmt": {"sql": s}})
        else:
            stmt = {"sql": s["q"]}
            if s.get("params"):
                stmt["args"] = [{"type": "text", "value": str(p)} for p in s["params"]]
            payload["requests"].append({"type": "execute", "stmt": stmt})
    payload["requests"].append({"type": "close"})

    r = httpx.post(f"{TURSO_HTTP}/v2/pipeline", headers=TURSO_HDRS,
                   json=payload, timeout=10)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results

def turso_query(sql: str, params: list = None) -> list:
    """Run a single SELECT and return rows as list of dicts."""
    results = turso_execute([{"q": sql, "params": params or []}])
    if not results:
        return []
    res = results[0]
    if res.get("type") == "error":
        raise Exception(res.get("error", {}).get("message", "Unknown error"))
    rows_data = res.get("response", {}).get("result", {}).get("rows", [])
    cols      = res.get("response", {}).get("result", {}).get("cols", [])
    col_names = [c["name"] for c in cols]
    rows = []
    for row in rows_data:
        rows.append({col_names[i]: (cell.get("value") if cell.get("type") != "null" else None)
                     for i, cell in enumerate(row)})
    return rows

def turso_run(sql: str, params: list = None):
    """Run a single INSERT/UPDATE/DELETE."""
    turso_execute([{"q": sql, "params": params or []}])

# ─────────────────────────────────────────────
#  DATABASE INIT & HELPERS
# ─────────────────────────────────────────────
def init_db():
    turso_run("""
        CREATE TABLE IF NOT EXISTS channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_url TEXT    NOT NULL UNIQUE,
            channel_id  TEXT,
            added_at    TEXT    DEFAULT (datetime('now'))
        )
    """)
    log.info("✅ Database initialised")

def db_add_channel(url: str, cid: str = None):
    turso_run(
        "INSERT OR IGNORE INTO channels (channel_url, channel_id) VALUES (?, ?)",
        [url, cid or ""]
    )

def db_remove_channel(url: str):
    turso_run("DELETE FROM channels WHERE channel_url = ?", [url])

def db_get_channels():
    rows = turso_query("SELECT channel_url, channel_id FROM channels")
    return [(r["channel_url"], r["channel_id"]) for r in rows]

def db_update_channel_id(url: str, cid: str):
    turso_run("UPDATE channels SET channel_id = ? WHERE channel_url = ?", [cid, url])

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

async def resolve_channel_id(invite_url: str):
    try:
        if "t.me/+" in invite_url or "t.me/joinchat" in invite_url:
            return None
        part = invite_url.rstrip("/").split("/")[-1]
        if not part.startswith("+"):
            chat = await bot.get_chat(f"@{part}")
            return str(chat.id)
    except Exception as e:
        log.warning(f"Could not resolve channel ID for {invite_url}: {e}")
    return None

async def check_user_in_channels(user_id: int):
    channels = db_get_channels()
    if not channels:
        return True, []

    not_joined = []
    for url, cid in channels:
        if not cid:
            cid = await resolve_channel_id(url)
            if cid:
                db_update_channel_id(url, cid)
        if not cid:
            continue
        try:
            member = await bot.get_chat_member(cid, user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(url)
        except Exception as e:
            log.warning(f"get_chat_member error for {cid}: {e}")
            not_joined.append(url)

    return len(not_joined) == 0, not_joined

def build_join_keyboard(not_joined: list) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, url in enumerate(not_joined, 1):
        kb.add(types.InlineKeyboardButton(text=f"📢  Join Channel {idx}", url=url))
    kb.add(types.InlineKeyboardButton(
        text=f"{TICK}  I've Requested — Verify Me",
        callback_data="verify_me"
    ))
    return kb

async def gate(message: types.Message) -> bool:
    uid = message.from_user.id
    if is_admin(uid):
        return True
    ok, missing = await check_user_in_channels(uid)
    if ok:
        return True
    lines = "\n".join([f"  {CHAIN} Channel {i+1}" for i, _ in enumerate(missing)])
    text = (
        f"{LOCK} *Access Restricted\\!*\n\n"
        f"To use this bot you must *request to join* our channel\\(s\\):\n\n"
        f"{lines}\n\n"
        f"{INFO} _Tap the button below → send a join request → then press_ *Verify Me*\\.\n"
        f"{WARN} _You do not need to be accepted — just sending the request is enough\\!_"
    )
    await bot.send_message(message.chat.id, text, parse_mode="MarkdownV2",
                           reply_markup=build_join_keyboard(missing))
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
        f"{ROCKET} *Welcome, {name}\\!*\n\n"
        f"{STAR} You have been *verified* and granted full access\\.\n\n"
        f"{KEY} *Available Commands:*\n"
        f"  `/start`   — Show this welcome message\n"
        f"  `/help`    — Get help & info\n"
        f"  `/verify`  — Re\\-check your membership\n\n"
        f"_Enjoy using the bot\\!_ {BELL}"
    )
    await bot.send_message(msg.chat.id, text, parse_mode="MarkdownV2")


@bot.message_handler(commands=["help"])
async def cmd_help(msg: types.Message):
    if not await gate(msg):
        return
    text = (
        f"{INFO} *Help & Information*\n\n"
        f"This bot uses *Force Subscribe* — you must join our channel\\(s\\) "
        f"before using any features\\.\n\n"
        f"{CROWN} *Admin Commands:*\n"
        f"  `/addchn <invite\\_url>` — Add a channel\n"
        f"  `/rmchn  <invite\\_url>` — Remove a channel\n"
        f"  `/listchn`             — List all channels\n\n"
        f"{STAR} *User Commands:*\n"
        f"  `/start`  — Start the bot\n"
        f"  `/verify` — Check your membership status\n"
    )
    await bot.send_message(msg.chat.id, text, parse_mode="MarkdownV2")


@bot.message_handler(commands=["verify"])
async def cmd_verify(msg: types.Message):
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "User"
    ok, missing = await check_user_in_channels(uid)
    if ok:
        await bot.send_message(msg.chat.id,
            f"{TICK} *Verification Successful\\!*\n\nHello *{name}*, you are a member of all required channels\\.\n{ROCKET} Full access granted\\!",
            parse_mode="MarkdownV2")
    else:
        lines = "\n".join([f"  {CHAIN} Channel {i+1}" for i, _ in enumerate(missing)])
        await bot.send_message(msg.chat.id,
            f"{CROSS} *Verification Failed\\!*\n\nYou still need to request access to:\n{lines}\n\n{WARN} _Send a join request, then press Verify Me\\._",
            parse_mode="MarkdownV2", reply_markup=build_join_keyboard(missing))


@bot.message_handler(commands=["addchn"])
async def cmd_addchn(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await bot.send_message(msg.chat.id, f"{CROSS} *Admin only command\\!*", parse_mode="MarkdownV2")
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(msg.chat.id,
            f"{WARN} *Usage:* `/addchn <invite\\_url>`\n\nExample: `/addchn https://t\\.me/\\+AbCdEfGhIjK`",
            parse_mode="MarkdownV2")
        return
    url = parts[1].strip()
    cid = await resolve_channel_id(url)
    db_add_channel(url, cid)
    resolved = f"\n{INFO} Channel ID resolved: `{cid}`" if cid else f"\n{WARN} _Make the bot an admin in the channel to resolve ID\\._"
    safe_url = url.replace(".", "\\.").replace("+", "\\+").replace("-", "\\-")
    await bot.send_message(msg.chat.id,
        f"{TICK} *Channel Added\\!*\n\n{CHAIN} `{safe_url}`{resolved}",
        parse_mode="MarkdownV2")


@bot.message_handler(commands=["rmchn"])
async def cmd_rmchn(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await bot.send_message(msg.chat.id, f"{CROSS} *Admin only command\\!*", parse_mode="MarkdownV2")
        return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await bot.send_message(msg.chat.id, f"{WARN} *Usage:* `/rmchn <invite\\_url>`", parse_mode="MarkdownV2")
        return
    url = parts[1].strip()
    db_remove_channel(url)
    safe_url = url.replace(".", "\\.").replace("+", "\\+").replace("-", "\\-")
    await bot.send_message(msg.chat.id,
        f"{TICK} *Channel Removed\\!*\n\n{CHAIN} `{safe_url}`",
        parse_mode="MarkdownV2")


@bot.message_handler(commands=["listchn"])
async def cmd_listchn(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await bot.send_message(msg.chat.id, f"{CROSS} *Admin only command\\!*", parse_mode="MarkdownV2")
        return
    rows = db_get_channels()
    if not rows:
        await bot.send_message(msg.chat.id,
            f"{INFO} *No channels added yet\\.*\n\nUse `/addchn <url>` to add one\\.",
            parse_mode="MarkdownV2")
        return
    lines = []
    for i, (url, cid) in enumerate(rows, 1):
        cid_txt = f"`{cid}`" if cid else "_not resolved_"
        safe_url = url.replace(".", "\\.").replace("+", "\\+").replace("-", "\\-")
        lines.append(f"{i}\\. {CHAIN} {safe_url}\n    ID: {cid_txt}")
    await bot.send_message(msg.chat.id,
        f"{STAR} *Registered Channels \\({len(rows)}\\):*\n\n" + "\n\n".join(lines),
        parse_mode="MarkdownV2")


@bot.callback_query_handler(func=lambda c: c.data == "verify_me")
async def cb_verify(call: types.CallbackQuery):
    uid  = call.from_user.id
    name = call.from_user.first_name or "User"
    await bot.answer_callback_query(call.id, "⏳ Checking your membership…")
    ok, missing = await check_user_in_channels(uid)
    if ok:
        await bot.edit_message_text(
            f"{TICK} *Verification Successful\\!*\n\nWelcome *{name}*\\! You have access to all channels\\.\n{ROCKET} Full access granted\\!",
            call.message.chat.id, call.message.message_id, parse_mode="MarkdownV2")
    else:
        lines = "\n".join([f"  {CHAIN} Channel {i+1}" for i, _ in enumerate(missing)])
        await bot.edit_message_text(
            f"{CROSS} *Still Not Verified\\!*\n\nYou haven't requested access to:\n{lines}\n\n{WARN} _Please send a join request first, then press Verify Me\\._",
            call.message.chat.id, call.message.message_id,
            parse_mode="MarkdownV2", reply_markup=build_join_keyboard(missing))


@bot.message_handler(func=lambda m: True)
async def catch_all(msg: types.Message):
    if not await gate(msg):
        return
    await bot.send_message(msg.chat.id,
        f"{TICK} *You are verified\\!*",
        parse_mode="MarkdownV2")


# ─────────────────────────────────────────────
#  FLASK
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "online", "service": "ForceSub Bot", "timestamp": datetime.utcnow().isoformat()})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"pong": True})

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    json_data = request.get_json(force=True)
    update    = types.Update.de_json(json_data)
    loop      = asyncio.new_event_loop()
    loop.run_until_complete(bot.process_new_updates([update]))
    loop.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────
#  AUTO-PING
# ─────────────────────────────────────────────
def auto_ping():
    if not RENDER_URL:
        return
    while True:
        try:
            r = httpx.get(f"{RENDER_URL}/ping", timeout=10)
            log.info(f"Auto-ping → {r.status_code}")
        except Exception as e:
            log.warning(f"Auto-ping failed: {e}")
        time.sleep(270)

# ─────────────────────────────────────────────
#  STARTUP
# ─────────────────────────────────────────────
async def set_webhook():
    if not RENDER_URL:
        return False
    wh_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot.set_webhook(wh_url)
    log.info(f"Webhook set → {wh_url}")
    return True

if __name__ == "__main__":
    init_db()
    threading.Thread(target=auto_ping, daemon=True).start()
    loop = asyncio.new_event_loop()
    use_webhook = loop.run_until_complete(set_webhook())
    loop.close()
    if use_webhook:
        log.info(f"🚀 Starting Flask on port {PORT}")
        app.run(host="0.0.0.0", port=PORT)
    else:
        log.info("Starting in polling mode")
        asyncio.run(bot.infinity_polling())
