🤖 Advanced Telegram Bot Manager
A powerful, all-in-one Telegram bot solution built with Python (v20+), Flask, and JSON persistence. This bot is designed to manage paid and free communities with smart link generation, automated demo access, and a support ticket system using Forum Topics.
🚀 Key Features
1. 🔐 Smart Access Control
 * One-Time Links (Free Batches): Generates unique invite links for free batches that expire after 1 use. This prevents link sharing.
 * Demo Mode (Paid Batches): Users can request "Demo Access".
   * Automated Timer: Grants a 3-hour trial period.
   * Auto-Kick: Automatically alerts admins and kicks the user when the demo time expires.
 * Permanent Access: Standard join requests for lifetime members.
2. 🎫 Advanced Support System
 * Topic-Based Tickets: Forwards user private messages to a specific Support Group as a Forum Topic.
 * Smart Sync:
   * Reaction Sync: User reactions on messages appear in the Admin Topic, and Admin reactions appear in the User's private chat.
   * Deletion Sync: Admin replies deleted in the Topic are automatically deleted from the User's chat.
3. 👥 Dynamic Management
 * Admin Management: The Owner can add/remove admins on the fly using commands (/addadmin, /removeadmin).
 * Batch Management: Add or remove Free/Paid batches directly via a wizard in the Admin Panel. No code changes required.
4. 📢 Broadcast & Posting
 * Broadcast: Send messages to all bot users.
 * Batch Post: Send updates to all connected Batch Channels simultaneously.
5. 🛡️ Security & Stability
 * Force Subscribe: Users must join a Mandatory Channel to use the bot.
 * Auto-Kick: If a user leaves the Mandatory Channel, they are banned from all Free Batches.
 * Keep-Alive Server: Built-in Flask server to prevent sleeping on cloud platforms like Render/Heroku.
 * JSON Persistence: All data (Admins, Batches, User info) is saved to bot_data.json.
🛠️ Deployment
Prerequisites
 * Python 3.10+
 * Telegram Bot Token (from @BotFather)
 * Telegram User ID (from @userinfobot)
Option A: Deploy on Render / Heroku
 * Fork this repository.
 * Create a new Web Service (or Worker).
 * Connect your repository.
 * Add the Environment Variables (see below).
 * Start Command: python bot.py
Option B: Docker
# Build the image
docker build -t bot-manager .

# Run the container
docker run -d \
  -e TELEGRAM_BOT_TOKEN="your_token" \
  -e OWNER_ID="your_id" \
  ... \
  bot-manager

Option C: Local Run
pip install -r requirements.txt
python bot.py

🔑 Environment Variables
| Variable | Description | Required | Example |
|---|---|---|---|
| TELEGRAM_BOT_TOKEN | Your Bot API Token | Yes | 12345:AAH... |
| OWNER_ID | Your numeric Telegram ID | Yes | 12345678 |
| MANDATORY_CHANNEL_ID | ID of the main channel users must join | Yes | -100123456789 |
| MANDATORY_CHANNEL_LINK | Invite link for the main channel | Yes | https://t.me/MyChannel |
| SUPPORT_GROUP_ID | Group ID (must be a Supergroup with Topics enabled) | Yes | -100987654321 |
| LOG_CHANNEL_ID | Channel for logs (kicks, demos expired) | No | -100555555555 |
| CONTACT_ADMIN_LINK | Username or Link for support button | No | https://t.me/Admin |
| DATA_FILE | Path to save JSON data (Render: /data/bot_data.json) | No | bot_data.json |
> Note: Channel IDs usually start with -100.
> 
🤖 Commands
👑 Owner Commands
 * /owner - Open the Owner Panel (Backup data, Manage Users).
 * /addadmin <id> - Add a new Admin dynamically.
 * /removeadmin <id> - Demote an Admin.
 * /backup - Download the bot_data.json database file.
👮‍♂️ Admin Commands
 * /admin - Open Admin Panel (Add Batches, Broadcast, Post).
 * /addbatch - Start the wizard to add a new Free or Paid batch.
 * /check <id> - Check a user's subscription status.
 * /link <id> - Manually link a Support Topic to a user.
👤 User Commands
 * /start - Main Menu.
 * /batch - View joined batches.
 * /id - View Telegram ID.
⚠️ Important Notes
 * Bot Permissions:
   * The bot MUST be an Admin in:
     * The Mandatory Channel (to check membership).
     * All Batch Channels (to generate invite links & kick users).
     * The Support Group (to manage topics).
 * Support Group:
   * Ensure the Support Group has "Topics" enabled in Group Settings.
 * Data Persistence:
   * On Render, use a Persistent Disk mounted at /data and set DATA_FILE to /data/bot_data.json to prevent data loss on restarts.
📝 Credits
Built with 🇮‌🇹‌'🇸‌ 🇭‌4️⃣🇷‌.
