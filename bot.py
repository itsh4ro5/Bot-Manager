# -*- coding: utf-8 -*-

"""
ADVANCED TELEGRAM BOT MANAGER (Final v3.0)
- Full Command Based Admin Panel (No Buttons for Admin)
- TXT Report Generation
- Message Editing & Reaction Sync
- Broadcast Confirmation
- Restart Proof Demo Timers
- Smart History Search Link (For Old Topics)
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

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- FLASK KEEPALIVE (For Render/Heroku) ---
try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "8080"))
        app = Flask(__name__)
        @app.route('/')
        def index(): return "Bot Running Securely (Final Version)", 200
        
        def run():
            app.run(host="0.0.0.0", port=port, use_reloader=False)
        
        t = threading.Thread(target=run, daemon=True)
        t.start()
except ImportError:
    def _start_keepalive(): pass
_start_keepalive()

# --- CONFIGURATION ---
# Replace these default values locally or set ENV Variables
DEFAULTS = {
    "TOKEN": "", 
    "OWNER": 0,
    "SUPPORT": 0, # Support Group ID
    "MAIN_CH": 0, # Mandatory Channel ID
    "LOG_CH": 0
}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULTS["TOKEN"])
OWNER_ID = int(os.environ.get("OWNER_ID", DEFAULTS["OWNER"]))
SUPPORT_GROUP_ID = int(os.environ.get("SUPPORT_GROUP_ID", DEFAULTS["SUPPORT"]))
MANDATORY_CHANNEL_ID = int(os.environ.get("MANDATORY_CHANNEL_ID", DEFAULTS["MAIN_CH"]))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", DEFAULTS["LOG_CH"]))

MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK", "https://t.me/YourChannel")
DATA_FILE = os.environ.get("DATA_FILE", "bot_data.json")

# --- DATA STORE ---
DB = {
    "ADMIN_IDS": [],
    "FREE_CHANNELS": {},
    "PAID_CHANNELS": {},
    "USER_DATA": {},
    "BLOCKED_USERS": [],
    "USER_TOPICS": {}
}

# Runtime Memory (Temporary)
MESSAGE_MAP = {} # {(chat_id, msg_id): (target_chat_id, target_msg_id)}
PENDING_DEMO_REQUESTS = {} 
BROADCAST_STATE = {} # {admin_id: {type: 'broadcast/post', content: message_object}}

data_lock = asyncio.Lock()

# --- PERSISTENCE (Saving Data) ---
def load_data():
    global DB
    if not os.path.exists(DATA_FILE):
        save_data_sync()
        return

    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            # Restore structure and convert keys back to int
            if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = loaded["ADMIN_IDS"]
            if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
            
            for k in ["FREE_CHANNELS", "PAID_CHANNELS", "USER_TOPICS", "USER_DATA"]:
                if k in loaded:
                    DB[k] = {int(i): v for i, v in loaded[k].items()}

            if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
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
            "USER_TOPICS": {str(k): v for k, v in DB["USER_TOPICS"].items()}
        }
        with open(DATA_FILE, "w") as f:
            json.dump(to_save, f, indent=4)
    except Exception as e:
        logger.error(f"Save Error: {e}")

async def save_data_async():
    async with data_lock:
        await asyncio.to_thread(save_data_sync)

# --- HELPERS ---
def is_admin(uid):
    return uid == OWNER_ID or uid in DB["ADMIN_IDS"]

async def check_membership(user_id, context):
    if is_admin(user_id) or not MANDATORY_CHANNEL_ID: return True
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        return m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except: return True # Fail safe

async def get_or_create_topic(user, context):
    if not SUPPORT_GROUP_ID: return None
    if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]

    try:
        # 1. Create Topic with Name + ID
        name = f"{user.first_name[:20]} ({user.id})"
        topic = await context.bot.create_forum_topic(SUPPORT_GROUP_ID, name)
        DB["USER_TOPICS"][user.id] = topic.message_thread_id
        await save_data_async()
        
        # 2. Smart Search Link Generator
        # (Links to: https://t.me/c/<id>/search?q=<user_id>)
        # Note: We strip '-100' from ID for t.me link
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        search_url = f"https://t.me/c/{group_id_str}?q={user.id}"
        
        lang = user.language_code or "Unknown"
        
        # 3. Detailed Info Message (Pinned/First Msg)
        text = (
            f"👤 **NEW USER TICKET**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📛 **Name:** {user.full_name}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"🔗 **Username:** @{user.username if user.username else 'None'}\n"
            f"🌐 **Language:** {lang}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Agar yeh purana user hai, to 'Search History' dabakar purane messages check karein.*"
        )
        
        kb = [[InlineKeyboardButton("🔎 Search Old History", url=search_url)]]

        await context.bot.send_message(
            SUPPORT_GROUP_ID, 
            text,
            message_thread_id=topic.message_thread_id,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
        return topic.message_thread_id
    except Exception as e:
        logger.error(f"Topic Error: {e}")
        return None

# --- ADMIN COMMANDS ---

async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_admin = int(context.args[0])
        if new_admin not in DB["ADMIN_IDS"]:
            DB["ADMIN_IDS"].append(new_admin)
            await save_data_async()
            await update.message.reply_text(f"✅ User {new_admin} is now Admin.")
        else:
            await update.message.reply_text("⚠️ Already Admin.")
    except: await update.message.reply_text("Usage: /addadmin [user_id]")

async def cmd_del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target = int(context.args[0])
        if target in DB["ADMIN_IDS"] and target != OWNER_ID:
            DB["ADMIN_IDS"].remove(target)
            await save_data_async()
            await update.message.reply_text(f"🗑 User {target} removed from Admin.")
        else:
            await update.message.reply_text("⚠️ Cannot remove.")
    except: await update.message.reply_text("Usage: /deladmin [user_id]")

async def cmd_add_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    # Format: /addbatch [free/paid] [Name] [ID]
    try:
        btype = context.args[0].lower()
        channel_id = int(context.args[-1])
        name = " ".join(context.args[1:-1])
        
        if btype == "free":
            DB["FREE_CHANNELS"][channel_id] = name
        elif btype == "paid":
            DB["PAID_CHANNELS"][channel_id] = name
        else:
            await update.message.reply_text("❌ Type must be 'free' or 'paid'")
            return

        await save_data_async()
        await update.message.reply_text(f"✅ **{name}** added to {btype} list.")
    except:
        await update.message.reply_text("Usage: /addbatch [free/paid] [Name] [Channel_ID]")

async def cmd_del_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    # Format: /delbatch [free/paid] [ID]
    try:
        btype = context.args[0].lower()
        cid = int(context.args[1])
        
        found = False
        if btype == "free" and cid in DB["FREE_CHANNELS"]:
            del DB["FREE_CHANNELS"][cid]
            found = True
        elif btype == "paid" and cid in DB["PAID_CHANNELS"]:
            del DB["PAID_CHANNELS"][cid]
            found = True
            
        if found:
            await save_data_async()
            await update.message.reply_text("✅ Batch removed.")
        else:
            await update.message.reply_text("❌ Batch not found.")
    except:
        await update.message.reply_text("Usage: /delbatch [free/paid] [Channel_ID]")

async def cmd_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        target_id = int(context.args[0])
    except:
        await update.message.reply_text("Usage: /user [chat_id]")
        return

    # Check if user exists
    user_info = DB["USER_DATA"].get(target_id)
    if not user_info:
        await update.message.reply_text("❌ User never started the bot.")
        return

    msg = await update.message.reply_text("🔍 Scanning batches... please wait.")
    
    # Generate Report
    report = f"USER REPORT FOR: {target_id}\n"
    report += f"Name: {user_info.get('name', 'Unknown')}\n"
    report += f"Username: {user_info.get('username', 'None')}\n"
    report += f"Joined Bot: {time.ctime(user_info.get('joined_at', 0))}\n"
    report += "-" * 30 + "\nBATCH STATUS:\n"

    # Scan Free Batches
    report += "\n[FREE BATCHES]\n"
    for cid, cname in DB["FREE_CHANNELS"].items():
        try:
            m = await context.bot.get_chat_member(cid, target_id)
            status = m.status
        except: status = "Unknown/Error"
        report += f"{cname} ({cid}): {status}\n"

    # Scan Paid Batches
    report += "\n[PAID BATCHES]\n"
    for cid, cname in DB["PAID_CHANNELS"].items():
        try:
            m = await context.bot.get_chat_member(cid, target_id)
            status = m.status
        except: status = "Unknown/Error"
        report += f"{cname} ({cid}): {status}\n"

    # Create File
    f = io.BytesIO(report.encode("utf-8"))
    f.name = f"user_{target_id}_report.txt"
    
    await update.message.reply_document(document=f, caption=f"✅ Report for {target_id}")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)

async def cmd_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    msg = await update.message.reply_text("⏳ Generating full database report...")
    
    report = "ALL USERS DUMP\n"
    report += f"Generated: {datetime.now()}\n"
    report += "ID | Name | Username\n"
    report += "-" * 50 + "\n"
    
    for uid, data in DB["USER_DATA"].items():
        line = f"{uid} | {data.get('name', 'N/A')} | @{data.get('username', 'None')}\n"
        report += line
        
    f = io.BytesIO(report.encode("utf-8"))
    f.name = "all_users.txt"
    
    await update.message.reply_document(document=f, caption="✅ All Users List")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    txt = (
        f"📊 **Stats**\n"
        f"Users: {len(DB['USER_DATA'])}\n"
        f"Free Batches: {len(DB['FREE_CHANNELS'])}\n"
        f"Paid Batches: {len(DB['PAID_CHANNELS'])}\n"
        f"Blocked: {len(DB['BLOCKED_USERS'])}"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if os.path.exists(DATA_FILE):
        await update.message.reply_document(document=open(DATA_FILE, "rb"), caption="DB Backup")
    else:
        await update.message.reply_text("No DB file found.")

# --- BROADCAST SYSTEM ---
async def cmd_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    BROADCAST_STATE[update.effective_user.id] = {"type": "broadcast", "step": "wait_msg"}
    await update.message.reply_text("📢 **Broadcast Mode**\nSend the message (Text/Photo/Video) you want to broadcast.\nType /cancel to stop.")

async def cmd_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    BROADCAST_STATE[update.effective_user.id] = {"type": "post", "step": "wait_msg"}
    await update.message.reply_text("📝 **Post Mode**\nSend the message to post in all Batches.\nType /cancel to stop.")

async def handle_broadcast_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in BROADCAST_STATE: return False
    
    state = BROADCAST_STATE[user.id]
    
    # Step 1: User sent the content
    if state["step"] == "wait_msg":
        state["content"] = update.message
        state["step"] = "confirm"
        
        kb = [[InlineKeyboardButton("✅ YES, Send", callback_data="confirm_yes"),
               InlineKeyboardButton("❌ NO, Cancel", callback_data="confirm_no")]]
        
        txt = "📢 **Confirmation**\nDo you want to send this message?"
        if state["type"] == "post": txt = "📝 **Confirmation**\nPost this to all batches?"
        
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return True
    
    return False

async def handle_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid not in BROADCAST_STATE: 
        await q.answer("Expired session.")
        return

    data = q.data
    state = BROADCAST_STATE[uid]
    msg_to_send = state.get("content")
    
    if data == "confirm_no":
        del BROADCAST_STATE[uid]
        await q.edit_message_text("❌ Action Cancelled.")
        return

    if data == "confirm_yes":
        await q.edit_message_text("⏳ Processing... (Do not use bot until done)")
        
        count = 0
        blocked = 0
        
        if state["type"] == "broadcast":
            # Send to Users
            users = list(DB["USER_DATA"].keys())
            for target_id in users:
                try:
                    await context.bot.copy_message(target_id, uid, msg_to_send.message_id)
                    count += 1
                    await asyncio.sleep(0.05) # Safe Delay
                except Forbidden:
                    blocked += 1
                    if target_id not in DB["BLOCKED_USERS"]: DB["BLOCKED_USERS"].append(target_id)
                except Exception: pass
            
            await context.bot.send_message(uid, f"✅ **Broadcast Done**\nSent: {count}\nBlocked: {blocked}")

        elif state["type"] == "post":
            # Send to Batches
            channels = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
            for cid in channels:
                try:
                    await context.bot.copy_message(cid, uid, msg_to_send.message_id)
                    count += 1
                    await asyncio.sleep(0.5) # Slower for channels
                except Exception as e:
                    logger.error(f"Post Fail {cid}: {e}")
            
            await context.bot.send_message(uid, f"✅ **Posting Done**\nChannels: {count}")

        del BROADCAST_STATE[uid]

# --- SYNC HANDLERS ---

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Syncs edited messages between User and Admin"""
    if not update.edited_message: return
    
    msg = update.edited_message
    chat_id = msg.chat.id
    msg_id = msg.message_id
    
    # Check if we know this message
    if (chat_id, msg_id) in MESSAGE_MAP:
        target_chat, target_msg = MESSAGE_MAP[(chat_id, msg_id)]
        
        new_text = msg.text or msg.caption or "Media Content"
        new_text = f"✏️ [EDITED]\n{new_text}"
        
        try:
            await context.bot.edit_message_text(new_text, chat_id=target_chat, message_id=target_msg)
        except Exception as e:
            # Maybe it's a caption, try edit_message_caption
            try:
                await context.bot.edit_message_caption(chat_id=target_chat, message_id=target_msg, caption=new_text)
            except: pass

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Syncs reactions"""
    if not update.message_reaction: return
    r = update.message_reaction
    
    # Key = Where the reaction happened
    key = (r.chat.id, r.message_id)
    
    if key in MESSAGE_MAP:
        target_chat, target_msg = MESSAGE_MAP[key]
        try:
            await context.bot.set_message_reaction(
                chat_id=target_chat,
                message_id=target_msg,
                reaction=r.new_reaction
            )
        except Exception: pass

# --- STANDARD MESSAGES ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in DB["BLOCKED_USERS"]: return

    # Register
    if user.id not in DB["USER_DATA"]:
        DB["USER_DATA"][user.id] = {
            "name": user.full_name,
            "username": user.username,
            "joined_at": time.time(),
            "demos": {}
        }
        await save_data_async()

    # Create Support Topic
    await get_or_create_topic(user, context)

    # If Admin, show commands help
    if is_admin(user.id):
        txt = (
            "👮‍♂️ **Admin Mode Active**\n\n"
            "/stats - View Stats\n"
            "/addbatch - Add Batch\n"
            "/delbatch - Remove Batch\n"
            "/broadcast - Broadcast Message\n"
            "/post - Post to Channels\n"
            "/user [id] - Get User Report\n"
        )
        if user.id == OWNER_ID:
            txt += "\n👑 **Owner:**\n/addadmin | /deladmin | /allusers | /backup"
        
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return

    # User Menu
    if await check_membership(user.id, context):
        await show_user_menu(update)
    else:
        kb = [[InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
              [InlineKeyboardButton("✅ I have Joined", callback_data="verify")]]
        await update.message.reply_text("⚠️ Join channel first:", reply_markup=InlineKeyboardMarkup(kb))

async def show_user_menu(update: Update):
    kb = [
        [InlineKeyboardButton("🆓 Free Batches", callback_data="u_free"),
         InlineKeyboardButton("💎 Paid Batches", callback_data="u_paid")],
        [InlineKeyboardButton("📂 My Batches", callback_data="u_mine")]
    ]
    txt = "👋 **Welcome!** Choose an option:"
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# --- USER CALLBACKS (Still using buttons for Users as it's better UX) ---
async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    # Admin Broadcast Confirmation
    if data.startswith("confirm_"):
        await handle_broadcast_callback(update, context)
        return

    if data == "verify":
        if await check_membership(uid, context):
            await q.answer("Verified!")
            await show_user_menu(update)
        else: await q.answer("❌ Not joined yet!", show_alert=True)
    
    elif data == "u_main": await show_user_menu(update)
    
    elif data == "u_free":
        kb = [[InlineKeyboardButton(f"🔗 {n}", callback_data=f"get_f_{i}")] for i, n in DB["FREE_CHANNELS"].items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        await q.edit_message_text("🆓 **Free Batches**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "u_paid":
        kb = [[InlineKeyboardButton(f"💎 {n}", callback_data=f"view_p_{i}")] for i, n in DB["PAID_CHANNELS"].items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        await q.edit_message_text("💎 **Paid Batches**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("get_f_"):
        cid = int(data.split("_")[2])
        try:
            link = await context.bot.create_chat_invite_link(cid, member_limit=1, name=f"U-{uid}")
            await q.answer("Link Sent!")
            await context.bot.send_message(uid, f"🔗 **Link:**\n{link.invite_link}")
        except: await q.answer("Error: Bot not admin there", show_alert=True)

    elif data.startswith("view_p_"):
        cid = int(data.split("_")[2])
        kb = [
            [InlineKeyboardButton("🕒 3hr Demo", callback_data=f"req_d_{cid}")],
            [InlineKeyboardButton("♾️ Permanent", callback_data=f"req_p_{cid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="u_paid")]
        ]
        await q.edit_message_text("Select Access:", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("req_d_"):
        cid = int(data.split("_")[2])
        PENDING_DEMO_REQUESTS[uid] = cid
        try:
            link = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"D-{uid}")
            await context.bot.send_message(uid, f"⏱ **Demo Link:**\n{link.invite_link}\n(Join -> Wait for approval)")
            await q.answer()
        except: await q.answer("Error", show_alert=True)

    elif data.startswith("req_p_"):
        cid = int(data.split("_")[2])
        try:
            link = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"P-{uid}")
            await context.bot.send_message(uid, f"♾️ **Perm Link:**\n{link.invite_link}")
            await q.answer()
        except: await q.answer("Error", show_alert=True)

# --- MESSAGE HANDLER ---
async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    # 1. Check Broadcast Flow
    if await handle_broadcast_flow(update, context):
        return

    # 2. Support System (User -> Admin)
    if chat.type == ChatType.PRIVATE:
        if user.id in DB["BLOCKED_USERS"]: return
        topic_id = await get_or_create_topic(user, context)
        if topic_id:
            try:
                sent = await context.bot.forward_message(SUPPORT_GROUP_ID, user.id, update.message.id, message_thread_id=topic_id)
                # Save Mapping
                MESSAGE_MAP[(user.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (user.id, update.message.id)
            except Exception as e: logger.error(f"Fwd Error: {e}")

    # 3. Support System (Admin -> User)
    elif chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        topic_id = update.message.message_thread_id
        target_uid = None
        for u, t in DB["USER_TOPICS"].items():
            if t == topic_id: target_uid = int(u); break
        
        if target_uid:
            try:
                sent = await context.bot.copy_message(target_uid, chat.id, update.message.id)
                # Save Mapping
                MESSAGE_MAP[(SUPPORT_GROUP_ID, update.message.id)] = (target_uid, sent.message_id)
                MESSAGE_MAP[(target_uid, sent.message_id)] = (SUPPORT_GROUP_ID, update.message.id)
            except: await update.message.reply_text("❌ User blocked bot.")

# --- JOB QUEUE (DEMO EXPIRE) ---
async def check_demos(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    users_mod = False
    for uid, data in DB["USER_DATA"].items():
        if "demos" not in data: continue
        d = data["demos"].copy()
        for bid, expiry in d.items():
            if now > expiry:
                try: 
                    await context.bot.ban_chat_member(int(bid), uid)
                    await context.bot.unban_chat_member(int(bid), uid)
                    await context.bot.send_message(uid, "⏰ Demo Expired.")
                except: pass
                del data["demos"][bid]
                users_mod = True
    if users_mod: await save_data_async()

async def on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    if not cm: return
    u = cm.from_user
    c = cm.chat
    status = cm.new_chat_member.status
    
    # Demo Logic
    if status == ChatMember.MEMBER:
        if u.id in PENDING_DEMO_REQUESTS and PENDING_DEMO_REQUESTS[u.id] == c.id:
            if "demos" not in DB["USER_DATA"][u.id]: DB["USER_DATA"][u.id]["demos"] = {}
            DB["USER_DATA"][u.id]["demos"][str(c.id)] = time.time() + (3*3600)
            await save_data_async()
            del PENDING_DEMO_REQUESTS[u.id]

# --- MAIN ---
def main():
    load_data()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addadmin", cmd_add_admin))
    app.add_handler(CommandHandler("deladmin", cmd_del_admin))
    app.add_handler(CommandHandler("addbatch", cmd_add_batch))
    app.add_handler(CommandHandler("delbatch", cmd_del_batch))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("user", cmd_user_details))
    app.add_handler(CommandHandler("allusers", cmd_all_users))
    
    # Broadcast Flows
    app.add_handler(CommandHandler("broadcast", cmd_broadcast_start))
    app.add_handler(CommandHandler("post", cmd_post_start))
    app.add_handler(CommandHandler("cancel", lambda u,c: u.message.reply_text("Cancelled."))) # Simple Cancel

    # Sync Handlers
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
    app.add_handler(MessageReactionHandler(handle_reaction))
    
    # General Handlers
    app.add_handler(CallbackQueryHandler(user_callback_handler))
    app.add_handler(ChatJoinRequestHandler(on_join))
    app.add_handler(ChatMemberHandler(on_join, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    
    if app.job_queue: app.job_queue.run_repeating(check_demos, interval=60, first=10)
    
    print("Bot Running (Final v3.0)...")
    app.run_polling()

if __name__ == "__main__":
    main()
