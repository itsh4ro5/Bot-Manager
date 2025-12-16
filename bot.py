# -*- coding: utf-8 -*-

"""
ALL-IN-ONE TELEGRAM BOT (UPGRADED)
Features: Support System (Topics), Store (Free/Paid), Broadcast, Auto-Kick, Data Backup, 
          Smart Links (One-Time/Demo), Reaction Sync, Dynamic Admins.
"""

import logging
import json
import os
import threading
import asyncio
from datetime import timedelta
from telegram import (
    Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, 
    BotCommandScopeChat, ChatJoinRequest, ChatPermissions
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, ChatMemberHandler, 
    CallbackQueryHandler, MessageHandler, filters, Application, ChatJoinRequestHandler,
    MessageReactionHandler
)

# --- KEEPALIVE WEB SERVER ---
try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "0") or "0")
        if port:
            app = Flask(__name__)
            @app.get("/")
            def _index(): return "Bot is Running!", 200
            @app.get("/health")
            def _health(): return "OK", 200
            th = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False), daemon=True)
            th.start()
            print(f"Flask Server started on port {port}")
except Exception:
    def _start_keepalive(): pass
_start_keepalive()

# --- CONFIGURATION ---
# Fallback values for local testing
LOCAL_BOT_TOKEN = "YOUR_BOT_TOKEN"
LOCAL_OWNER_ID = 0
LOCAL_MANDATORY_CHANNEL = -100123456789

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", LOCAL_BOT_TOKEN)
OWNER_ID = int(os.environ.get("OWNER_ID", LOCAL_OWNER_ID))
MANDATORY_CHANNEL_ID = int(os.environ.get("MANDATORY_CHANNEL_ID", LOCAL_MANDATORY_CHANNEL))
MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK", "https://t.me/...")
SUPPORT_GROUP_ID = int(os.environ.get("SUPPORT_GROUP_ID", 0))
CONTACT_ADMIN_LINK = os.environ.get("CONTACT_ADMIN_LINK", "https://t.me/...")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))
DATA_FILE = os.environ.get("DATA_FILE", "bot_data.json")

# --- GLOBAL DATA STRUCTURES ---
# Saved in JSON
ADMIN_IDS = []
FREE_CHANNELS = {}  # {id: name}
PAID_CHANNELS = {}  # {id: name}
USER_DATA = {}      # {id: {name, username}}
BLOCKED_USER_IDS = set()
USER_TOPICS = {}    # {user_id: topic_id}
MESSAGE_MAP = {}    # {(chat_id, msg_id): (target_chat_id, target_msg_id)} - For sync

# Runtime only (not saved)
DEMO_PENDING = {}   # {user_id: batch_id} - Tracks who requested a demo link

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- PERSISTENCE ---
def save_data():
    try:
        if "/" in DATA_FILE: os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        # Convert keys to strings for JSON, sets to lists
        data = {
            "ADMIN_IDS": ADMIN_IDS,
            "FREE_CHANNELS": {str(k): v for k, v in FREE_CHANNELS.items()},
            "PAID_CHANNELS": {str(k): v for k, v in PAID_CHANNELS.items()},
            "BLOCKED_USER_IDS": list(BLOCKED_USER_IDS),
            "USER_TOPICS": {str(k): v for k, v in USER_TOPICS.items()},
            # We don't save MESSAGE_MAP to keep JSON small, but for production use a DB
            "USER_DATA": USER_DATA
        }
        with open(DATA_FILE, "w") as f: json.dump(data, f, indent=4)
    except Exception as e: logger.error(f"Save Error: {e}")

def load_data():
    global ADMIN_IDS, FREE_CHANNELS, PAID_CHANNELS, BLOCKED_USER_IDS, USER_TOPICS, USER_DATA
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            
            # Load Admins: Merge Env Admins with JSON Admins
            json_admins = data.get("ADMIN_IDS", [])
            env_admins_str = os.environ.get("ADMIN_IDS", "")
            env_admins = [int(x.strip()) for x in env_admins_str.split(',') if x.strip()]
            
            # Combine unique IDs
            combined = set(json_admins + env_admins)
            if OWNER_ID: combined.add(OWNER_ID)
            ADMIN_IDS = list(combined)

            FREE_CHANNELS = {int(k): v for k, v in data.get("FREE_CHANNELS", {}).items()}
            PAID_CHANNELS = {int(k): v for k, v in data.get("PAID_CHANNELS", {}).items()}
            BLOCKED_USER_IDS = set(data.get("BLOCKED_USER_IDS", []))
            USER_TOPICS = {int(k): v for k, v in data.get("USER_TOPICS", {}).items()}
            USER_DATA = {int(k): v for k, v in data.get("USER_DATA", {}).items()}
            
    except FileNotFoundError:
        ADMIN_IDS = [OWNER_ID] if OWNER_ID else []
        save_data()

# --- HELPER FUNCTIONS ---
def is_owner(uid): return uid == OWNER_ID
def is_admin(uid): return uid in ADMIN_IDS

async def get_or_create_user_topic(user, context):
    if not SUPPORT_GROUP_ID: return None
    if user.id in USER_TOPICS: 
        # Verify if topic still exists (optional, keeping simple)
        return USER_TOPICS[user.id]
    
    try:
        topic_name = f"{user.first_name[:30]} ({user.id})"
        topic = await context.bot.create_forum_topic(chat_id=SUPPORT_GROUP_ID, name=topic_name)
        USER_TOPICS[user.id] = topic.message_thread_id
        save_data()
        
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID, 
            message_thread_id=topic.message_thread_id,
            text=f"🆕 **New Session**\nUser: {user.full_name}\nID: `{user.id}`\n@{user.username}",
            parse_mode=ParseMode.MARKDOWN
        )
        return topic.message_thread_id
    except Exception as e:
        logger.error(f"Topic Create Error: {e}")
        return None

async def is_member(user_id, context):
    if is_admin(user_id) or not MANDATORY_CHANNEL_ID: return True
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        return m.status in [ChatMember.OWNER, ChatMember.ADMINISTRATOR, ChatMember.MEMBER]
    except: return False

# --- CORE HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id in BLOCKED_USER_IDS: return

    USER_DATA[user.id] = {'full_name': user.full_name, 'username': user.username}
    # Don't save on every start to reduce IO, rely on periodic or event based saves
    
    # Send user info to support if new session needed
    if SUPPORT_GROUP_ID and user.id not in USER_TOPICS:
        await get_or_create_user_topic(user, context)

    # Role Based Menu
    if is_owner(user.id):
        kb = [[InlineKeyboardButton("🔑 Owner Panel", callback_data='owner_panel')],
              [InlineKeyboardButton("👮‍♂️ Admin Panel", callback_data='admin_panel')]]
        await update.message.reply_text(f"👑 **Owner Menu**\nWelcome Boss.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif is_admin(user.id):
        kb = [[InlineKeyboardButton("👮‍♂️ Admin Panel", callback_data='admin_panel')]]
        await update.message.reply_text(f"👮‍♂️ **Admin Menu**\nReady to manage.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else:
        # User Menu
        if await is_member(user.id, context):
            kb = [
                [InlineKeyboardButton("🆓 Free Batches", callback_data='show_free'), InlineKeyboardButton("💎 Paid Batches", callback_data='show_paid')],
                [InlineKeyboardButton("📂 My Batches", callback_data='my_batches'), InlineKeyboardButton("📞 Support", url=CONTACT_ADMIN_LINK)],
                [InlineKeyboardButton("📢 Channel", url=MANDATORY_CHANNEL_LINK)]
            ]
            await update.message.reply_text(
                f"👋 **Welcome {user.first_name}!**\nSelect an option below to continue.", 
                reply_markup=InlineKeyboardMarkup(kb), 
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            kb = [[InlineKeyboardButton("➡️ Join Channel", url=MANDATORY_CHANNEL_LINK)], 
                  [InlineKeyboardButton("✅ I joined", callback_data='verify')]]
            await update.message.reply_text("⚠️ **Action Required**\nPlease join our mandatory channel to use this bot.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command for users to see their batches"""
    user_id = update.effective_user.id
    report = "📂 **Your Batches:**\n\n"
    
    # Check Free
    free_joined = []
    for cid, name in FREE_CHANNELS.items():
        try:
            m = await context.bot.get_chat_member(cid, user_id)
            if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                free_joined.append(name)
        except: pass
        
    # Check Paid
    paid_joined = []
    for cid, name in PAID_CHANNELS.items():
        try:
            m = await context.bot.get_chat_member(cid, user_id)
            if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                paid_joined.append(name)
        except: pass
    
    if free_joined: report += "🆓 **Free:**\n" + "\n".join([f"- {n}" for n in free_joined]) + "\n\n"
    if paid_joined: report += "💎 **Paid:**\n" + "\n".join([f"- {n}" for n in paid_joined])
    if not free_joined and not paid_joined: report = "❌ You haven't joined any batches yet."
    
    await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)

# --- ADMIN / OWNER COMMANDS ---

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not context.args: return await update.message.reply_text("Usage: /addadmin <id>")
    try:
        new_admin = int(context.args[0])
        if new_admin not in ADMIN_IDS:
            ADMIN_IDS.append(new_admin)
            save_data()
            await update.message.reply_text(f"✅ Admin {new_admin} added.")
        else:
            await update.message.reply_text("⚠️ Already an admin.")
    except: await update.message.reply_text("❌ Invalid ID.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not context.args: return await update.message.reply_text("Usage: /removeadmin <id>")
    try:
        rem_admin = int(context.args[0])
        if rem_admin in ADMIN_IDS:
            ADMIN_IDS.remove(rem_admin)
            save_data()
            await update.message.reply_text(f"✅ Admin {rem_admin} removed.")
        else:
            await update.message.reply_text("⚠️ Not an admin.")
    except: await update.message.reply_text("❌ Invalid ID.")

async def add_batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    # Start Wizard
    context.user_data['step'] = 'batch_type'
    kb = [[InlineKeyboardButton("🆓 Free", callback_data='type_free'), InlineKeyboardButton("💎 Paid", callback_data='type_paid')],
          [InlineKeyboardButton("Cancel", callback_data='admin_panel')]]
    await update.message.reply_text("🆕 **Add New Batch**\n\nSelect Batch Type:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def remove_batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    # List batches with remove buttons
    kb = []
    for cid, name in FREE_CHANNELS.items():
        kb.append([InlineKeyboardButton(f"❌ Del Free: {name}", callback_data=f'delbatch_f_{cid}')])
    for cid, name in PAID_CHANNELS.items():
        kb.append([InlineKeyboardButton(f"❌ Del Paid: {name}", callback_data=f'delbatch_p_{cid}')])
    kb.append([InlineKeyboardButton("Cancel", callback_data='admin_panel')])
    await update.message.reply_text("🗑 **Remove Batch**\nSelect to delete:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# --- CALLBACK HANDLER ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data
    
    if data == 'verify':
        if await is_member(uid, context):
            await q.answer("Verified! Welcome.")
            await start_command(update, context)
        else:
            await q.answer("❌ Still not joined!", show_alert=True)
            
    elif data == 'my_batches':
        # Simulate command
        await batch_command(update, context)
        await q.answer()

    # --- USER JOIN FLOWS ---
    elif data == 'show_free':
        kb = [[InlineKeyboardButton(f"🔗 {n}", callback_data=f'getlink_f_{c}')] for c, n in FREE_CHANNELS.items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data='main_menu')])
        await q.edit_message_text("🆓 **Available Free Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        
    elif data == 'show_paid':
        kb = [[InlineKeyboardButton(f"💎 {n}", callback_data=f'sel_p_{c}')] for c, n in PAID_CHANNELS.items()]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data='main_menu')])
        await q.edit_message_text("💎 **Premium Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    # Free Link Generation (One-Time)
    elif data.startswith('getlink_f_'):
        cid = int(data.split('_')[2])
        try:
            # Create Member Limit 1 link
            link_obj = await context.bot.create_chat_invite_link(chat_id=cid, member_limit=1, name=f"User-{uid}")
            await q.answer("Link Generated!")
            await context.bot.send_message(uid, f"🔗 **Your Unique Link:**\n{link_obj.invite_link}\n\n(Expires after 1 use)", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await q.answer("Error generating link. Bot might not be admin.", show_alert=True)
            logger.error(f"Link Gen Error: {e}")

    # Paid Batch Selection
    elif data.startswith('sel_p_'):
        cid = int(data.split('_')[2])
        kb = [
            [InlineKeyboardButton("🕒 3hr Demo", callback_data=f'joindemo_{cid}')],
            [InlineKeyboardButton("♾️ Permanent", callback_data=f'joinperm_{cid}')],
            [InlineKeyboardButton("🔙 Back", callback_data='show_paid')]
        ]
        await q.edit_message_text(f"💎 **Select Access Type** for {PAID_CHANNELS.get(cid, 'Batch')}:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    # Demo Link (Join Request)
    elif data.startswith('joindemo_'):
        cid = int(data.split('_')[1])
        try:
            # Join Request Link
            link_obj = await context.bot.create_chat_invite_link(chat_id=cid, creates_join_request=True, name=f"Demo-{uid}")
            DEMO_PENDING[uid] = cid # Track that this user wants a demo
            await q.answer("Request Link Generated!")
            
            msg = (
                f"🕒 **Demo Access Link**\n"
                f"{link_obj.invite_link}\n\n"
                f"1. Click Link -> Request to Join.\n"
                f"2. Wait for Admin Approval.\n"
                f"3. Once approved, you have 3 hours."
            )
            await context.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await q.answer("Error. Bot needs Admin rights.", show_alert=True)

    # Permanent Link (Join Request)
    elif data.startswith('joinperm_'):
        cid = int(data.split('_')[1])
        try:
            link_obj = await context.bot.create_chat_invite_link(chat_id=cid, creates_join_request=True, name=f"Perm-{uid}")
            await q.answer("Link Generated!")
            await context.bot.send_message(uid, f"♾️ **Permanent Access Link**\n{link_obj.invite_link}\n\nWait for approval after joining.", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await q.answer("Error.", show_alert=True)

    # --- ADMIN WIZARDS ---
    elif data == 'admin_panel' and is_admin(uid):
        kb = [
            [InlineKeyboardButton("📢 Broadcast", callback_data='wiz_bc'), InlineKeyboardButton("✍️ Post Batches", callback_data='wiz_post')],
            [InlineKeyboardButton("➕ Add Batch", callback_data='wiz_addbatch'), InlineKeyboardButton("➖ Remove Batch", callback_data='wiz_rembatch')],
            [InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')]
        ]
        await q.edit_message_text("👮‍♂️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == 'owner_panel' and is_owner(uid):
        kb = [[InlineKeyboardButton("👥 Users", callback_data='mng_users'), InlineKeyboardButton("⬇️ Backup", callback_data='get_backup')],
              [InlineKeyboardButton("🔙 Back", callback_data='main_menu')]]
        await q.edit_message_text("🔑 **Owner Panel**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == 'main_menu':
        await q.delete_message()
        await start_command(update, context)

    # Add Batch Wizard
    elif data == 'wiz_addbatch':
        await add_batch_command(update, context)
        await q.answer()
    
    elif data.startswith('type_'):
        b_type = data.split('_')[1] # free or paid
        context.user_data['batch_wiz'] = {'type': b_type}
        context.user_data['step'] = 'batch_name'
        await q.edit_message_text(f"Selected: {b_type.upper()}\n\n➡️ **Send the Name** for this batch:")

    # Remove Batch
    elif data == 'wiz_rembatch':
        await remove_batch_command(update, context)
        await q.answer()
    
    elif data.startswith('delbatch_'):
        # delbatch_f_123
        parts = data.split('_')
        b_type, cid = parts[1], int(parts[2])
        if b_type == 'f' and cid in FREE_CHANNELS:
            del FREE_CHANNELS[cid]
        elif b_type == 'p' and cid in PAID_CHANNELS:
            del PAID_CHANNELS[cid]
        save_data()
        await q.answer("Batch Removed")
        await remove_batch_command(update, context) # Refresh list

    # Broadcast/Post Wizards
    elif data == 'wiz_bc':
        context.user_data['step'] = 'broadcast'
        await q.edit_message_text("📢 **Broadcast Mode**\nSend the message you want to broadcast (Text, Photo, etc).")
    
    elif data == 'wiz_post':
        context.user_data['step'] = 'post_batch'
        await q.edit_message_text("✍️ **Batch Post Mode**\nSend the message to post in ALL batches.")

    elif data == 'get_backup' and is_owner(uid):
        if os.path.exists(DATA_FILE):
            await context.bot.send_document(uid, document=open(DATA_FILE, 'rb'), caption="📦 Backup")
        else: await q.answer("No Data.", show_alert=True)

    await q.answer()

# --- MESSAGE HANDLERS ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not user: return
    
    # 1. Admin Wizard Inputs
    if 'step' in context.user_data:
        step = context.user_data['step']
        text = update.message.text
        
        if step == 'batch_name':
            if not text: return
            context.user_data['batch_wiz']['name'] = text
            context.user_data['step'] = 'batch_id'
            await update.message.reply_text(f"Name set to: {text}\n\n➡️ **Now Send the Channel ID** (e.g. -100123...):")
            return
            
        elif step == 'batch_id':
            try:
                cid = int(text)
                wiz = context.user_data['batch_wiz']
                if wiz['type'] == 'free':
                    FREE_CHANNELS[cid] = wiz['name']
                else:
                    PAID_CHANNELS[cid] = wiz['name']
                save_data()
                del context.user_data['step']
                await update.message.reply_text(f"✅ Batch Added Successfully!\n\n**Note:** Make sure to add me as Admin in that channel so I can generate links.")
            except ValueError:
                await update.message.reply_text("❌ Invalid ID. It must be an integer starting with -100.")
            return

        elif step == 'broadcast':
            msg = await update.message.reply_text("⏳ Sending...")
            count = 0
            for uid in list(USER_DATA.keys()):
                try:
                    await context.bot.copy_message(uid, chat.id, update.message.id)
                    count += 1
                except: pass
            del context.user_data['step']
            await context.bot.edit_message_text(chat_id=chat.id, message_id=msg.message_id, text=f"✅ Broadcast Sent to {count} users.")
            return

        elif step == 'post_batch':
            msg = await update.message.reply_text("⏳ Posting...")
            count = 0
            all_cids = list(FREE_CHANNELS.keys()) + list(PAID_CHANNELS.keys())
            for cid in all_cids:
                try:
                    await context.bot.copy_message(cid, chat.id, update.message.id)
                    count += 1
                except: pass
            del context.user_data['step']
            await context.bot.edit_message_text(chat_id=chat.id, message_id=msg.message_id, text=f"✅ Posted in {count} batches.")
            return

    # 2. Support System (Private -> Topic)
    if chat.type == ChatType.PRIVATE:
        topic_id = await get_or_create_user_topic(user, context)
        if topic_id:
            try:
                fwd = await context.bot.forward_message(SUPPORT_GROUP_ID, user.id, update.message.id, message_thread_id=topic_id)
                # Sync: Map (TopicID, FwdMsgID) -> (UserID, OrigMsgID)
                # But for deletion sync, we usually need to delete the User's message? 
                # Telegram Bots CANNOT delete user messages in private chats.
                # Requirement: "If Admin deletes... corresponding message in User's chat must be deleted."
                # This only works for messages SENT BY THE BOT. 
                # So we can only sync deletion of ADMIN REPLIES.
                pass
            except Exception as e: logger.error(f"Fwd error: {e}")

    # 3. Support System (Topic -> Private)
    elif chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        topic_id = update.message.message_thread_id
        
        # Admin Deletion Command
        if update.message.text and update.message.text.strip() == "/delete" and update.message.reply_to_message:
            # Admin wants to delete a message sent to the user
            reply_msg_id = update.message.reply_to_message.message_id
            # Find the user message ID associated with this topic message
            key = (SUPPORT_GROUP_ID, reply_msg_id)
            if key in MESSAGE_MAP:
                target_chat, target_msg = MESSAGE_MAP[key]
                try:
                    await context.bot.delete_message(target_chat, target_msg)
                    await update.message.reply_text("✅ Deleted for user.")
                except Exception as e:
                    await update.message.reply_text(f"❌ Could not delete: {e}")
            return

        # Normal Reply
        target_uid = None
        for uid, tid in USER_TOPICS.items():
            if tid == topic_id: target_uid = uid; break
        
        if target_uid:
            try:
                sent_msg = await context.bot.copy_message(target_uid, chat.id, update.message.id)
                # Store mapping for Deletion Sync and Reaction Sync
                # Map (TopicMsgID) -> (UserChatID, UserMsgID)
                MESSAGE_MAP[(chat.id, update.message.id)] = (target_uid, sent_msg.message_id)
                # Reverse map for Reaction Sync (User reacts to sent_msg -> Show on TopicMsg)
                MESSAGE_MAP[(target_uid, sent_msg.message_id)] = (chat.id, update.message.id)
            except Exception as e:
                await update.message.reply_text(f"❌ Send Failed: {e}")

# --- SYNC HANDLERS ---
async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Syncs reactions between User Private Chat and Admin Topic"""
    if not update.message_reaction: return
    
    chat_id = update.message_reaction.chat.id
    msg_id = update.message_reaction.message_id
    new_reaction = update.message_reaction.new_reaction
    
    # Check if this message is in our map
    if (chat_id, msg_id) in MESSAGE_MAP:
        target_chat, target_msg = MESSAGE_MAP[(chat_id, msg_id)]
        try:
            # Apply reaction to target
            await context.bot.set_message_reaction(chat_id=target_chat, message_id=target_msg, reaction=new_reaction)
        except Exception: pass

# --- BATCH ACCESS LOGIC (DEMO TIMER) ---
async def demo_expired(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data.get('user_id')
    chat_id = job.data.get('chat_id')
    
    # Notify Admin
    if LOG_CHANNEL_ID:
        await context.bot.send_message(LOG_CHANNEL_ID, f"⏰ **Demo Expired**\nUser: {user_id}\nChat: {chat_id}\n\nPlease kick them manually or use /ban.")
    
    # Notify User
    try:
        await context.bot.send_message(user_id, "⏰ **Demo Time Over**\nYour 3-hour trial has ended. Please join the Permanent batch to continue.")
    except: pass
    
    # Try to kick (Ban then Unban)
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.unban_chat_member(chat_id, user_id)
    except Exception as e:
        logger.error(f"Failed to auto-kick demo user: {e}")

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles Pending Join Requests"""
    req = update.chat_join_request
    # We could auto-approve here if we wanted to fully automate
    # But for now we just log or let admins click 'Approve' in Telegram
    pass

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tracks when user actually enters the chat (after approval)"""
    res = update.chat_member
    if not res: return
    
    user = res.from_user
    chat = res.chat
    
    # 1. Mandatory Channel Check (Auto Kick if they left)
    if chat.id == MANDATORY_CHANNEL_ID:
        if res.new_chat_member.status in [ChatMember.LEFT, ChatMember.BANNED]:
            # User left mandatory channel -> Kick from all Free batches
            for cid in FREE_CHANNELS:
                try:
                    await context.bot.ban_chat_member(cid, user.id)
                    await context.bot.unban_chat_member(cid, user.id)
                except: pass
            if LOG_CHANNEL_ID: await context.bot.send_message(LOG_CHANNEL_ID, f"🚫 **Auto-Kick**: {user.full_name} left mandatory channel.")
    
    # 2. Paid Batch Entry (Demo Check)
    if res.new_chat_member.status == ChatMember.MEMBER:
        # Check if they were pending demo
        if user.id in DEMO_PENDING and DEMO_PENDING[user.id] == chat.id:
            # Start 3 Hour Timer
            context.job_queue.run_once(demo_expired, 10800, data={'user_id': user.id, 'chat_id': chat.id})
            del DEMO_PENDING[user.id]
            try:
                await context.bot.send_message(user.id, f"✅ **Welcome to {chat.title}!**\nYour 3-hour demo starts now.")
            except: pass
        
        # General Welcome for Permanent/Paid
        elif chat.id in PAID_CHANNELS:
             try:
                await context.bot.send_message(user.id, f"✅ **Welcome to {chat.title}!**\nMembership Active.")
             except: pass


# --- MAIN SETUP ---

def post_init(app: Application):
    """Sets commands"""
    # Standard User Commands
    app.bot.set_my_commands([
        ("start", "Main Menu"),
        ("batch", "My Batches"),
        ("help", "Help")
    ])

def main():
    load_data()
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("batch", batch_command))
    
    # Admin
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("removeadmin", remove_admin_command))
    app.add_handler(CommandHandler("addbatch", add_batch_command))
    app.add_handler(CommandHandler("removebatch", remove_batch_command))
    
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(ChatMemberHandler(handle_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageReactionHandler(reaction_handler))
    
    # Message Handler (Must be last)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    
    print("Bot Upgraded & Running...")
    app.run_polling()

if __name__ == '__main__':
    main()
