# -*- coding: utf-8 -*-

"""
A Telegram Bot to manage channels with a robust Owner/Admin role system,
mandatory join, block detection, and a Forum-based Support System.
"""

import logging
import json
import os
import threading
from telegram import Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, ChatMemberHandler, CallbackQueryHandler, MessageHandler, filters

# --- KEEPALIVE WEB SERVER ---
try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "0") or "0")
        if port:
            app = Flask(__name__)
            @app.get("/")
            def _index(): return "OK", 200
            @app.get("/health")
            def _health(): return "ok", 200
            th = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False), daemon=True)
            th.start()
except Exception:
    def _start_keepalive(): pass
_start_keepalive()

# --- CONFIGURATION SECTION ---

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    # Fallback for testing if env var is missing (Optional)
    # TELEGRAM_BOT_TOKEN = "YOUR_TOKEN_HERE_IF_NEEDED"
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

OWNER_ID = int(os.environ.get("OWNER_ID", 0))
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(',') if x.strip()]

MANDATORY_CHANNEL_ID = int(os.environ.get("MANDATORY_CHANNEL_ID", 0))
MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK")
CONTACT_ADMIN_LINK = os.environ.get("CONTACT_ADMIN_LINK")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))

# ==============================================================================
# 👇👇👇 YAHAN APNA SUPPORT GROUP ID DALEIN (HARDCODED) 👇👇👇
# Agar aap Heroku Env Variable set nahi kar pa rahe, to apna ID yahan likh dein.
# Example: -100123456789 (Minus sign zaroori hai agar Supergroup hai)
# ==============================================================================
HARDCODED_SUPPORT_ID = -1002367377217  # <--- REPLACE THIS WITH YOUR GROUP ID

# Logic: Pehle Env Var check karega, agar nahi mila to upar wala ID use karega
SUPPORT_GROUP_ID = int(os.environ.get("SUPPORT_GROUP_ID", HARDCODED_SUPPORT_ID))

DATA_FILE = os.environ.get("DATA_FILE") or "bot_data.json"

if OWNER_ID and OWNER_ID not in ADMIN_IDS: ADMIN_IDS.append(OWNER_ID)

# --- DYNAMIC DATA ---
FREE_CHANNELS = {}
FREE_CHANNEL_LINKS = {}
PAID_CHANNELS = []
USER_DATA = {}
BLOCKED_USER_IDS = set()
ACTIVE_CHATS = {}
USER_TOPICS = {} # Maps User ID -> Forum Topic ID

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PERSISTENCE ---
def save_data():
    try:
        # Check if path has directory structure
        if "/" in DATA_FILE: os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        data = {
            "ADMIN_IDS": ADMIN_IDS,
            "FREE_CHANNELS": FREE_CHANNELS,
            "FREE_CHANNEL_LINKS": FREE_CHANNEL_LINKS,
            "PAID_CHANNELS": PAID_CHANNELS,
            "BLOCKED_USER_IDS": list(BLOCKED_USER_IDS),
            "ACTIVE_CHATS": ACTIVE_CHATS,
            "USER_TOPICS": USER_TOPICS
        }
        with open(DATA_FILE, "w") as f: json.dump(data, f, indent=4)
    except Exception as e: logger.error(f"Save Error: {e}")

def load_data():
    global ADMIN_IDS, FREE_CHANNELS, FREE_CHANNEL_LINKS, PAID_CHANNELS, BLOCKED_USER_IDS, ACTIVE_CHATS, USER_TOPICS
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            ADMIN_IDS = data.get("ADMIN_IDS", ADMIN_IDS)
            FREE_CHANNELS = {int(k): v for k, v in data.get("FREE_CHANNELS", {}).items()}
            FREE_CHANNEL_LINKS = {int(k): v for k, v in data.get("FREE_CHANNEL_LINKS", {}).items()}
            PAID_CHANNELS = data.get("PAID_CHANNELS", [])
            BLOCKED_USER_IDS = set(data.get("BLOCKED_USER_IDS", []))
            ACTIVE_CHATS = {int(k): v for k, v in data.get("ACTIVE_CHATS", {}).items()}
            USER_TOPICS = {int(k): v for k, v in data.get("USER_TOPICS", {}).items()}
    except FileNotFoundError: save_data()

# --- PERMISSIONS ---
def is_owner(uid): return uid == OWNER_ID
def is_admin(uid): return uid in ADMIN_IDS

# --- SUPPORT SYSTEM HELPERS ---
async def get_or_create_user_topic(user, context):
    """Creates a forum topic in SUPPORT_GROUP_ID for the user."""
    # Check if Support Group ID is valid (Not 0 and Not placeholder)
    if not SUPPORT_GROUP_ID or SUPPORT_GROUP_ID == -100123456789: 
        if SUPPORT_GROUP_ID == -100123456789:
            logger.warning("Support Group ID is set to default placeholder. Please update it.")
        return None
    
    # Return existing topic if found
    if user.id in USER_TOPICS: 
        return USER_TOPICS[user.id]
    
    try:
        # Create Topic
        topic_name = f"{user.first_name[:30]} ({user.id})"
        topic = await context.bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=topic_name)
        
        USER_TOPICS[user.id] = topic.message_thread_id
        save_data()
        
        # Send Alert in new topic
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID, 
            message_thread_id=topic.message_thread_id,
            text=f"🆕 **New Ticket Created**\nUser: {user.full_name}\nID: `{user.id}`",
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
        username = f"@{user.username}" if user.username else "No Username"
        
        text = (f"ℹ️ **User Started Bot**\n\n"
                f"👤 Name: {user.full_name}\n"
                f"🆔 User ID: `{user.id}`\n"
                f"💬 Chat ID: `{chat_id}`\n"
                f"🔗 Username: {username}\n"
                f"📝 Bio: {bio}")
        
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID, 
            message_thread_id=topic_id, 
            text=text, 
            parse_mode='Markdown'
        )
    except Exception: pass

# --- HELPERS ---
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
    
    # --- SUPPORT TRIGGER ---
    if SUPPORT_GROUP_ID:
        await send_user_info_to_topic(user, chat.id, context)

    # --- MENUS ---
    if is_owner(user.id):
        kb = [[InlineKeyboardButton("👑 Admin Panel", callback_data='admin_panel')], [InlineKeyboardButton("🔑 Owner Panel", callback_data='owner_panel')]]
        await update.message.reply_text("Hello Owner!", reply_markup=InlineKeyboardMarkup(kb))
    elif is_admin(user.id):
        kb = [[InlineKeyboardButton("👑 Admin Panel", callback_data='admin_panel')]]
        await update.message.reply_text("Hello Admin!", reply_markup=InlineKeyboardMarkup(kb))
    else:
        if await is_member(user.id, context):
            kb = [[InlineKeyboardButton("🆓 Free Batches", callback_data='show_free'), InlineKeyboardButton("💎 Paid Channels", callback_data='show_paid')],
                  [InlineKeyboardButton("📢 Channel", url=MANDATORY_CHANNEL_LINK or "#"), InlineKeyboardButton("📞 Contact Support", url=CONTACT_ADMIN_LINK or "#")],
                  [InlineKeyboardButton("🆔 My ID", callback_data='get_my_id')]]
            await update.message.reply_text(f"Welcome {user.first_name}!", reply_markup=InlineKeyboardMarkup(kb))
        else:
            kb = [[InlineKeyboardButton("➡️ Join Channel", url=MANDATORY_CHANNEL_LINK or "#")], [InlineKeyboardButton("✅ I have joined", callback_data='verify')]]
            await update.message.reply_text("Please join our channel first.", reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid in BLOCKED_USER_IDS: return await q.answer("Blocked", show_alert=True)
    
    if q.data == 'verify':
        if await is_member(uid, context):
            await q.answer("Verified!")
            await start_command(update, context)
        else: await q.answer("Not joined!", show_alert=True)

    elif q.data == 'get_my_id':
        await q.answer(f"ID: {uid}", show_alert=True)

    # Admin Navigation
    elif q.data == 'admin_panel' and is_admin(uid):
        kb = [[InlineKeyboardButton("📢 Broadcast", callback_data='ask_bc'), InlineKeyboardButton("✍️ Post", callback_data='ask_post')],
              [InlineKeyboardButton("Manage Free", callback_data='mng_free'), InlineKeyboardButton("Manage Paid", callback_data='mng_paid')]]
        if is_owner(uid): kb.append([InlineKeyboardButton("Back", callback_data='main_owner')])
        await q.edit_message_text("Admin Panel:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'owner_panel' and is_owner(uid):
        kb = [[InlineKeyboardButton("Users", callback_data='mng_users')], [InlineKeyboardButton("Back", callback_data='main_owner')]]
        await q.edit_message_text("Owner Panel:", reply_markup=InlineKeyboardMarkup(kb))
    
    elif q.data == 'main_owner': await start_command(update, context)

    # Inputs
    elif q.data.startswith('ask_') and is_admin(uid):
        act = q.data.split('_')[1]
        context.user_data['step'] = act
        await q.edit_message_text(f"Send input for: {act}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data='admin_panel')]]))

    # List Views
    elif q.data == 'show_free':
        kb = [[InlineKeyboardButton(f"🆓 {t}", callback_data=f'join_f_{c}')] for c, t in FREE_CHANNELS.items()]
        kb.append([InlineKeyboardButton("Back", callback_data='start_member')])
        await q.edit_message_text("Free Batches:", reply_markup=InlineKeyboardMarkup(kb))
    
    elif q.data == 'show_paid':
        kb = [[InlineKeyboardButton(f"Paid {i+1}", callback_data=f'join_p_{i}')] for i, _ in enumerate(PAID_CHANNELS)]
        kb.append([InlineKeyboardButton("Back", callback_data='start_member')])
        await q.edit_message_text("Paid Batches:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'start_member': await start_command(update, context)

    # Management
    elif q.data == 'mng_free':
        kb = [[InlineKeyboardButton("Add", callback_data='ask_addf'), InlineKeyboardButton("Remove", callback_data='ask_remf')],
              [InlineKeyboardButton("List", callback_data='lst_f'), InlineKeyboardButton("Back", callback_data='admin_panel')]]
        await q.edit_message_text("Manage Free:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'mng_paid':
        kb = [[InlineKeyboardButton("Add", callback_data='ask_addp'), InlineKeyboardButton("Remove", callback_data='ask_remp')],
              [InlineKeyboardButton("List", callback_data='lst_p'), InlineKeyboardButton("Back", callback_data='admin_panel')]]
        await q.edit_message_text("Manage Paid:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'mng_users':
        kb = [[InlineKeyboardButton("List Users", callback_data='lst_u'), InlineKeyboardButton("Block", callback_data='ask_blk')],
              [InlineKeyboardButton("Back", callback_data='owner_panel')]]
        await q.edit_message_text("Manage Users:", reply_markup=InlineKeyboardMarkup(kb))

    elif q.data == 'lst_u':
        # Simple count display for brevity
        await q.edit_message_text(f"Total Users: {len(USER_DATA)}\nBlocked: {len(BLOCKED_USER_IDS)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data='mng_users')]]))
        
    elif q.data == 'lst_f':
        txt = "\n".join([f"{c}: {t}" for c,t in FREE_CHANNELS.items()]) or "Empty"
        await q.edit_message_text(txt[:4000], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data='mng_free')]]))

    elif q.data == 'lst_p':
        txt = "\n".join(PAID_CHANNELS) or "Empty"
        await q.edit_message_text(txt[:4000], parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data='mng_paid')]]))

    # Join Actions
    elif q.data.startswith('join_f_'):
        cid = int(q.data.split('_')[2])
        if cid in FREE_CHANNEL_LINKS:
            await q.answer()
            await context.bot.send_message(uid, "Link:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join", url=FREE_CHANNEL_LINKS[cid])]]))

    elif q.data.startswith('join_p_'):
        idx = int(q.data.split('_')[2])
        if 0 <= idx < len(PAID_CHANNELS):
            try:
                lnk = PAID_CHANNELS[idx].split("href='")[1].split("'")[0]
                await q.answer()
                await context.bot.send_message(uid, "Link:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join", url=lnk)]]))
            except: pass
            
    await q.answer()


# --- UNIVERSAL MESSAGE HANDLER ---
async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user: return

    # 1. ADMIN WIZARD INPUTS
    if is_admin(user.id) and 'step' in context.user_data:
        step = context.user_data.pop('step')
        text = update.message.text
        
        if step == 'bc':
            for u in USER_DATA:
                try: await context.bot.send_message(u, text); 
                except: pass
            await update.message.reply_text("Broadcast Done.")
            
        elif step == 'post':
            for c in FREE_CHANNELS:
                try: await context.bot.send_message(c, text); 
                except: pass
            await update.message.reply_text("Posted.")

        elif step == 'addf':
            context.user_data['n'] = text
            context.user_data['step'] = 'addf_l'
            await update.message.reply_text("Link:")
        elif step == 'addf_l':
            context.user_data['l'] = text
            context.user_data['step'] = 'addf_id'
            await update.message.reply_text("Channel ID:")
        elif step == 'addf_id':
            try:
                cid = int(text)
                FREE_CHANNELS[cid] = context.user_data['n']
                FREE_CHANNEL_LINKS[cid] = context.user_data['l']
                save_data()
                await update.message.reply_text("Added.")
            except: await update.message.reply_text("Invalid ID.")

        elif step == 'remf':
            try:
                cid = int(text)
                if cid in FREE_CHANNELS:
                    del FREE_CHANNELS[cid]
                    if cid in FREE_CHANNEL_LINKS: del FREE_CHANNEL_LINKS[cid]
                    save_data()
                    await update.message.reply_text("Removed.")
                else: await update.message.reply_text("Not found.")
            except: pass

        elif step == 'addp':
            context.user_data['n'] = text
            context.user_data['step'] = 'addp_l'
            await update.message.reply_text("Link:")
        elif step == 'addp_l':
            entry = f"<a href='{text}'>{context.user_data['n']}</a>"
            PAID_CHANNELS.append(entry)
            save_data()
            await update.message.reply_text("Added.")
        
        elif step == 'remp':
            try:
                idx = int(text) - 1
                if 0 <= idx < len(PAID_CHANNELS):
                    PAID_CHANNELS.pop(idx)
                    save_data()
                    await update.message.reply_text("Removed.")
            except: pass

        elif step == 'blk':
            try:
                uid = int(text)
                BLOCKED_USER_IDS.add(uid)
                save_data()
                await update.message.reply_text("Blocked.")
            except: pass

        return

    # 2. SUPPORT FORWARDING (Private Chat -> Topic)
    if chat.type == ChatType.PRIVATE and SUPPORT_GROUP_ID:
        topic_id = await get_or_create_user_topic(user, context)
        if topic_id:
            try:
                # Forward msg to the specific topic
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
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Updated Filter: Text + Media
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.DOCUMENT) & ~filters.COMMAND, 
        handle_message_input
    ))
    
    app.add_handler(ChatMemberHandler(track_status, ChatMemberHandler.CHAT_MEMBER))
    
    print("Bot Running with Support System...")
    app.run_polling()

if __name__ == '__main__':
    main()
