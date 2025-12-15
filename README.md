All-in-One Telegram Bot Manager

A powerful Telegram bot for managing channels, support, and broadcasting. Deploys easily to Render, Heroku, or Koyeb.

Features

Support System: Forwards user messages to a Private Group Topics.

Batch Posting: Post to multiple Free/Paid channels instantly.

Broadcast: Message all bot users.

Force Subscribe: Forces users to join a channel.

Backup/Restore: JSON based database management.

Environment Variables (Required)

Variable

Description

Example

TELEGRAM_BOT_TOKEN

Your Bot Token from @BotFather

12345:AAH...

OWNER_ID

Your Telegram User ID

12345678

ADMIN_IDS

IDs of other admins (comma separated)

12345, 67890

MANDATORY_CHANNEL_ID

ID of channel users MUST join (start with -100)

-100123456789

MANDATORY_CHANNEL_LINK

Invite link for the mandatory channel

https://t.me/MyChannel

SUPPORT_GROUP_ID

Group ID where user msgs are sent as Topics

-100987654321

CONTACT_ADMIN_LINK

Link for direct support button

https://t.me/MySupport

LOG_CHANNEL_ID

Channel ID for logs (bans/errors)

-100555555555

DATA_FILE

Path to save data (auto-handled mostly)

bot_data.json or /data/bot_data.json

Deployment

Render (Recommended)

Create a Worker (or Web Service).

Connect this repo.

Add Persistent Disk at mount path /data (Crucial for saving data).

Set DATA_FILE to /data/bot_data.json.

Add all Env Vars.

Koyeb / Heroku

Deploy as a Web Service.

The bot runs a mini Flask server on $PORT to stay alive.

Set Env Vars.

Commands

/start - Main Menu

/admin - Admin Panel (Broadcast, Post, Manage Batches)

/owner - Owner Panel (Backup, User Mng)

/help - Show commands
