======================================
TELEGRAM AUTO-APPROVER BOT - @Imtudu_accept2bot
======================================

This bot automatically approves join requests for Telegram channels/groups and provides
admin tools for broadcasting messages and tracking statistics.

----- QUICK SETUP -----

1. REQUIREMENTS:
   - Python 3.8 or higher
   - MongoDB database
   - Telegram Bot Token, API ID, and API Hash

2. CONFIGURATION:
   Create a config.py file with:
   - BOT_TOKEN = "your_bot_token"
   - API_ID = your_api_id
   - API_HASH = "your_api_hash"
   - MONGODB_URI = "your_mongodb_connection_string"
   - DB_NAME = "your_database_name"
   - CHANNEL_LINK = "https://t.me/your_channel"
   - ADMIN_IDS = [your_user_id, another_admin_id]

3. INSTALLATION:
   - Install dependencies: pip install -r requirements.txt
   - Run the bot: python app.py

4. DOCKER SETUP:
   - Build image: docker build -t telegram-autoapprover .
   - Run container: docker run -d --name telegram-bot telegram-autoapprover

----- FEATURES -----

- Auto-approves join requests for channels and groups
- Admin commands: /stats, /broadcast, /fetch_users, /setwelcome
- Customizable welcome messages
- Broadcasting to all users (with media support)
- Statistics tracking

----- BOT SETUP -----

1. Start a chat with @Imtudu_accept2bot (https://t.me/Imtudu_accept2bot)
2. Add your bot to your channel/group as an admin
3. Give it "Add Members" permission
4. Enable join requests in channel/group settings

See README.md for more detailed instructions and explanations. 