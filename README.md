# Telegram Auto-Approver Bot - @Imtudu_accept2bot

A Telegram bot that automatically approves join requests for channels and groups, built with Pyrogram and MongoDB.

## Features

- Auto-approve join requests for channels and groups
- Send welcome messages to approved users
- Admin commands for managing the bot
- Customizable welcome messages
- Broadcasting messages to all users
- Detailed statistics tracking

## Setup Instructions

### Prerequisites

- Python 3.8+
- MongoDB database
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- API ID and API Hash from [Telegram API Development Tools](https://my.telegram.org/apps)

### Configuration

Create a `config.py` file with the following content:

```python
# Bot credentials
BOT_TOKEN = "your_bot_token_from_botfather"  # Get for @Imtudu_accept2bot
API_ID = 12345  # Your API ID from my.telegram.org
API_HASH = "your_api_hash_from_my_telegram_org"

# MongoDB connection
MONGODB_URI = "mongodb://username:password@host:port/dbname"
DB_NAME = "telegram_bot_db"

# Other settings
CHANNEL_LINK = "https://t.me/your_channel"  # Your channel link for users to join
ADMIN_IDS = [123456789]  # List of admin user IDs (these users can access admin commands)
```

### Installation Options

#### Option 1: Direct Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/telegram-autoapprover.git
   cd telegram-autoapprover
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Run the bot:
   ```
   python app.py
   ```

#### Option 2: Using Docker

1. Build the Docker image:
   ```
   docker build -t telegram-autoapprover .
   ```

2. Run the container:
   ```
   docker run -d --name telegram-bot telegram-autoapprover
   ```

## Usage

### Setup Your Bot

1. Start a chat with [@Imtudu_accept2bot](https://t.me/Imtudu_accept2bot) or use your own bot
2. Add your bot to the target channels or groups
3. Make your bot an admin with "Add Members" permission
4. Enable join requests in your channel/group settings

### Admin Commands

- `/start` - Welcome message and command menu
- `/stats` - View detailed statistics
- `/broadcast` - Send message to all users
- `/fetch_users` - Get CSV file with all users data
- `/setwelcome` - Set custom welcome message for users
- `/cancel` - Cancel current operation

### Broadcast Feature

There are two ways to broadcast messages:

1. Reply to any message with `/broadcast` to send that exact message to all users
2. Send `/broadcast` first, then send your content as a follow-up message

All broadcasts are sent using `.copy()` to preserve the exact formatting and media.

### Custom Welcome Messages

To set a custom welcome message:

1. Send `/setwelcome` in a private chat with the bot
2. Send the welcome message you want users to see when they start the bot
3. Alternatively, you can reply to an existing message with `/setwelcome`

## Docker Deployment

The included Dockerfile creates a container with everything needed to run the bot:

```
docker build -t telegram-autoapprover .
docker run -d --name telegram-bot telegram-autoapprover
```

For persistent data, consider mounting the MongoDB data directory or using an external MongoDB service.

## License

[MIT License](LICENSE) 