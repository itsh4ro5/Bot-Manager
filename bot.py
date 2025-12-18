# -*- coding: utf-8 -*-

"""
ULTIMATE BOT MANAGER (v9.7 - Fixes & Enhanced Discovery)
Fixes Implemented:
1. /batches: Shows ALL connected chats (Passive Discovery added).
2. /user: Only lists chats where user is ACTUALLY joined (Filters out 'Left'/'Kicked').

FEATURES INCLUDED:
1.  Owner Commands: /addadmin, /deladmin, /backup, /allusers
2.  Admin Commands: /stats, /user, /addbatch, /delbatch, /broadcast, /post, /cancel
    **NEW:** /batches (List all connected chats/batches)
3.  User Features: 
    - Professional Welcome (Separate for Owner/Admin/User)
    - Free/Paid Batches Menus
    - Ticket Support System
4.  Automation:
    - 3-Hour Demo Timer (Restart Proof)
    - Auto-Kick on Expiry
    - Smart Topic Creation (No Duplicates)
    - History Search Link in Tickets
    - Admin Command Auto-Deletion (20 mins)
    - Bidirectional Message Edit & Reaction Sync
    - Auto-Detect Batch Name from Channel ID
    - **Pre-Link Membership Check** (Checks if user is already in channel before sending link)
    - **Auto-Approve Free Batches** (Only if Mandatory Channel Joined)
    - **Deep User Scan** (Checks all known batches, filters only joined ones)
    - **Auto-Track Bot Membership** (Saves any chat bot is added to or active in)
5.  Persistence:
    - MongoDB Support (Critical for remembering Topics after redeploy)
    - Local JSON Fallback
"""

import logging
import json
import os
import io
import asyncio
import time
import threading
from datetime import datetime
from telegram import (
    Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, 
    BotCommandScopeChat, ChatJoinRequest
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError, BadRequest, Forbidden
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, ChatMemberHandler, 
    CallbackQueryHandler, MessageHandler, filters, Application, ChatJoinRequestHandler,
    MessageReactionHandler
)

# --- 1. LOGGING & SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. FLASK KEEPALIVE SERVER ---
try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "8080"))
        app = Flask(__name__)
        @app.route('/')
        def index(): return "Bot Running - v9.7 Fixes", 200
        
        def run():
            app.run(host="0.0.0.0", port=port, use_reloader=False)
        
        t = threading.Thread(target=run, daemon=True)
        t.start()
except ImportError:
    def _start_keepalive(): pass
_start_keepalive()

# --- 3. CONFIGURATION & DEFAULTS ---
DEFAULTS = {
    "TOKEN": "", 
    "OWNER": 0,
    "SUPPORT": 0,
    "MAIN_CH": 0,
    "LOG_CH": 0
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULTS["TOKEN"])
OWNER_ID = int(os.environ.get("OWNER_ID", DEFAULTS["OWNER"]))
SUPPORT_GROUP_ID = int(os.environ.get("SUPPORT_GROUP_ID", DEFAULTS["SUPPORT"]))
MANDATORY_CHANNEL_ID = int(os.environ.get("MANDATORY_CHANNEL_ID", DEFAULTS["MAIN_CH"]))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", DEFAULTS["LOG_CH"]))
MONGO_URL = os.environ.get("MONGO_URL", None) 

MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK", "https://t.me/YourChannel")
DATA_FILE = os.environ.get("DATA_FILE", "bot_data.json")

# --- 4. DATABASE & MEMORY ---
DB = {
    "ADMIN_IDS": [],
    "FREE_CHANNELS": {},
    "PAID_CHANNELS": {},
    "ALL_CHATS": {},     # Track ALL chats bot is in
    "USER_DATA": {},
    "BLOCKED_USERS": [],
    "USER_TOPICS": {}, 
    "PENDING_REQUESTS": {} 
}

# Runtime Memory
MESSAGE_MAP = {} 
ADMIN_WIZARD = {} 
BROADCAST_STATE = {} 
TOPIC_CREATION_LOCK = set()

data_lock = asyncio.Lock()

# MongoDB Setup
mongo_client = None
mongo_collection = None

if MONGO_URL:
    try:
        from pymongo import MongoClient
        import certifi
        mongo_client = MongoClient(MONGO_URL, tlsCAFile=certifi.where())
        mongo_db = mongo_client.get_database("telegram_bot_db")
        mongo_collection = mongo_db.get_collection("bot_settings")
        logger.info("✅ Connected to MongoDB Atlas")
    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        MONGO_URL = None

# --- 5. PERSISTENCE FUNCTIONS ---
def load_data():
    global DB
    
    # Try loading from MongoDB first
    if MONGO_URL and mongo_collection is not None:
        try:
            data = mongo_collection.find_one({"_id": "main_settings"})
            if data and "data" in data:
                loaded = data["data"]
                # Convert string keys back to integers
                for k in ["FREE_CHANNELS", "PAID_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                    if k in loaded:
                        DB[k] = {int(i): v for i, v in loaded[k].items()}
                
                if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = loaded["ADMIN_IDS"]
                if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
                
                if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
                
                # Sync lists to ALL_CHATS for legacy support
                for cid, name in DB["FREE_CHANNELS"].items():
                    if cid not in DB["ALL_CHATS"]: DB["ALL_CHATS"][cid] = name
                for cid, name in DB["PAID_CHANNELS"].items():
                    if cid not in DB["ALL_CHATS"]: DB["ALL_CHATS"][cid] = name
                    
                logger.info("✅ Database loaded from MongoDB.")
                return
        except Exception as e:
            logger.error(f"MongoDB Load Error: {e}")

    # Fallback to Local JSON
    if not os.path.exists(DATA_FILE):
        save_data_sync()
        return

    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = loaded["ADMIN_IDS"]
            if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
            for k in ["FREE_CHANNELS", "PAID_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                if k in loaded:
                    DB[k] = {int(i): v for i, v in loaded[k].items()}

            if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
            
            # Sync legacy
            for cid, name in DB["FREE_CHANNELS"].items():
                if cid not in DB["ALL_CHATS"]: DB["ALL_CHATS"][cid] = name
            for cid, name in DB["PAID_CHANNELS"].items():
                if cid not in DB["ALL_CHATS"]: DB["ALL_CHATS"][cid] = name
                
            logger.info("Database loaded from Local File.")
    except Exception as e:
        logger.error(f"Local Load Error: {e}")

def save_data_sync():
    try:
        to_save = {
            "ADMIN_IDS": DB["ADMIN_IDS"],
            "BLOCKED_USERS": DB["BLOCKED_USERS"],
            "FREE_CHANNELS": {str(k): v for k, v in DB["FREE_CHANNELS"].items()},
            "PAID_CHANNELS": {str(k): v for k, v in DB["PAID_CHANNELS"].items()},
            "ALL_CHATS": {str(k): v for k, v in DB["ALL_CHATS"].items()},
            "USER_DATA": {str(k): v for k, v in DB["USER_DATA"].items()},
            "USER_TOPICS": {str(k): v for k, v in DB["USER_TOPICS"].items()},
            "PENDING_REQUESTS": {str(k): v for k, v in DB["PENDING_REQUESTS"].items()}
        }

        if MONGO_URL and mongo_collection is not None:
            try:
                mongo_collection.replace_one(
                    {"_id": "main_settings"},
                    {"_id": "main_settings", "data": to_save},
                    upsert=True
                )
            except Exception as e:
                logger.error(f"MongoDB Save Error: {e}")
        
        with open(DATA_FILE, "w") as f:
            json.dump(to_save, f, indent=4)
            
    except Exception as e:
        logger.error(f"Save Error: {e}")

async def save_data_async():
    async with data_lock:
        await asyncio.to_thread(save_data_sync)

# --- 6. CORE HELPERS ---

def is_admin(uid):
    return uid == OWNER_ID or uid in DB["ADMIN_IDS"]

async def check_membership(user_id, context):
    """Checks if user is in Mandatory Channel."""
    if is_admin(user_id) or not MANDATORY_CHANNEL_ID: return True
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        return m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except: return True

async def is_already_in_channel(context, chat_id, user_id):
    """Checks if user is ALREADY in the target batch."""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            return True
        return False
    except BadRequest:
        return False 
    except Exception:
        return False

async def delete_later(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try: await context.bot.delete_message(chat_id=job.data['chat_id'], message_id=job.data['msg_id'])
    except: pass

async def schedule_delete(context, message):
    if message:
        context.job_queue.run_once(delete_later, 1200, data={'chat_id': message.chat.id, 'msg_id': message.message_id})

async def get_or_create_topic(user, context):
    """
    Creates or retrieves a forum topic.
    CRITICAL: Relies on DB to avoid duplicates on redeploy.
    """
    if not SUPPORT_GROUP_ID: return None
    
    # 1. Check DB first (To avoid creating duplicate if already known)
    if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]

    # 2. Lock to prevent race conditions
    if user.id in TOPIC_CREATION_LOCK:
        await asyncio.sleep(1) 
        if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]
    
    TOPIC_CREATION_LOCK.add(user.id)
    try:
        # Create new topic
        name = f"{user.first_name[:20]} ({user.id})"
        topic = await context.bot.create_forum_topic(SUPPORT_GROUP_ID, name)
        
        DB["USER_TOPICS"][user.id] = topic.message_thread_id
        await save_data_async()
        
        # Initial Message
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        search_url = f"https://t.me/c/{group_id_str}?q={user.id}"
        lang = user.language_code or "N/A"
        
        text = (
            f"👤 **NEW USER TICKET**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📛 **Name:** {user.full_name}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"🔗 **Username:** @{user.username if user.username else 'None'}\n"
            f"🌐 **Lang:** {lang}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📜 [Click to Check History]({search_url})"
        )
        
        await context.bot.send_message(
            SUPPORT_GROUP_ID, text,
            message_thread_id=topic.message_thread_id,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        return topic.message_thread_id
    except Exception as e:
        logger.error(f"Topic Creation Error: {e}")
        return None
    finally:
        TOPIC_CREATION_LOCK.discard(user.id)

# --- 7. AUTO-TRACK CHATS (NEW) ---

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Automatically tracks ALL chats where the bot is added as Admin/Member.
    This enables the /batches command to see everything.
    """
    if not update.my_chat_member: return
    
    chat = update.my_chat_member.chat
    new_status = update.my_chat_member.new_chat_member.status
    
    # Bot was added or promoted
    if new_status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]:
        if chat.id not in DB["ALL_CHATS"]:
            DB["ALL_CHATS"][chat.id] = chat.title or f"Chat {chat.id}"
            await save_data_async()
            logger.info(f"✅ Added to new chat: {chat.title} ({chat.id})")
    
    # Bot was removed or left
    elif new_status in [ChatMember.LEFT, ChatMember.KICKED]:
        if chat.id in DB["ALL_CHATS"]:
            # Only remove if not in manual lists (optional safety)
            if chat.id not in DB["FREE_CHANNELS"] and chat.id not in DB["PAID_CHANNELS"]:
                del DB["ALL_CHATS"][chat.id]
                await save_data_async()

# --- 8. COMMAND HANDLERS ---

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg_obj = update.effective_message
    
    text = ""
    if chat.type == ChatType.PRIVATE:
        if user: text = f"👤 **Your User ID:** `{user.id}`"
        else: text = f"🆔 **Chat ID:** `{chat.id}`"
    else:
        text = f"🆔 **Chat ID:** `{chat.id}`"
        if msg_obj and msg_obj.is_topic_message and msg_obj.message_thread_id:
            text += f"\n🧵 **Topic ID:** `{msg_obj.message_thread_id}`"
        if user:
            text += f"\n👤 **User ID:** `{user.id}`"
            
    try:
        if msg_obj: await msg_obj.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        else: await context.bot.send_message(chat.id, text, parse_mode=ParseMode.MARKDOWN)
    except: pass
    
    if user and is_admin(user.id):
        await schedule_delete(context, msg_obj)

async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_admin = int(context.args[0])
        if new_admin not in DB["ADMIN_IDS"]:
            DB["ADMIN_IDS"].append(new_admin)
            await save_data_async()
            msg = await update.message.reply_text(f"✅ User {new_admin} is now Admin.")
        else: msg = await update.message.reply_text("⚠️ Already Admin.")
    except: msg = await update.message.reply_text("Usage: /addadmin [user_id]")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target = int(context.args[0])
        if target in DB["ADMIN_IDS"] and target != OWNER_ID:
            DB["ADMIN_IDS"].remove(target)
            await save_data_async()
            msg = await update.message.reply_text(f"🗑 User {target} removed from Admin.")
        else: msg = await update.message.reply_text("⚠️ Cannot remove.")
    except: msg = await update.message.reply_text("Usage: /deladmin [user_id]")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    save_data_sync()
    if os.path.exists(DATA_FILE):
        await update.message.reply_document(document=open(DATA_FILE, "rb"), caption="DB Backup (JSON)")
    else:
        msg = await update.message.reply_text("No DB file found locally.")
        await schedule_delete(context, msg)
    await schedule_delete(context, update.message)

async def cmd_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    msg = await update.message.reply_text("⏳ Generating report...")
    report = f"ALL USERS DUMP - {datetime.now()}\n" + "-" * 40 + "\nID | Name | Username\n"
    for uid, data in DB["USER_DATA"].items():
        report += f"{uid} | {data.get('name')} | @{data.get('username')}\n"
    f = io.BytesIO(report.encode("utf-8"))
    f.name = "all_users.txt"
    await update.message.reply_document(document=f, caption="✅ All Users List")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

# /user [id] (Enhanced - Scans ALL batches)
async def cmd_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target_id = int(context.args[0])
    except: 
        msg = await update.message.reply_text("Usage: /user [id]")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return

    info = DB["USER_DATA"].get(target_id)
    msg = await update.message.reply_text("🔍 Scanning ALL connected batches... This might take a moment.")
    
    report = f"USER DETAILS REPORT: {target_id}\n"
    report += f"Name: {info.get('name') if info else 'Unknown'}\n"
    report += f"Joined Bot: {time.ctime(info.get('joined_at',0)) if info else 'Unknown'}\n\n"
    report += "--- BATCH MEMBERSHIP STATUS (JOINED ONLY) ---\n"
    
    # FIX 1: Ensure all keys are captured (Passive discovery relies on DB["ALL_CHATS"] being populated)
    all_known_chats = set(list(DB["ALL_CHATS"].keys()) + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys()))
    
    found_any = False
    for cid in all_known_chats:
        cname = DB["ALL_CHATS"].get(cid) or DB["FREE_CHANNELS"].get(cid) or DB["PAID_CHANNELS"].get(cid) or f"Unknown {cid}"
        
        # Determine Type
        b_type = "OTHER"
        if cid in DB["FREE_CHANNELS"]: b_type = "FREE"
        elif cid in DB["PAID_CHANNELS"]: b_type = "PAID"
        elif cid == SUPPORT_GROUP_ID: b_type = "SUPPORT"
        elif cid == MANDATORY_CHANNEL_ID: b_type = "MAIN"
        elif cid == LOG_CHANNEL_ID: b_type = "LOG"
        
        try:
            m = await context.bot.get_chat_member(cid, target_id)
            # FIX 2: Filter to ONLY show Joined/Admin status
            if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]:
                report += f"[{b_type}] {cname}: {m.status.upper()} ✅\n"
                found_any = True
            # We purposely do NOT log "LEFT", "KICKED" or "Not Found"
        except TelegramError:
            pass
            
    if not found_any:
        report += "User not found in any connected batches.\n"

    f = io.BytesIO(report.encode("utf-8"))
    f.name = f"user_scan_{target_id}.txt"
    await update.message.reply_document(document=f, caption=f"🔍 Deep Scan Result for {target_id} (Joined Only)")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

# /batches (NEW COMMAND)
async def cmd_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    
    msg = await update.message.reply_text("⏳ Compiling list of ALL connected batches...")
    
    report = f"ALL CONNECTED BATCHES REPORT - {datetime.now()}\n"
    report += "This list includes every group/channel the bot knows about.\n"
    report += "=" * 60 + "\n"
    report += f"{'TYPE':<10} | {'ID':<15} | {'NAME'}\n"
    report += "-" * 60 + "\n"
    
    # Use ALL_CHATS as the source of truth for "connected" chats
    all_keys = set(list(DB["ALL_CHATS"].keys()) + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys()))
    
    count = 0
    for cid in all_keys:
        cname = DB["ALL_CHATS"].get(cid) or DB["FREE_CHANNELS"].get(cid) or DB["PAID_CHANNELS"].get(cid) or "Unknown"
        
        b_type = "OTHER"
        if cid in DB["FREE_CHANNELS"]: b_type = "FREE"
        elif cid in DB["PAID_CHANNELS"]: b_type = "PAID"
        elif cid == SUPPORT_GROUP_ID: b_type = "SUPPORT"
        elif cid == MANDATORY_CHANNEL_ID: b_type = "MAIN"
        elif cid == LOG_CHANNEL_ID: b_type = "LOG"
        
        report += f"{b_type:<10} | {cid:<15} | {cname}\n"
        count += 1
        
    report += "=" * 60 + "\n"
    report += f"Total Connected Batches: {count}"
    
    f = io.BytesIO(report.encode("utf-8"))
    f.name = "all_batches_list.txt"
    
    await update.message.reply_document(document=f, caption=f"✅ Found {count} connected batches/chats.")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

# /stats (Admin)
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    
    total_users = len(DB['USER_DATA'])
    free_batches = len(DB['FREE_CHANNELS'])
    paid_batches = len(DB['PAID_CHANNELS'])
    all_chats_tracked = len(DB['ALL_CHATS'])
    blocked = len(DB['BLOCKED_USERS'])
    
    mode = "MongoDB Cloud ☁️" if MONGO_URL else "Local File 📁"

    t = (
        f"📊 **Statistics**\n"
        f"💾 **Storage:** {mode}\n"
        f"👥 Users: {total_users}\n"
        f"🆓 Free Batches: {free_batches}\n"
        f"💎 Paid Batches: {paid_batches}\n"
        f"📡 All Tracked Chats: {all_chats_tracked}\n"
        f"🚫 Blocked: {blocked}"
    )
    msg = await update.message.reply_text(t, parse_mode=ParseMode.MARKDOWN)
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

# /delbatch (Admin)
async def cmd_delbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        t, cid = context.args[0].lower(), int(context.args[1])
        d = DB["FREE_CHANNELS"] if t == "free" else DB["PAID_CHANNELS"]
        if cid in d: 
            del d[cid]
            await save_data_async()
            msg = await update.message.reply_text("✅ Batch Deleted")
        else: msg = await update.message.reply_text("❌ Batch ID not found in that category.")
    except: msg = await update.message.reply_text("Usage: /delbatch [free/paid] [id]")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in BROADCAST_STATE: del BROADCAST_STATE[uid]
    if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
    msg = await update.message.reply_text("❌ Operation Cancelled")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

# --- 9. WIZARD SYSTEM ---

async def cmd_addbatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    ADMIN_WIZARD[update.effective_user.id] = {"step": "ask_type"}
    kb = [[InlineKeyboardButton("Free", callback_data="wiz_free"), 
           InlineKeyboardButton("Paid", callback_data="wiz_paid")]]
    msg = await update.message.reply_text("🆕 **Add Batch Wizard**\nSelect Batch Type:", reply_markup=InlineKeyboardMarkup(kb))
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid not in ADMIN_WIZARD: await q.answer("Wizard Expired"); return
    data = q.data
    
    if data in ["wiz_free", "wiz_paid"]:
        ADMIN_WIZARD[uid]["type"] = "free" if data == "wiz_free" else "paid"
        ADMIN_WIZARD[uid]["step"] = "ask_id"
        await q.edit_message_text(
            f"Selected: **{data.split('_')[1].upper()}**\n\n"
            f"➡️ Send **Channel ID** (starts with -100):\n"
            f"ℹ️ *Bot must be admin there!*", 
            parse_mode=ParseMode.MARKDOWN
        )

async def wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return False
    uid = user.id
    txt = update.message.text
    if uid not in ADMIN_WIZARD: return False
    
    state = ADMIN_WIZARD[uid]
    if state["step"] == "ask_id":
        try:
            cid = int(txt)
            try:
                chat_obj = await context.bot.get_chat(cid)
                batch_name = chat_obj.title or f"Batch {cid}"
            except Exception:
                await update.message.reply_text("❌ **Error:** Could not fetch Channel.\nEnsure Bot is Admin there first!", parse_mode=ParseMode.MARKDOWN)
                return True

            target = DB["FREE_CHANNELS"] if state["type"] == "free" else DB["PAID_CHANNELS"]
            target[cid] = batch_name
            # Also add to ALL_CHATS
            DB["ALL_CHATS"][cid] = batch_name
            await save_data_async()
            
            msg = await update.message.reply_text(f"✅ **Batch Added!**\n\n📛 Name: {batch_name}\n🆔 ID: `{cid}`", parse_mode=ParseMode.MARKDOWN)
            del ADMIN_WIZARD[uid]
        except ValueError:
            msg = await update.message.reply_text("❌ Invalid ID format.")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return True
    return False

# --- 10. BROADCAST SYSTEM ---

async def cmd_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    BROADCAST_STATE[update.effective_user.id] = {"type": "broadcast", "step": "wait_msg"}
    msg = await update.message.reply_text("📢 **Broadcast Mode**\nSend the message to send to ALL users.\nType /cancel to stop.")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    BROADCAST_STATE[update.effective_user.id] = {"type": "post", "step": "wait_msg"}
    msg = await update.message.reply_text("📝 **Post Mode**\nSend the message to post to ALL Batches.\nType /cancel to stop.")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def handle_broadcast_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return False
    if user.id not in BROADCAST_STATE: return False
    
    state = BROADCAST_STATE[user.id]
    if state["step"] == "wait_msg":
        state["content"] = update.message
        state["step"] = "confirm"
        kb = [[InlineKeyboardButton("✅ YES, Send", callback_data="bc_yes"),
               InlineKeyboardButton("❌ NO, Cancel", callback_data="bc_no")]]
        txt = "📢 **Confirm Broadcast?**" if state["type"] == "broadcast" else "📝 **Confirm Post?**"
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return True
    return False

async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid not in BROADCAST_STATE: await q.answer("Expired"); return
    data = q.data
    state = BROADCAST_STATE[uid]
    
    if data == "bc_no":
        del BROADCAST_STATE[uid]
        await q.edit_message_text("❌ Action Cancelled")
        return
        
    if data == "bc_yes":
        await q.edit_message_text("⏳ Processing...")
        msg_obj = state["content"]
        count = 0
        
        if state["type"] == "broadcast":
            for target_id in list(DB["USER_DATA"].keys()):
                try:
                    await context.bot.copy_message(target_id, uid, msg_obj.message_id)
                    count += 1
                    await asyncio.sleep(0.05)
                except: pass
            await context.bot.send_message(uid, f"✅ **Broadcast Done**\nSent: {count}")

        elif state["type"] == "post":
            # Post to ALL known chats (Free + Paid + Others)
            targets = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
            for cid in targets:
                try:
                    await context.bot.copy_message(cid, uid, msg_obj.message_id)
                    count += 1
                    await asyncio.sleep(0.5)
                except: pass
            await context.bot.send_message(uid, f"✅ **Posting Done**\nPosted in {count} channels.")
            
        del BROADCAST_STATE[uid]

# --- 11. SYNC & MESSAGE HANDLER ---

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message_reaction: return
    r = update.message_reaction
    key = (r.chat.id, r.message_id)
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try: await context.bot.set_message_reaction(tc, tm, reaction=r.new_reaction)
        except: pass

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message: return
    m = update.edited_message
    key = (m.chat.id, m.message_id)
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        txt = f"✏️ [EDITED]\n{m.text or m.caption or 'Media'}"
        try: await context.bot.edit_message_text(txt, tc, tm)
        except: 
            try: await context.bot.edit_message_caption(tc, tm, caption=txt)
            except: pass

async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    # NEW: Passive Discovery - If message comes from a group, ensure it's in DB
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
        if chat.id not in DB["ALL_CHATS"]:
            DB["ALL_CHATS"][chat.id] = chat.title or f"Chat {chat.id}"
            await save_data_async()
            logger.info(f"✅ Discovered new connected chat: {chat.title}")

    if not user: return 

    if await wizard_message(update, context): return
    if await handle_broadcast_flow(update, context): return

    # User -> Admin
    if chat.type == ChatType.PRIVATE:
        if user.id in DB["BLOCKED_USERS"]: return
        
        # Safe Topic Retrieval
        topic_id = await get_or_create_topic(user, context)
        if topic_id:
            try:
                sent = await context.bot.copy_message(SUPPORT_GROUP_ID, chat.id, update.message.id, message_thread_id=topic_id)
                MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
            except Exception as e:
                # Retry if topic seems gone
                if "thread not found" in str(e).lower():
                    if user.id in DB["USER_TOPICS"]: del DB["USER_TOPICS"][user.id]
                    topic_id = await get_or_create_topic(user, context)
                    if topic_id:
                        try:
                            sent = await context.bot.copy_message(SUPPORT_GROUP_ID, chat.id, update.message.id, message_thread_id=topic_id)
                            MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                            MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
                        except: pass

    # Admin -> User
    elif chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        if update.message.from_user.id == context.bot.id: return 
        
        topic_id = update.message.message_thread_id
        target_uid = None
        for u, t in DB["USER_TOPICS"].items():
            if t == topic_id: target_uid = int(u); break
        
        if target_uid:
            try:
                sent = await context.bot.copy_message(target_uid, chat.id, update.message.id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, update.message.id)] = (target_uid, sent.message_id)
                MESSAGE_MAP[(target_uid, sent.message_id)] = (SUPPORT_GROUP_ID, update.message.id)
            except Forbidden:
                await context.bot.send_message(SUPPORT_GROUP_ID, "❌ User has blocked the bot.", message_thread_id=topic_id)
            except: pass

# --- 12. JOIN & DEMO ---

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles Join Requests for Free & Paid Batches.
    Auto-approves Free Batches IF Mandatory Channel is joined.
    """
    req = update.chat_join_request
    chat = req.chat
    user = req.from_user
    
    if chat.id in DB["FREE_CHANNELS"]:
        # Check Mandatory Membership
        if await check_membership(user.id, context):
            try:
                await context.bot.approve_chat_join_request(chat.id, user.id)
                await context.bot.send_message(user.id, f"✅ **Approved!**\nWelcome to {chat.title}", parse_mode=ParseMode.MARKDOWN)
            except: pass
        else:
            try:
                await context.bot.send_message(user.id, f"⚠️ **Declined!**\nJoin Main Channel first:\n{MANDATORY_CHANNEL_LINK}", parse_mode=ParseMode.MARKDOWN)
                await context.bot.decline_chat_join_request(chat.id, user.id)
            except: pass
    elif chat.id in DB["PAID_CHANNELS"]:
        # Paid batches require manual admin approval
        pass

async def on_join_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    if not cm: return
    user = cm.from_user
    chat = cm.chat
    
    if cm.new_chat_member.status == ChatMember.MEMBER and cm.old_chat_member.status != ChatMember.MEMBER:
        if user.id in DB["PENDING_REQUESTS"] and DB["PENDING_REQUESTS"][user.id] == chat.id:
            try: await context.bot.send_message(user.id, "✅ **Request Approved!**\nDemo Started.")
            except: pass
            
            expiry = time.time() + (3 * 3600)
            if "demos" not in DB["USER_DATA"][user.id]: DB["USER_DATA"][user.id]["demos"] = {}
            DB["USER_DATA"][user.id]["demos"][str(chat.id)] = expiry
            del DB["PENDING_REQUESTS"][user.id]
            await save_data_async()

async def check_demos(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    mod = False
    for uid, data in DB["USER_DATA"].items():
        if "demos" not in data: continue
        d = data["demos"].copy()
        for bid, expiry in d.items():
            if now > expiry:
                try: 
                    await context.bot.ban_chat_member(int(bid), uid)
                    await context.bot.unban_chat_member(int(bid), uid)
                    await context.bot.send_message(uid, "⏰ **Demo Expired.**")
                except: pass
                del data["demos"][bid]
                mod = True
    if mod: await save_data_async()

# --- 13. USER UI ---

async def general_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data

    if data.startswith("wiz_"): await wizard_callback(update, context); return
    if data.startswith("bc_"): await broadcast_callback(update, context); return

    if data == "verify":
        if await check_membership(uid, context):
            await q.answer("✅ Verified!")
            await show_user_menu(update)
        else: await q.answer("❌ Join Main Channel First!", show_alert=True)
    elif data == "u_main": await show_user_menu(update)
    elif data == "u_free":
        if not DB["FREE_CHANNELS"]: await q.answer("Empty", show_alert=True); return
        kb = [[InlineKeyboardButton(f"🔗 {n}", callback_data=f"get_f_{i}")] for i, n in DB["FREE_CHANNELS"].items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        await q.edit_message_text("📂 **Free Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif data == "u_paid":
        if not DB["PAID_CHANNELS"]: await q.answer("Empty", show_alert=True); return
        kb = [[InlineKeyboardButton(f"💎 {n}", callback_data=f"view_p_{i}")] for i, n in DB["PAID_CHANNELS"].items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        await q.edit_message_text("💎 **Premium Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    
    # --- GET FREE BATCH LINK ---
    elif data.startswith("get_f_"):
        cid = int(data.split("_")[2])
        # PRE-LINK CHECK
        if await is_already_in_channel(context, cid, uid): 
            await q.answer("⚠️ Already Joined!", show_alert=True) 
            return
        
        try:
            l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Free-{uid}")
            await context.bot.send_message(uid, f"🔗 **Link:**\n{l.invite_link}\n\nℹ️ *Request auto-approved.*")
            await q.answer("Sent to DM")
        except: await q.answer("Bot Error", show_alert=True)

    # --- VIEW PAID BATCH OPTIONS ---
    elif data.startswith("view_p_"):
        cid = int(data.split("_")[2])
        kb = [[InlineKeyboardButton("🕒 3hr Demo", callback_data=f"req_d_{cid}")],
              [InlineKeyboardButton("♾️ Permanent", callback_data=f"req_p_{cid}")],
              [InlineKeyboardButton("🔙 Back", callback_data="u_paid")]]
        await q.edit_message_text("💎 **Choose Access:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    # --- REQUEST DEMO LINK ---
    elif data.startswith("req_d_"):
        cid = int(data.split("_")[2])
        # PRE-LINK CHECK (Restored)
        if await is_already_in_channel(context, cid, uid):
            await q.answer("⚠️ You are already in this channel!", show_alert=True)
            return
            
        DB["PENDING_REQUESTS"][uid] = cid
        await save_data_async()
        try:
            l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Demo-{uid}")
            await context.bot.send_message(uid, f"⏱ **Demo Link:**\n{l.invite_link}")
            await q.answer("Sent to DM")
        except: await q.answer("Bot Error: Check Admin Rights", show_alert=True)

    # --- REQUEST PERMANENT LINK ---
    elif data.startswith("req_p_"):
        cid = int(data.split("_")[2])
        # PRE-LINK CHECK (Restored)
        if await is_already_in_channel(context, cid, uid):
            await q.answer("⚠️ You are already in this channel!", show_alert=True)
            return

        try:
            l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Perm-{uid}")
            await context.bot.send_message(uid, f"💎 **Link:**\n{l.invite_link}")
            await q.answer("Sent to DM")
        except: await q.answer("Bot Error: Check Admin Rights", show_alert=True)

async def show_user_menu(update: Update):
    kb = [[InlineKeyboardButton("📂 Free Batches", callback_data="u_free"), InlineKeyboardButton("💎 Paid Batches", callback_data="u_paid")],
          [InlineKeyboardButton("🆘 Support", url=f"tg://user?id={SUPPORT_GROUP_ID}")]]
    txt = "👋 **Welcome!**\nChoose an option:"
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# --- 14. MAIN ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in DB["BLOCKED_USERS"]: return
    if user.id not in DB["USER_DATA"]:
        DB["USER_DATA"][user.id] = {"name": user.full_name, "username": user.username, "joined_at": time.time(), "demos": {}}
        await save_data_async()
    await get_or_create_topic(user, context)
    
    # 1. OWNER VIEW
    if user.id == OWNER_ID:
        await update.message.reply_text(
            f"👑 **WELCOME BOSS!**\n"
            f"You have full system access.\n\n"
            f"**⚙️ Owner Commands:**\n"
            f"`/addadmin [id]` - Add new admin\n"
            f"`/deladmin [id]` - Remove admin\n"
            f"`/backup` - Download data backup\n"
            f"`/allusers` - Get list of all users\n\n"
            f"**🛠 Admin Tools:**\n"
            f"`/stats` - View Bot Statistics\n"
            f"`/addbatch` - Create New Batch\n"
            f"`/delbatch` - Delete Batch\n"
            f"`/broadcast` - Send Msg to All Users\n"
            f"`/post` - Post to All Channels\n"
            f"`/user [id]` - Check User Info\n"
            f"`/batches` - List Connected Batches (New)\n"
            f"`/cancel` - Stop Current Action",
            parse_mode=ParseMode.MARKDOWN
        )
    # 2. ADMIN VIEW
    elif is_admin(user.id):
        await update.message.reply_text(
            f"👮‍♂️ **WELCOME ADMIN!**\n"
            f"Here are your management tools.\n\n"
            f"**🛠 Available Commands:**\n"
            f"`/stats` - View Bot Statistics\n"
            f"`/addbatch` - Create New Batch\n"
            f"`/delbatch` - Delete Batch\n"
            f"`/broadcast` - Send Msg to All Users\n"
            f"`/post` - Post to All Channels\n"
            f"`/user [id]` - Check User Info\n"
            f"`/batches` - List Connected Batches (New)\n"
            f"`/cancel` - Stop Current Action\n\n"
            f"⚠️ *Note: You cannot add/remove other admins.*",
            parse_mode=ParseMode.MARKDOWN
        )
    # 3. USER VIEW
    elif await check_membership(user.id, context):
        await show_user_menu(update)
    else:
        kb = [[InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
              [InlineKeyboardButton("✅ Verified", callback_data="verify")]]
        await update.message.reply_text("⚠️ **Join Main Channel First**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

def main():
    load_data()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, cmd_id))
    
    app.add_handler(CommandHandler("addadmin", cmd_add_admin))
    app.add_handler(CommandHandler("deladmin", cmd_del_admin))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("allusers", cmd_all_users))
    
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("user", cmd_user_details))
    app.add_handler(CommandHandler("batches", cmd_batches)) # NEW
    app.add_handler(CommandHandler("addbatch", cmd_addbatch_start))
    app.add_handler(CommandHandler("delbatch", cmd_delbatch))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast_start))
    app.add_handler(CommandHandler("post", cmd_post_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    
    app.add_handler(CallbackQueryHandler(general_callback))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(ChatMemberHandler(on_join_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER)) # NEW: Auto-track bot
    app.add_handler(MessageReactionHandler(handle_reaction))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    
    if app.job_queue: app.job_queue.run_repeating(check_demos, interval=60, first=10)
    
    print("Bot v9.7 All-Scan Edition Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
