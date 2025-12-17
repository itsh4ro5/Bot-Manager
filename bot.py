# -*- coding: utf-8 -*-

"""
ULTIMATE BOT MANAGER (v9.0 - The Mammoth Edition)
Features Included:
1.  Owner Commands: /addadmin, /deladmin, /backup, /allusers
2.  Admin Commands: /stats, /user, /addbatch, /delbatch, /broadcast, /post, /cancel
3.  User Features: Professional Welcome, Free/Paid Batches, Ticket Support
4.  Automation:
    - 3-Hour Demo Timer (Restart Proof)
    - Auto-Kick on Expiry
    - Smart Topic Creation (No Duplicates)
    - History Search Link in Tickets
    - Admin Command Auto-Deletion (20 mins)
    - Bidirectional Message Edit & Reaction Sync
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
        def index(): return "Bot Running - v9.0 Mammoth Edition", 200
        
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

MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK", "https://t.me/YourChannel")
DATA_FILE = os.environ.get("DATA_FILE", "bot_data.json")

# --- 4. DATABASE & MEMORY ---
DB = {
    "ADMIN_IDS": [],
    "FREE_CHANNELS": {},
    "PAID_CHANNELS": {},
    "USER_DATA": {},
    "BLOCKED_USERS": [],
    "USER_TOPICS": {}, 
    "PENDING_REQUESTS": {} 
}

# Runtime Memory (Cleared on Restart)
MESSAGE_MAP = {} 
ADMIN_WIZARD = {} 
BROADCAST_STATE = {} 
TOPIC_CREATION_LOCK = set()

data_lock = asyncio.Lock()

# --- 5. PERSISTENCE FUNCTIONS ---
def load_data():
    global DB
    if not os.path.exists(DATA_FILE):
        save_data_sync()
        return

    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            # Safe Merge to prevent errors
            if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = loaded["ADMIN_IDS"]
            if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
            
            # Convert string keys back to integers
            for k in ["FREE_CHANNELS", "PAID_CHANNELS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                if k in loaded:
                    DB[k] = {int(i): v for i, v in loaded[k].items()}

            # Ensure Owner is always Admin
            if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
            logger.info("Database loaded successfully.")
    except Exception as e:
        logger.error(f"Load Error: {e}")

def save_data_sync():
    try:
        to_save = {
            "ADMIN_IDS": DB["ADMIN_IDS"],
            "BLOCKED_USERS": DB["BLOCKED_USERS"],
            "FREE_CHANNELS": {str(k): v for k, v in DB["FREE_CHANNELS"].items()},
            "PAID_CHANNELS": {str(k): v for k, v in DB["PAID_CHANNELS"].items()},
            "USER_DATA": {str(k): v for k, v in DB["USER_DATA"].items()},
            "USER_TOPICS": {str(k): v for k, v in DB["USER_TOPICS"].items()},
            "PENDING_REQUESTS": {str(k): v for k, v in DB["PENDING_REQUESTS"].items()}
        }
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
    """Checks if user has joined the mandatory channel."""
    if is_admin(user_id) or not MANDATORY_CHANNEL_ID: return True
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        return m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except: return True # Fail open if bot is not admin in channel

async def delete_later(context: ContextTypes.DEFAULT_TYPE):
    """Job to auto-delete messages."""
    job = context.job
    try: await context.bot.delete_message(chat_id=job.data['chat_id'], message_id=job.data['msg_id'])
    except: pass

async def schedule_delete(context, message):
    """Schedules deletion in 20 minutes (1200 seconds)."""
    if message:
        context.job_queue.run_once(delete_later, 1200, data={'chat_id': message.chat.id, 'msg_id': message.message_id})

async def get_or_create_topic(user, context):
    """
    Creates a forum topic for the user.
    Includes DUPLICATE CHECK and HISTORY LINK.
    """
    if not SUPPORT_GROUP_ID: return None
    
    # Check DB first
    if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]

    # Lock to prevent race conditions
    if user.id in TOPIC_CREATION_LOCK:
        await asyncio.sleep(1) 
        if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]
    
    TOPIC_CREATION_LOCK.add(user.id)
    try:
        name = f"{user.first_name[:20]} ({user.id})"
        topic = await context.bot.create_forum_topic(SUPPORT_GROUP_ID, name)
        
        DB["USER_TOPICS"][user.id] = topic.message_thread_id
        await save_data_async()
        
        # Generate History Link
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        search_url = f"https://t.me/c/{group_id_str}?q={user.id}"
        lang = user.language_code or "N/A"
        
        # Professional User Detail Header
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

# --- 7. COMMAND HANDLERS ---

# /id command
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg_obj = update.effective_message
    
    if chat.type == ChatType.PRIVATE:
        text = f"👤 **Your User ID:** `{user.id}`"
    else:
        text = f"🆔 **Chat ID:** `{chat.id}`"
        
    sent_msg = await msg_obj.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    
    # Auto-clean if admin
    if user and is_admin(user.id):
        await schedule_delete(context, msg_obj)
        await schedule_delete(context, sent_msg)

# /addadmin (Owner Only)
async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_admin = int(context.args[0])
        if new_admin not in DB["ADMIN_IDS"]:
            DB["ADMIN_IDS"].append(new_admin)
            await save_data_async()
            msg = await update.message.reply_text(f"✅ User {new_admin} is now Admin.")
        else:
            msg = await update.message.reply_text("⚠️ Already Admin.")
    except: msg = await update.message.reply_text("Usage: /addadmin [user_id]")
    
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

# /deladmin (Owner Only)
async def cmd_del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target = int(context.args[0])
        if target in DB["ADMIN_IDS"] and target != OWNER_ID:
            DB["ADMIN_IDS"].remove(target)
            await save_data_async()
            msg = await update.message.reply_text(f"🗑 User {target} removed from Admin.")
        else:
            msg = await update.message.reply_text("⚠️ Cannot remove.")
    except: msg = await update.message.reply_text("Usage: /deladmin [user_id]")
    
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

# /backup (Owner Only)
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if os.path.exists(DATA_FILE):
        await update.message.reply_document(document=open(DATA_FILE, "rb"), caption="DB Backup")
    else:
        msg = await update.message.reply_text("No DB file found.")
        await schedule_delete(context, msg)
    await schedule_delete(context, update.message)

# /allusers (Owner Only - TXT Dump)
async def cmd_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    msg = await update.message.reply_text("⏳ Generating report...")
    
    report = f"ALL USERS DUMP - {datetime.now()}\n"
    report += "-" * 40 + "\n"
    report += "ID | Name | Username\n"
    
    for uid, data in DB["USER_DATA"].items():
        report += f"{uid} | {data.get('name')} | @{data.get('username')}\n"
    
    f = io.BytesIO(report.encode("utf-8"))
    f.name = "all_users.txt"
    
    await update.message.reply_document(document=f, caption="✅ All Users List")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

# /user [id] (Admin - Details Report)
async def cmd_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target_id = int(context.args[0])
    except: 
        msg = await update.message.reply_text("Usage: /user [id]")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return

    info = DB["USER_DATA"].get(target_id)
    if not info:
        msg = await update.message.reply_text("❌ User not found in DB.")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return

    msg = await update.message.reply_text("🔍 Scanning batches...")
    
    report = f"USER DETAILS: {target_id}\n"
    report += f"Name: {info.get('name')}\n"
    report += f"Joined: {time.ctime(info.get('joined_at',0))}\n\n"
    report += "--- BATCH STATUS ---\n"
    
    for cid, name in DB["FREE_CHANNELS"].items():
        try: 
            m = await context.bot.get_chat_member(cid, target_id)
            report += f"[Free] {name}: {m.status}\n"
        except: report += f"[Free] {name}: Error/Not Found\n"
        
    for cid, name in DB["PAID_CHANNELS"].items():
        try: 
            m = await context.bot.get_chat_member(cid, target_id)
            report += f"[Paid] {name}: {m.status}\n"
        except: report += f"[Paid] {name}: Error/Not Found\n"
    
    f = io.BytesIO(report.encode("utf-8"))
    f.name = f"user_{target_id}.txt"
    await update.message.reply_document(document=f, caption=f"Report for {target_id}")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

# /stats (Admin)
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    t = (
        f"📊 **Statistics**\n"
        f"👥 Users: {len(DB['USER_DATA'])}\n"
        f"🆓 Free Batches: {len(DB['FREE_CHANNELS'])}\n"
        f"💎 Paid Batches: {len(DB['PAID_CHANNELS'])}\n"
        f"🚫 Blocked: {len(DB['BLOCKED_USERS'])}"
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

# /cancel (Admin - Stops Wizard/Broadcast)
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in BROADCAST_STATE: del BROADCAST_STATE[uid]
    if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
    msg = await update.message.reply_text("❌ Operation Cancelled")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

# --- 8. WIZARD SYSTEM (/addbatch) ---

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
    if uid not in ADMIN_WIZARD: 
        await q.answer("Wizard Expired")
        return
    data = q.data
    
    if data in ["wiz_free", "wiz_paid"]:
        ADMIN_WIZARD[uid]["type"] = "free" if data == "wiz_free" else "paid"
        ADMIN_WIZARD[uid]["step"] = "ask_name"
        await q.edit_message_text(f"Selected: **{data.split('_')[1].upper()}**\n\n➡️ Please send the **Batch Name** now:", parse_mode=ParseMode.MARKDOWN)

async def wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    txt = update.message.text
    if uid not in ADMIN_WIZARD: return False
    
    state = ADMIN_WIZARD[uid]
    
    if state["step"] == "ask_name":
        state["name"] = txt
        state["step"] = "ask_id"
        msg = await update.message.reply_text(f"Name: {txt}\n\n➡️ Now send the **Channel ID** (starts with -100):")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return True
        
    elif state["step"] == "ask_id":
        try:
            cid = int(txt)
            target = DB["FREE_CHANNELS"] if state["type"] == "free" else DB["PAID_CHANNELS"]
            target[cid] = state["name"]
            await save_data_async()
            msg = await update.message.reply_text(f"✅ **Batch Added Successfully!**\nName: {state['name']}\nID: `{cid}`", parse_mode=ParseMode.MARKDOWN)
            del ADMIN_WIZARD[uid]
        except:
            msg = await update.message.reply_text("❌ Invalid ID format. Try again.")
        
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return True
        
    return False

# --- 9. BROADCAST SYSTEM ---

async def cmd_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    BROADCAST_STATE[update.effective_user.id] = {"type": "broadcast", "step": "wait_msg"}
    msg = await update.message.reply_text("📢 **Broadcast Mode**\nSend the message (Text/Photo/Video) you want to send to ALL users.\nType /cancel to stop.")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    BROADCAST_STATE[update.effective_user.id] = {"type": "post", "step": "wait_msg"}
    msg = await update.message.reply_text("📝 **Post Mode**\nSend the message you want to post to ALL Batches.\nType /cancel to stop.")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def handle_broadcast_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
        await q.edit_message_text("⏳ Processing... Do not use bot until done.")
        msg_obj = state["content"]
        count = 0
        blocked = 0
        
        if state["type"] == "broadcast":
            # Send to Users
            for target_id in list(DB["USER_DATA"].keys()):
                try:
                    await context.bot.copy_message(target_id, uid, msg_obj.message_id)
                    count += 1
                    await asyncio.sleep(0.05) # Flood protection
                except Forbidden: 
                    blocked += 1
                    if target_id not in DB["BLOCKED_USERS"]: DB["BLOCKED_USERS"].append(target_id)
                except: pass
            await context.bot.send_message(uid, f"✅ **Broadcast Done**\nSent: {count}\nBlocked: {blocked}")

        elif state["type"] == "post":
            # Send to Channels
            targets = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
            for cid in targets:
                try:
                    await context.bot.copy_message(cid, uid, msg_obj.message_id)
                    count += 1
                    await asyncio.sleep(0.5)
                except: pass
            await context.bot.send_message(uid, f"✅ **Posting Done**\nPosted in {count} channels.")
            
        del BROADCAST_STATE[uid]

# --- 10. SYNC (EDIT & REACTION) ---

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Syncs reactions bidirectionally."""
    if not update.message_reaction: return
    r = update.message_reaction
    key = (r.chat.id, r.message_id)
    
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try: 
            await context.bot.set_message_reaction(tc, tm, reaction=r.new_reaction)
        except: pass

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Syncs message edits."""
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

# --- 11. MAIN MESSAGING LOGIC ---

async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    # Check Active States first
    if await wizard_message(update, context): return
    if await handle_broadcast_flow(update, context): return

    # User -> Admin (Private Chat)
    if chat.type == ChatType.PRIVATE:
        if user.id in DB["BLOCKED_USERS"]: return
        
        topic_id = await get_or_create_topic(user, context)
        if topic_id:
            try:
                # Use copy_message to allow Edit Sync
                sent = await context.bot.copy_message(SUPPORT_GROUP_ID, chat.id, update.message.id, message_thread_id=topic_id)
                MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
            except Exception as e:
                # If topic deleted, remove from DB and retry ONCE
                if "thread not found" in str(e).lower() or "bad request" in str(e).lower():
                    if user.id in DB["USER_TOPICS"]: del DB["USER_TOPICS"][user.id]
                    topic_id = await get_or_create_topic(user, context)
                    if topic_id:
                        try:
                            sent = await context.bot.copy_message(SUPPORT_GROUP_ID, chat.id, update.message.id, message_thread_id=topic_id)
                            MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                            MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
                        except: pass

    # Admin -> User (Topic)
    elif chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        if update.message.from_user.id == context.bot.id: return # Ignore bot's own msgs
        
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

# --- 12. JOIN LOGIC & DEMO TIMER ---

async def on_join_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detects when Admin approves a join request (Invite -> Member)."""
    cm = update.chat_member
    if not cm: return
    user = cm.from_user
    chat = cm.chat
    
    # Status changed to MEMBER (Approved)
    if cm.new_chat_member.status == ChatMember.MEMBER and cm.old_chat_member.status != ChatMember.MEMBER:
        if user.id in DB["PENDING_REQUESTS"] and DB["PENDING_REQUESTS"][user.id] == chat.id:
            try: 
                await context.bot.send_message(user.id, "✅ **Request Approved!**\n\nYour 3-Hour Demo Access starts NOW.")
            except: pass
            
            # Start Timer (Persisted)
            expiry = time.time() + (3 * 3600)
            if "demos" not in DB["USER_DATA"][user.id]: DB["USER_DATA"][user.id]["demos"] = {}
            DB["USER_DATA"][user.id]["demos"][str(chat.id)] = expiry
            
            # Clear Request
            del DB["PENDING_REQUESTS"][user.id]
            await save_data_async()

async def check_demos(context: ContextTypes.DEFAULT_TYPE):
    """Background Job to kick expired users."""
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
                    await context.bot.send_message(uid, "⏰ **Demo Access Expired.**\nPlease contact admin for permanent access.")
                except: pass
                del data["demos"][bid]
                mod = True
    if mod: await save_data_async()

# --- 13. USER CALLBACKS ---

async def general_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data

    # Wizard & Broadcast
    if data.startswith("wiz_"): await wizard_callback(update, context); return
    if data.startswith("bc_"): await broadcast_callback(update, context); return

    # User Logic
    if data == "verify":
        if await check_membership(uid, context):
            await q.answer("✅ Verified!")
            await show_user_menu(update)
        else: await q.answer("❌ You must join the channel first!", show_alert=True)
    
    elif data == "u_main":
        await show_user_menu(update)

    elif data == "u_free":
        if not DB["FREE_CHANNELS"]:
            await q.answer("No free batches available", show_alert=True)
            return
        kb = [[InlineKeyboardButton(f"🔗 {n}", callback_data=f"get_f_{i}")] for i, n in DB["FREE_CHANNELS"].items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        await q.edit_message_text("📂 **Available Free Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "u_paid":
        if not DB["PAID_CHANNELS"]:
            await q.answer("No paid batches available", show_alert=True)
            return
        kb = [[InlineKeyboardButton(f"💎 {n}", callback_data=f"view_p_{i}")] for i, n in DB["PAID_CHANNELS"].items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        await q.edit_message_text("💎 **Premium Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    
    elif data.startswith("get_f_"):
        try:
            cid = int(data.split("_")[2])
            l = await context.bot.create_chat_invite_link(cid, member_limit=1, name=f"U-{uid}")
            await context.bot.send_message(uid, f"🔗 **Your Free Link:**\n{l.invite_link}\n(One-time use only)")
            await q.answer("Link sent to DM!")
        except Exception as e: 
            logger.error(f"Link Gen Error: {e}")
            await q.answer("Error: Bot needs admin rights in that channel!", show_alert=True)
    
    elif data.startswith("view_p_"):
        cid = int(data.split("_")[2])
        kb = [
            [InlineKeyboardButton("🕒 Request 3hr Demo", callback_data=f"req_d_{cid}")],
            [InlineKeyboardButton("♾️ Permanent Access", callback_data=f"req_p_{cid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="u_paid")]
        ]
        await q.edit_message_text("💎 **Choose Access Type:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    
    elif data.startswith("req_d_"):
        cid = int(data.split("_")[2])
        # Save intent
        DB["PENDING_REQUESTS"][uid] = cid
        await save_data_async()
        try:
            l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Demo-{uid}")
            await context.bot.send_message(uid, f"⏱ **Demo Link Generated:**\n{l.invite_link}\n\n1. Join via link.\n2. Wait for Admin Approval.\n3. Once approved, you get 3 Hours.")
            await q.answer("Check your DM!")
        except: await q.answer("Bot needs admin rights!", show_alert=True)
    
    elif data.startswith("req_p_"):
        cid = int(data.split("_")[2])
        try:
            l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Perm-{uid}")
            await context.bot.send_message(uid, f"💎 **Permanent Link:**\n{l.invite_link}")
            await q.answer("Check your DM!")
        except: await q.answer("Error generating link", show_alert=True)

async def show_user_menu(update: Update):
    kb = [
        [InlineKeyboardButton("📂 Free Batches", callback_data="u_free"), InlineKeyboardButton("💎 Paid Batches", callback_data="u_paid")],
        [InlineKeyboardButton("🆘 Contact Support", url=f"tg://user?id={SUPPORT_GROUP_ID}" if SUPPORT_GROUP_ID else "https://t.me/Admin")]
    ]
    txt = (
        "👋 **Welcome to the Ultimate Bot Manager!**\n\n"
        "🚀 **Features:**\n"
        "• Instant Access to Free Content\n"
        "• Exclusive Premium Batches\n"
        "• 3-Hour Demo Trials\n"
        "• Direct Support Line\n\n"
        "👇 *Tap a button below to get started:*"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# --- 14. STARTUP ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in DB["BLOCKED_USERS"]: return
    
    # Init User
    if user.id not in DB["USER_DATA"]:
        DB["USER_DATA"][user.id] = {"name": user.full_name, "username": user.username, "joined_at": time.time(), "demos": {}}
        await save_data_async()
    
    # Ensure Topic Exists
    await get_or_create_topic(user, context)
    
    if await check_membership(user.id, context):
        await show_user_menu(update)
    else: 
        kb = [[InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
              [InlineKeyboardButton("✅ I Have Joined", callback_data="verify")]]
        await update.message.reply_text("⚠️ **Verification Required**\n\nYou must join our update channel to use this bot.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

def main():
    load_data()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # --- HANDLER REGISTRATION ---
    
    # 1. Core Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", cmd_id))
    
    # 2. Owner Commands
    app.add_handler(CommandHandler("addadmin", cmd_add_admin))
    app.add_handler(CommandHandler("deladmin", cmd_del_admin))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("allusers", cmd_all_users))
    
    # 3. Admin Commands
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("user", cmd_user_details))
    app.add_handler(CommandHandler("addbatch", cmd_addbatch_start))
    app.add_handler(CommandHandler("delbatch", cmd_delbatch))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast_start))
    app.add_handler(CommandHandler("post", cmd_post_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    
    # 4. Callbacks & Events
    app.add_handler(CallbackQueryHandler(general_callback))
    app.add_handler(ChatMemberHandler(on_join_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageReactionHandler(handle_reaction))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
    
    # 5. Main Message Loop (Must be last)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    
    # 6. Job Queue
    if app.job_queue: 
        app.job_queue.run_repeating(check_demos, interval=60, first=10)
    
    print("Bot v9.0 Mammoth Edition Started...")
    app.run_polling()

if __name__ == "__main__":
    main()
