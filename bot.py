# -*- coding: utf-8 -*-

"""
ALL-IN-ONE TELEGRAM BOT
Features: Support System (Topics), Store (Free/Paid), Broadcast, Auto-Kick, Data Backup.
Platform: Heroku, Render, Koyeb, Termux (Universal).
"""

import logging
import json
import os
import threading
from telegram import Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, BotCommandScopeChat
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, ChatMemberHandler, CallbackQueryHandler, MessageHandler, filters, Application

# --- KEEPALIVE WEB SERVER (For Cloud Deployments) ---
try:
    from flask import Flask
    def _start_keepalive():
        # Render/Heroku provide PORT env var
        port = int(os.environ.get("PORT", "0") or "0")
        if port:
            app = Flask(__name__)
            @app.get("/")
            def _index(): return "Bot is Running!", 200
            @app.get("/health")
            def _health(): return "OK", 200
            # Run Flask in a separate thread so it doesn't block the bot
            th = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False), daemon=True)
            th.start()
            print(f"Flask Server started on port {port}")
        else:
            print("No PORT env var found, skipping Flask server.")
except Exception as e:
    print(f"Flask Error: {e}")
    def _start_keepalive(): pass
_start_keepalive()

# ==============================================================================
# 👇👇👇 TERMUX / LOCAL CONFIGURATION (Fallback if Env Vars are missing)
# ==============================================================================
LOCAL_BOT_TOKEN = "7947999475:AAG9_cCpaL0o_5qcrPGnjOp1wtL1r6KqvMQ"       # Token here
LOCAL_OWNER_ID = 8197649993         # Owner ID
LOCAL_ADMIN_IDS = "7728794948"       # Comma separated IDs
LOCAL_SUPPORT_GROUP = -1003629338139    # Support Group ID
# ==============================================================================

# --- CONFIGURATION LOADING logic ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", LOCAL_BOT_TOKEN)
if not TELEGRAM_BOT_TOKEN:
    print("❌ ERROR: Bot Token missing! Check Env Var or LOCAL_BOT_TOKEN.")

OWNER_ID = int(os.environ.get("OWNER_ID", LOCAL_OWNER_ID or 0))

env_admins = os.environ.get("ADMIN_IDS", "")
local_admins = LOCAL_ADMIN_IDS
final_admins = env_admins if env_admins else local_admins
ADMIN_IDS = [int(x.strip()) for x in final_admins.split(',') if x.strip()]

MANDATORY_CHANNEL_ID = int(os.environ.get("MANDATORY_CHANNEL_ID", 0))
MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK")
CONTACT_ADMIN_LINK = os.environ.get("CONTACT_ADMIN_LINK")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))
SUPPORT_GROUP_ID = int(os.environ.get("SUPPORT_GROUP_ID", LOCAL_SUPPORT_GROUP or 0))

DATA_FILE = os.environ.get("DATA_FILE") or "bot_data.json"

if OWNER_ID and OWNER_ID not in ADMIN_IDS: ADMIN_IDS.append(OWNER_ID)

# --- DYNAMIC DATA STORAGE ---
FREE_CHANNELS = {}      
FREE_CHANNEL_LINKS = {} 
PAID_CHANNELS = {}      
PAID_CHANNEL_LINKS = {} 

USER_DATA = {}
BLOCKED_USER_IDS = set()
ACTIVE_CHATS = {}
USER_TOPICS = {} 

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PERSISTENCE ---
def save_data():
    try:
        # Ensure directory exists if path is provided
        if "/" in DATA_FILE: os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        data = {
            "ADMIN_IDS": ADMIN_IDS,
            "FREE_CHANNELS": FREE_CHANNELS,
            "FREE_CHANNEL_LINKS": FREE_CHANNEL_LINKS,
            "PAID_CHANNELS": PAID_CHANNELS,
            "PAID_CHANNEL_LINKS": PAID_CHANNEL_LINKS,
            "BLOCKED_USER_IDS": list(BLOCKED_USER_IDS),
            "ACTIVE_CHATS": ACTIVE_CHATS,
            "USER_TOPICS": USER_TOPICS
        }
        with open(DATA_FILE, "w") as f: json.dump(data, f, indent=4)
    except Exception as e: logger.error(f"Save Error: {e}")

def load_data():
    global ADMIN_IDS, FREE_CHANNELS, FREE_CHANNEL_LINKS, PAID_CHANNELS, PAID_CHANNEL_LINKS, BLOCKED_USER_IDS, ACTIVE_CHATS, USER_TOPICS
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            # We don't overwrite ADMIN_IDS entirely from file to allow Env Var override, 
            # but you can adapt logic if needed. Here we trust Env/Local config primarily for Admins.
            # ADMIN_IDS = data.get("ADMIN_IDS", ADMIN_IDS) 
            
            FREE_CHANNELS = {int(k): v for k, v in data.get("FREE_CHANNELS", {}).items()}
            FREE_CHANNEL_LINKS = {int(k): v for k, v in data.get("FREE_CHANNEL_LINKS", {}).items()}
            
            pc_data = data.get("PAID_CHANNELS", {})
            if isinstance(pc_data, list):
                PAID_CHANNELS = {} 
                PAID_CHANNEL_LINKS = {}
            else:
                PAID_CHANNELS = {int(k): v for k, v in pc_data.items()}
                PAID_CHANNEL_LINKS = {int(k): v for k, v in data.get("PAID_CHANNEL_LINKS", {}).items()}
                
            BLOCKED_USER_IDS = set(data.get("BLOCKED_USER_IDS", []))
            ACTIVE_CHATS = {int(k): v for k, v in data.get("ACTIVE_CHATS", {}).items()}
            USER_TOPICS = {int(k): v for k, v in data.get("USER_TOPICS", {}).items()}
    except FileNotFoundError: save_data()

# --- HELPER FUNCTIONS ---
def is_owner(uid): return uid == OWNER_ID
def is_admin(uid): return uid in ADMIN_IDS

async def get_or_create_user_topic(user, context):
    if not SUPPORT_GROUP_ID: return None
    if user.id in USER_TOPICS: return USER_TOPICS[user.id]
    
    try:
        topic_name = f"{user.first_name[:30]} ({user.id})"
        topic = await context.bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=topic_name)
        USER_TOPICS[user.id] = topic.message_thread_id
        save_data()
        
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID, 
            message_thread_id=topic.message_thread_id,
            text=f"🆕 **New Session Started**\nUser: {user.full_name}\nID: `{user.id}`\n\n(History access nahi ho sakti, manual link karein).",
            parse_mode='Markdown'
        )
        return topic.message_thread_id
    except Exception as e:
        logger.error(f"Topic Create Error: {e}")
        return None

async def send_user_info_to_topic(user, chat_id, context):
    topic_id = await get_or_create_user_topic(user, context)
    if not topic_id: return
    try:
        full_chat = await context.bot.get_chat(user.id)
        bio = full_chat.bio if full_chat.bio else "No Bio"
        username = f"@{user.username}" if user.username else "None"
        text = (f"ℹ️ **User Profile**\n\nName: {user.full_name}\nID: `{user.id}`\nUsername: {username}\nBio: {bio}")
        await context.bot.send_message(chat_id=SUPPORT_GROUP_ID, message_thread_id=topic_id, text=text, parse_mode='Markdown')
    except Exception: pass

async def is_member(user_id, context):
    if is_admin(user_id) or not MANDATORY_CHANNEL_ID: return True
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        return m.status in [ChatMember.OWNER, ChatMember.ADMINISTRATOR, ChatMember.MEMBER]
    except: return False

async def remove_user_from_free(user_id, context):
    if is_admin(user_id): return
    for cid in FREE_CHANNELS:
        try:
            await context.bot.ban_chat_member(cid, user_id)
            await context.bot.unban_chat_member(cid, user_id)
        except: pass

# --- AUTO COMMAND MENU SETTER ---
async def post_init(application: Application):
    """Sets the command menu based on user roles at startup."""
    print("Setting up Command Menus...")
    
    # 1. Default (For All Users)
    user_cmds = [
        ("start", "Main Menu"),
        ("help", "Help & Info"),
        ("id", "Get ID")
    ]
    await application.bot.set_my_commands(user_cmds) # Default scope

    # 2. Admin Commands
    admin_cmds = user_cmds + [
        ("admin", "Admin Panel"),
        ("broadcast", "Send Broadcast"),
        ("post", "Post to All Batches"),
        ("check", "Check User Details"),
        ("link", "Link Topic")
    ]

    # 3. Owner Commands
    owner_cmds = admin_cmds + [
        ("owner", "Owner Panel"),
        ("backup", "Download Backup")
    ]

    # Assign menus to specific Admin IDs
    for uid in ADMIN_IDS:
        try:
            cmds = owner_cmds if uid == OWNER_ID else admin_cmds
            await application.bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))
        except Exception as e:
            print(f"Failed to set menu for {uid}: {e}")

# --- HANDLERS ---
async def track_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = update.chat_member
    if not res: return
    u = res.from_user
    if is_admin(u.id): return
    
    if res.new_chat_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
        if res.chat.id == MANDATORY_CHANNEL_ID:
            await remove_user_from_free(u.id, context)
        elif res.chat.type == ChatType.PRIVATE:
             await remove_user_from_free(u.id, context)
             if LOG_CHANNEL_ID:
                 try: await context.bot.send_message(LOG_CHANNEL_ID, f"🚫 Blocked: {u.full_name} ({u.id})")
                 except: pass

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user or user.id in BLOCKED_USER_IDS: return

    USER_DATA[user.id] = {'full_name': user.full_name, 'username': user.username}
    save_data()
    
    if SUPPORT_GROUP_ID:
        await send_user_info_to_topic(user, chat.id, context)

    # Simple Role-Based Response
    if is_owner(user.id):
        await update.message.reply_text(
            f"👑 **Owner Menu**\n\nUse /owner or buttons below.", 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Owner Panel", callback_data='owner_panel')]])
        )
    elif is_admin(user.id):
        await update.message.reply_text(
            f"👮‍♂️ **Admin Menu**\n\nUse /admin or buttons below.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 Admin Panel", callback_data='admin_panel')]])
        )
    else:
        if await is_member(user.id, context):
            kb = [[InlineKeyboardButton("🆓 Free Batches", callback_data='show_free'), InlineKeyboardButton("💎 Paid Batches", callback_data='show_paid')],
                  [InlineKeyboardButton("📢 Channel", url=MANDATORY_CHANNEL_LINK or "#"), InlineKeyboardButton("📞 Support", url=CONTACT_ADMIN_LINK or "#")],
                  [InlineKeyboardButton("🆔 My ID", callback_data='get_my_id')]]
            await update.message.reply_text(f"👋 Namaste {user.first_name}!", reply_markup=InlineKeyboardMarkup(kb))
        else:
            kb = [[InlineKeyboardButton("➡️ Join Channel", url=MANDATORY_CHANNEL_LINK or "#")], [InlineKeyboardButton("✅ I joined", callback_data='verify')]]
            await update.message.reply_text("⚠️ Bot use karne ke liye Channel Join karein.", reply_markup=InlineKeyboardMarkup(kb))

# --- COMMAND HANDLERS (SHORTCUTS) ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_owner(user.id):
        txt = (
            "👑 **Owner Commands:**\n"
            "/start - Start Bot\n"
            "/owner - Manage Admins/Users\n"
            "/admin - Manage Channels\n"
            "/broadcast - Send Msg to All\n"
            "/post - Post to Batches\n"
            "/backup - Download Data\n"
            "/check <id> - User Info\n"
            "/link <id> - Merge Chat"
        )
    elif is_admin(user.id):
        txt = (
            "👮‍♂️ **Admin Commands:**\n"
            "/start - Start Bot\n"
            "/admin - Manage Channels\n"
            "/broadcast - Send Msg to All\n"
            "/post - Post to Batches\n"
            "/check <id> - User Info\n"
            "/link <id> - Merge Chat"
        )
    else:
        txt = (
            "👤 **User Commands:**\n"
            "/start - Main Menu\n"
            "/id - Get Your ID\n"
            "/help - Show this message"
        )
    await update.message.reply_text(txt, parse_mode='Markdown')

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Your ID: `{update.effective_user.id}`\n💬 Chat ID: `{update.effective_chat.id}`", parse_mode='Markdown')

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    kb = [[InlineKeyboardButton("📢 Broadcast", callback_data='ask_bc'), InlineKeyboardButton("✍️ Post to All", callback_data='ask_post')],
          [InlineKeyboardButton("Manage Free", callback_data='mng_free'), InlineKeyboardButton("Manage Paid", callback_data='mng_paid')]]
    await update.message.reply_text("👮‍♂️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def owner_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    kb = [[InlineKeyboardButton("Users", callback_data='mng_users'), InlineKeyboardButton("⬇️ Backup Data", callback_data='get_backup')]]
    await update.message.reply_text("🔑 **Owner Panel**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data['step'] = 'bc'
    await update.message.reply_text("📢 **Broadcast Mode**\nAb jo message bhejoge, wo sabhi users ko jayega.\n(Cancel karne ke liye /start dabayein).", parse_mode='Markdown')

async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data['step'] = 'post'
    await update.message.reply_text("✍️ **Post Mode**\nAb jo message bhejoge, wo sabhi Free + Paid Batches me jayega.", parse_mode='Markdown')

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/check 123456789`", parse_mode='Markdown')
        return
    # Use existing check logic via simulating text input
    context.user_data['step'] = 'check_u'
    # Hack: Inject ID into update message to reuse handle_message logic or just run logic here.
    # Running logic directly is cleaner:
    target_id = context.args[0]
    await handle_check_logic(update, context, target_id)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if os.path.exists(DATA_FILE):
        await context.bot.send_document(chat_id=update.effective_user.id, document=open(DATA_FILE, 'rb'), caption="📦 **Backup File**", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ No Data Found.")

async def link_topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not update.message.message_thread_id:
        await update.message.reply_text("❌ Command Topic ke andar use karein.")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/link USER_ID`")
        return
    try:
        target_uid = int(context.args[0])
        USER_TOPICS[target_uid] = update.message.message_thread_id
        save_data()
        await update.message.reply_text(f"✅ Linked User `{target_uid}`.", parse_mode='Markdown')
    except: await update.message.reply_text("❌ Invalid ID.")

# --- CALLBACK HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid in BLOCKED_USER_IDS: return await q.answer("Blocked", show_alert=True)
    
    if q.data == 'verify':
        if await is_member(uid, context):
            await q.answer("Verified!")
            await start_command(update, context)
        else: await q.answer("Not joined yet!", show_alert=True)

    elif q.data == 'get_my_id':
        await q.answer(f"ID: {uid}", show_alert=True)

    # Panels
    elif q.data == 'admin_panel' and is_admin(uid):
        await admin_panel_command(update, context) # Reuse command logic
        await q.answer()

    elif q.data == 'owner_panel' and is_owner(uid):
        await owner_panel_command(update, context)
        await q.answer()
    
    elif q.data == 'main_owner': await start_command(update, context)

    # Backup Button
    elif q.data == 'get_backup' and is_owner(uid):
        await backup_command(update, context)
        await q.answer()

    # Input Steps
    elif q.data.startswith('ask_') and is_admin(uid):
        act = q.data.split('_')[1]
        context.user_data['step'] = act
        prompts = {'bc': "Broadcast Msg bhejo:", 'post': "Batch Post bhejo:", 'check_u': "User ID bhejo:"}
        await q.edit_message_text(prompts.get(act, f"Input for {act}:"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data='admin_panel')]]))

    # Store Display
    elif q.data == 'show_free':
        kb = [[InlineKeyboardButton(f"🆓 {t}", callback_data=f'join_f_{c}')] for c, t in FREE_CHANNELS.items()]
        kb.append([InlineKeyboardButton("Back", callback_data='start_member')])
        await q.edit_message_text("Free Batches:", reply_markup=InlineKeyboardMarkup(kb))
    
    elif q.data == 'show_paid':
        kb = [[InlineKeyboardButton(f"💎 {t}", callback_data=f'join_p_{c}')] for c, t in PAID_CHANNELS.items()]
        kb.append([InlineKeyboardButton("Back", callback_data='start_member')])
        await q.edit_message_text("Paid Batches:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'start_member': await start_command(update, context)

    # Manage Menus
    elif q.data == 'mng_free':
        kb = [[InlineKeyboardButton("Add", callback_data='ask_addf'), InlineKeyboardButton("Remove", callback_data='ask_remf')],
              [InlineKeyboardButton("List", callback_data='lst_f'), InlineKeyboardButton("Back", callback_data='admin_panel')]]
        await q.edit_message_text("Manage Free Batches:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'mng_paid':
        kb = [[InlineKeyboardButton("Add", callback_data='ask_addp'), InlineKeyboardButton("Remove", callback_data='ask_remp')],
              [InlineKeyboardButton("List", callback_data='lst_p'), InlineKeyboardButton("Back", callback_data='admin_panel')]]
        await q.edit_message_text("Manage Paid Batches:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'mng_users':
        kb = [[InlineKeyboardButton("List Users", callback_data='lst_u'), InlineKeyboardButton("Block User", callback_data='ask_blk')],
              [InlineKeyboardButton("🔎 Check User", callback_data='ask_check_u'), InlineKeyboardButton("Back", callback_data='owner_panel')]]
        await q.edit_message_text("User Management:", reply_markup=InlineKeyboardMarkup(kb))

    # Lists
    elif q.data == 'lst_u':
        await q.edit_message_text(f"Total Users: {len(USER_DATA)}\nBlocked: {len(BLOCKED_USER_IDS)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data='mng_users')]]))
    elif q.data == 'lst_f':
        txt = "\n".join([f"{c}: {t}" for c,t in FREE_CHANNELS.items()]) or "No Free Batches."
        await q.edit_message_text(txt[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data='mng_free')]]))
    elif q.data == 'lst_p':
        txt = "\n".join([f"{c}: {t}" for c,t in PAID_CHANNELS.items()]) or "No Paid Batches."
        await q.edit_message_text(txt[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data='mng_paid')]]))

    # Join Links
    elif q.data.startswith('join_f_'):
        cid = int(q.data.split('_')[2])
        if cid in FREE_CHANNEL_LINKS:
            await q.answer()
            await context.bot.send_message(uid, "Link:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url=FREE_CHANNEL_LINKS[cid])]]))
    elif q.data.startswith('join_p_'):
        cid = int(q.data.split('_')[2])
        if cid in PAID_CHANNEL_LINKS:
            await q.answer()
            await context.bot.send_message(uid, "Link:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url=PAID_CHANNEL_LINKS[cid])]]))
            
    await q.answer()

# --- REUSABLE LOGIC ---
async def handle_check_logic(update, context, target_id_str):
    try:
        target_id = int(target_id_str)
        report = f"🔍 **User Report: `{target_id}`**\n\n"
        
        # Check Mandatory
        try:
            m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, target_id)
            status = "✅ Joined" if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER] else "❌ Not Joined"
            report += f"📢 Main Channel: {status}\n\n"
        except: report += f"📢 Main Channel: ❓ Error\n\n"

        # Check Batches
        report += "🆓 **Free Batches:**\n"
        found = False
        for cid, title in FREE_CHANNELS.items():
            try:
                m = await context.bot.get_chat_member(cid, target_id)
                if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                    report += f"✅ {title}\n"; found = True
            except: report += f"❓ {title} (Check Failed)\n"
        
        report += "\n💎 **Paid Batches:**\n"
        for cid, title in PAID_CHANNELS.items():
            try:
                m = await context.bot.get_chat_member(cid, target_id)
                if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                    report += f"✅ {title}\n"; found = True
            except: report += f"❓ {title} (Check Failed)\n"

        if not found: report += "\n❌ User kisi batch me nahi hai."
        await update.message.reply_text(report, parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format.")

# --- UNIVERSAL MESSAGE HANDLER ---
async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user: return

    # --- 1. ADMIN REPLY (Topic -> Private Chat) ---
    # Agar message Support Group me aaya hai aur Topic ID hai
    if chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        topic_id = update.message.message_thread_id
        target_user_id = None
        
        # Topic ID se User ID dhoondho
        for uid, tid in USER_TOPICS.items():
            if tid == topic_id:
                target_user_id = uid
                break
        
        # Message Copy karo User ko
        if target_user_id:
            try:
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=chat.id,
                    message_id=update.message.id
                )
            except Exception as e:
                # Agar user ne block kiya hai ya koi error hai
                await update.message.reply_text(f"❌ Failed to send: {e}")
        return

    # --- 2. RESTORE DATA (Owner Only) ---
    if is_owner(user.id) and update.message.document:
        doc = update.message.document
        if update.message.caption and "restore" in update.message.caption.lower():
            if doc.file_name == "bot_data.json":
                try:
                    f = await doc.get_file()
                    await f.download_to_drive(DATA_FILE)
                    load_data()
                    await update.message.reply_text("✅ Data Restored!")
                except Exception as e:
                    await update.message.reply_text(f"❌ Restore Failed: {e}")
            return

    # --- 3. ADMIN WIZARD INPUTS ---
    if is_admin(user.id) and 'step' in context.user_data:
        step = context.user_data.pop('step')
        
        if step == 'check_u':
            await handle_check_logic(update, context, update.message.text)
            return

        # Broadcast
        if step == 'bc':
            status = await update.message.reply_text("⏳ Broadcasting...")
            s, f = 0, 0
            for u in list(USER_DATA.keys()):
                try: 
                    await context.bot.copy_message(chat_id=u, from_chat_id=chat.id, message_id=update.message.id)
                    s += 1
                except: f += 1
            await context.bot.edit_message_text(chat_id=chat.id, message_id=status.message_id, text=f"✅ Broadcast Complete.\nSent: {s}\nFailed: {f}")
            return
            
        # Post to Batches
        elif step == 'post':
            status = await update.message.reply_text("⏳ Posting to ALL Batches...")
            s, f = 0, 0
            all_batches = list(set(list(FREE_CHANNELS.keys()) + list(PAID_CHANNELS.keys())))
            for c in all_batches:
                try: 
                    await context.bot.copy_message(chat_id=c, from_chat_id=chat.id, message_id=update.message.id)
                    s += 1
                except: f += 1
            await context.bot.edit_message_text(chat_id=chat.id, message_id=status.message_id, text=f"✅ Posting Complete.\nSent: {s}\nFailed: {f}")
            return

        text = update.message.text
        if not text: return

        # Add/Remove Logic
        if step == 'addf':
            context.user_data['n'] = text
            context.user_data['step'] = 'addf_l'
            await update.message.reply_text("Send Link:")
        elif step == 'addf_l':
            context.user_data['l'] = text
            context.user_data['step'] = 'addf_id'
            await update.message.reply_text("Send Channel ID:")
        elif step == 'addf_id':
            try:
                cid = int(text)
                FREE_CHANNELS[cid] = context.user_data['n']
                FREE_CHANNEL_LINKS[cid] = context.user_data['l']
                save_data()
                await update.message.reply_text("✅ Free Batch Added.")
            except: await update.message.reply_text("❌ Invalid ID.")

        elif step == 'remf':
            try:
                cid = int(text)
                if cid in FREE_CHANNELS:
                    del FREE_CHANNELS[cid]
                    if cid in FREE_CHANNEL_LINKS: del FREE_CHANNEL_LINKS[cid]
                    save_data()
                    await update.message.reply_text("✅ Removed.")
            except: pass

        elif step == 'addp':
            context.user_data['n'] = text
            context.user_data['step'] = 'addp_l'
            await update.message.reply_text("Send Link:")
        elif step == 'addp_l':
            context.user_data['l'] = text
            context.user_data['step'] = 'addp_id'
            await update.message.reply_text("Send Channel ID:")
        elif step == 'addp_id':
            try:
                cid = int(text)
                PAID_CHANNELS[cid] = context.user_data['n']
                PAID_CHANNEL_LINKS[cid] = context.user_data['l']
                save_data()
                await update.message.reply_text("✅ Paid Batch Added.")
            except: await update.message.reply_text("❌ Invalid ID.")
        
        elif step == 'remp':
            try:
                cid = int(text)
                if cid in PAID_CHANNELS:
                    del PAID_CHANNELS[cid]
                    if cid in PAID_CHANNEL_LINKS: del PAID_CHANNEL_LINKS[cid]
                    save_data()
                    await update.message.reply_text("✅ Removed.")
            except: pass

        elif step == 'blk':
            try:
                uid = int(text)
                BLOCKED_USER_IDS.add(uid)
                save_data()
                await update.message.reply_text("🚫 User Blocked.")
            except: pass

        return

    # --- 4. SUPPORT FORWARDING (Private Chat -> Topic) ---
    if chat.type == ChatType.PRIVATE and SUPPORT_GROUP_ID:
        topic_id = await get_or_create_user_topic(user, context)
        if topic_id:
            try:
                await context.bot.forward_message(
                    chat_id=SUPPORT_GROUP_ID,
                    from_chat_id=user.id,
                    message_id=update.message.id,
                    message_thread_id=topic_id
                )
            except Exception as e:
                logger.error(f"Fwd Error: {e}")

def main():
    load_data()
    # Use post_init to set commands on startup
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # User Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    
    # Admin Commands
    app.add_handler(CommandHandler("admin", admin_panel_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("link", link_topic_command))
    
    # Owner Commands
    app.add_handler(CommandHandler("owner", owner_panel_command))
    app.add_handler(CommandHandler("backup", backup_command))

    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Updated Filter: Allows text/media from BOTH private chats AND groups (support group)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.DOCUMENT | filters.VOICE | filters.STICKER) & ~filters.COMMAND, 
        handle_message_input
    ))
    
    app.add_handler(ChatMemberHandler(track_status, ChatMemberHandler.CHAT_MEMBER))
    
    print("Bot is Running...")
    app.run_polling()

if __name__ == '__main__':
    main()
