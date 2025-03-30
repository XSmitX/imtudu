import asyncio
import logging
import os
from datetime import datetime
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, DuplicateKeyError
from pyrogram import Client, filters, idle
from pyrogram.types import ChatJoinRequest, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, UserDeactivated, PeerIdInvalid, ChatAdminRequired
from config import BOT_TOKEN, API_ID, API_HASH, MONGODB_URI, DB_NAME, CHANNEL_LINK, ADMIN_IDS
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot configuration

# MongoDB setup
try:
    # Connect to MongoDB
    db_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    db_client.admin.command('ping')  # Test connection
    logger.info("Successfully connected to MongoDB")
    
    # Setup collections
    db = db_client[DB_NAME]
    users_collection = db["users"]
    channels_collection = db["channels"]
    stats_collection = db["statistics"]
    requests_queue = db["requests_queue"]
    settings_collection = db["settings"]  # For storing bot settings like welcome message
    
    # Create indexes
    users_collection.create_index("user_id", unique=True)
    requests_queue.create_index("timestamp")
    requests_queue.create_index([("processed", 1), ("timestamp", 1)])
    channels_collection.create_index("channel_id", unique=True)
except (ConnectionFailure, ServerSelectionTimeoutError) as e:
    logger.error(f"Could not connect to MongoDB: {e}")
    db_client = None
    db = None
    users_collection = None
    channels_collection = None
    stats_collection = None
    requests_queue = None
    settings_collection = None

# Initialize the Pyrogram Client
app = Client(
    "join_request_approver5",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Rate limiting tracker
rate_limit_tracker = {}

# Helper functions
async def save_user(user_id, first_name, username=None):
    """Save user to MongoDB with duplicate prevention"""
    if users_collection is None:
        return False
        
    try:
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "name": first_name,
                "username": username,
                "last_activity": datetime.utcnow()
            }},
            upsert=True
        )
        logger.info(f"User saved/updated: {first_name} (ID: {user_id})")
        return True
    except Exception as e:
        logger.error(f"Failed to save user: {e}")
        return False

async def fetch_all_users():
    """Fetch all users from the database"""
    if users_collection is None:
        return []
        
    try:
        users = list(users_collection.find({}, {"user_id": 1, "name": 1, "username": 1}))
        logger.info(f"Fetched {len(users)} users from database")
        return users
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}")
        return []

async def is_user_admin(user_id):
    """Check if the user is an admin"""
    # Only check if user ID is in ADMIN_IDS list
    if user_id in ADMIN_IDS:
        return True
    return False

async def update_channel_in_db(channel_id):
    """Add or update channel information in database"""
    if channels_collection is None:
        return
        
    try:
        channels_collection.update_one(
            {"channel_id": channel_id},
            {"$setOnInsert": {
                "added_at": datetime.utcnow(),
                "total_approved": 0,
                "active": True
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Failed to update channel in database: {e}")

async def update_stats(chat_id, user_id, approved=False, error=None):
    """Update statistics for analytics"""
    if stats_collection is None or channels_collection is None:
        return
        
    try:
        stats_collection.insert_one({
            "timestamp": datetime.utcnow(),
            "chat_id": chat_id,
            "user_id": user_id,
            "approved": approved,
            "error": error
        })
        
        if approved:
            # Increment the approved count for the channel
            channels_collection.update_one(
                {"channel_id": chat_id},
                {"$inc": {"total_approved": 1}}
            )
    except Exception as e:
        logger.error(f"Failed to update statistics: {e}")

async def add_request_to_queue(client, join_request):
    """Add join request to processing queue in MongoDB"""
    if requests_queue is None:
        return
        
    user = join_request.from_user
    chat = join_request.chat
    
    try:
        requests_queue.insert_one({
            "timestamp": datetime.utcnow(),
            "chat_id": chat.id,
            "user_id": user.id,
            "user_first_name": user.first_name,
            "chat_title": chat.title,
            "processed": False,
            "retries": 0
        })
        logger.info(f"Added join request from {user.first_name} (ID: {user.id}) to queue for {chat.title}")
    except Exception as e:
        logger.error(f"Failed to add request to queue: {e}")
        # Even if we fail to queue, try to approve directly as fallback
        try:
            await approve_single_request(client, chat.id, user.id, user.first_name, chat.title)
        except Exception as inner_e:
            logger.error(f"Fallback approval failed: {inner_e}")

async def process_queue():
    """Process the queue of join requests"""
    if requests_queue is None:
        return
        
    while True:
        try:
            # Find unprocessed requests
            requests = list(requests_queue.find(
                {"processed": False}
            ).sort("timestamp", 1).limit(10))  # Process 10 at a time
            
            if not requests:
                # No pending requests, sleep briefly
                await asyncio.sleep(1)
                continue
                
            logger.info(f"Processing batch of {len(requests)} join requests")
            
            for request in requests:
                try:
                    # Process the request
                    await approve_single_request(
                        app, 
                        request["chat_id"], 
                        request["user_id"],
                        request["user_first_name"],
                        request["chat_title"]
                    )
                    
                    # Mark as processed
                    requests_queue.update_one(
                        {"_id": request["_id"]},
                        {"$set": {"processed": True, "approved_at": datetime.utcnow()}}
                    )
                    
                except Exception as e:
                    # Update retry counter
                    retry_count = request.get("retries", 0) + 1
                    
                    # If we've tried too many times, mark as failed
                    if retry_count >= 3:
                        requests_queue.update_one(
                            {"_id": request["_id"]},
                            {
                                "$set": {
                                    "processed": True, 
                                    "error": str(e),
                                    "failed": True
                                }
                            }
                        )
                        logger.error(f"Failed to approve request after 3 retries: {e}")
                    else:
                        # Increment retry counter
                        requests_queue.update_one(
                            {"_id": request["_id"]},
                            {"$inc": {"retries": 1}}
                        )
            
            # Cooldown between batch processing to avoid rate limits
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Error processing queue: {e}")
            await asyncio.sleep(5)  # Wait a bit longer on error

async def approve_single_request(client, chat_id, user_id, user_first_name, chat_title):
    """Approve a single join request with rate limit handling"""
    chat_key = f"chat_{chat_id}"
    
    try:
        # Approve the join request
        await client.approve_chat_join_request(
            chat_id=chat_id,
            user_id=user_id
        )
        logger.info(f"Approved join request for {user_first_name} (ID: {user_id}) in {chat_title}")
        await client.send_photo(
            chat_id=user_id,
            photo="https://ibb.co/8nRJ3Ndp",
            caption=f'''<b>1000+ Games Available On https://bit.ly/Imtudu100exch1

Cricket, Tennis, Football, TeenPatti, Aviator, Mines, Dragon Tiger, Andar Bahar ETC - 1000+ Games 🤑

Minimum Id & Bet 100 Rs...

Sabse Badi Baat Yaha 0% Tax Hai Winning Pe India Ka Eklauta Betting Site Jaha 0% Tax 😍

Casino Me Aisa Games Hote Jaha Aap 100 Rs Se Lakho Jeet Sakte Ho 🔥

Official Telegram Link👇
https://t.me/+dWexXsRpmhkyYmZl

𝗠𝗔𝗞𝗘 𝗬𝗢𝗨𝗥 𝗜𝗗 𝗡𝗢𝗪 👇
➡️ https://bit.ly/Imtudu100exch1
➡️ https://bit.ly/Imtudu100exch1</b>'''
        )
        # Send approval notification to the user
        await client.send_message(
            chat_id=user_id,
            text=f'''<b>👋 Hello {user_first_name}!
🔺 Your Request To Join {chat_title} Has Been Approved 🔻
✅ Send /start For Dream 11 Free Entry & ₹1000 Paytm 💵 Free Cash🎁</b>'''
        )
        
        # Update stats
        await update_stats(chat_id, user_id, approved=True)
        
        # Save user to database
        await save_user(user_id, user_first_name)
        
        return True
        
    except FloodWait as e:
        # Handle rate limiting
        logger.warning(f"Rate limited for {e.value} seconds on chat {chat_id}")
        await asyncio.sleep(e.value)
        
        # Try again after waiting
        await client.approve_chat_join_request(
            chat_id=chat_id,
            user_id=user_id
        )
        logger.info(f"Approved join request for {user_first_name} after waiting")
        await update_stats(chat_id, user_id, approved=True)
        await save_user(user_id, user_first_name)
        return True
        
    except (UserDeactivated, PeerIdInvalid) as e:
        # User no longer exists or has deactivated their account
        logger.warning(f"Cannot approve request for {user_first_name}: {e}")
        await update_stats(chat_id, user_id, error=str(e))
        return False
        
    except ChatAdminRequired as e:
        # Bot is not admin or lacks permissions
        logger.error(f"Bot lacks permission to approve requests in chat {chat_id}: {e}")
        await update_stats(chat_id, user_id, error=str(e))
        return False
        
    except Exception as e:
        logger.error(f"Failed to approve join request: {e}")
        await update_stats(chat_id, user_id, error=str(e))
        return False

# Event Handlers
@app.on_chat_join_request()
async def handle_join_request(client, join_request):
    """Handler for incoming join requests"""
    # Queue the request for processing
    await add_request_to_queue(client, join_request)

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    """Handler for the /start command"""
    user = message.from_user
    user_id = user.id
    
    # Save the user to database
    await save_user(user_id, user.first_name, user.username)
    
    # Check if the user is an admin
    is_admin = await is_user_admin(user_id)
    
    if is_admin:
        # Admin welcome message with buttons
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Add me to channel", url=f"https://t.me/{app.me.username}?startchannel=true")],
            [InlineKeyboardButton("Add me to group", url=f"https://t.me/{app.me.username}?startgroup=true")]
        ])
        
        await message.reply(
            "Welcome, Admin! 👨‍💼\n\nYou can manage the bot with the commands below:\n"
            "/stats - View detailed stats\n"
            "/broadcast - Send message to all users\n"
            "/fetch_users - Get all users info\n"
            "/setwelcome - Set custom welcome message for users",
            reply_markup=keyboard
        )
    else:
        # Regular user welcome message with channel link
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Join Our Channel", url=CHANNEL_LINK)]
        ])
        
        # Check if custom welcome message exists
        custom_welcome = None
        if settings_collection is not None:
            setting = settings_collection.find_one({"setting_name": "welcome_message"})
            if setting and "message_id" in setting:
                try:
                    # Get the stored welcome message and copy it
                    custom_welcome = await client.get_messages(
                        chat_id=setting["chat_id"],
                        message_ids=setting["message_id"]
                    )
                    if custom_welcome:
                        # Send with the keyboard added
                        await custom_welcome.copy(
                            chat_id=message.chat.id,
                            reply_markup=keyboard
                        )
                        return
                except Exception as e:
                    logger.error(f"Error sending custom welcome: {e}")
        
        # Default welcome if no custom welcome is set
        await message.reply(
            "Welcome to imTUDU bot (@Imtudu_accept2bot).\n\n"
            "Click the button below to join our channel:",
            reply_markup=keyboard
        )

@app.on_message(filters.command("setwelcome") & filters.private)
async def set_welcome_command(client, message):
    """Handler for setting a custom welcome message"""
    if settings_collection is None:
        await message.reply("Database not connected. Please try again later.")
        return
        
    user_id = message.from_user.id
    
    # Check if user is admin
    is_admin = await is_user_admin(user_id)
    if not is_admin:
        await message.reply("Sorry, this command is only available to admins.")
        return
    
    # Check if it's a reply to another message
    if message.reply_to_message:
        # Save the replied message details for future use
        settings_collection.update_one(
            {"setting_name": "welcome_message"},
            {"$set": {
                "chat_id": message.chat.id,
                "message_id": message.reply_to_message.id,
                "updated_at": datetime.utcnow(),
                "updated_by": user_id
            }},
            upsert=True
        )
        
        await message.reply("✅ Welcome message updated successfully. Users will now see this message when they start the bot.")
    else:
        # Two-step process - Request the welcome message
        await message.reply(
            "Please send or forward the message you want to set as the welcome message for new users.\n\n"
            "You can send text, images, videos, or any other content.\n"
            "To cancel, send /cancel\n\n"
            "TIP: You can also reply to any message with /setwelcome to set it as the welcome message."
        )
        
        # Set the user's next step to receive welcome content
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"waiting_for_welcome": True}}
        )

@app.on_message(filters.private & ~filters.command(["cancel", "start", "stats", "broadcast", "fetch_users", "setwelcome"]))
async def receive_message_content(client, message):
    """Receive broadcast or welcome message content from admin"""
    if users_collection is None:
        return
        
    user_id = message.from_user.id
    
    # Get user data
    user = users_collection.find_one({"user_id": user_id})
    if not user:
        return
        
    # Check if waiting for broadcast
    if user.get("waiting_for_broadcast", False):
        # Reset the waiting flag
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"waiting_for_broadcast": False}}
        )
        
        await handle_broadcast_message(client, message)
        return
        
    # Check if waiting for welcome message
    if user.get("waiting_for_welcome", False):
        # Reset the waiting flag
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"waiting_for_welcome": False}}
        )
        
        # Save the message details for future welcome messages
        if settings_collection is not None:
            settings_collection.update_one(
                {"setting_name": "welcome_message"},
                {"$set": {
                    "chat_id": message.chat.id,
                    "message_id": message.id,
                    "updated_at": datetime.utcnow(),
                    "updated_by": user_id
                }},
                upsert=True
            )
            
            await message.reply("✅ Welcome message updated successfully. Users will now see this message when they start the bot.")
        else:
            await message.reply("Error: Could not save welcome message. Database not connected.")

async def handle_broadcast_message(client, message):
    """Handle broadcasting a message to all users"""
    # Confirm broadcast
    status_message = await message.reply("Preparing to broadcast... Fetching users...")
    
    # Get all users
    users = await fetch_all_users()
    total_users = len(users)
    
    await status_message.edit_text(f"Starting broadcast to {total_users} users...")
    
    # Counter for successful/failed sends
    successful = 0
    failed = 0
    
    # Start time for calculating speed
    start_time = datetime.utcnow()
    
    # Process users in chunks to avoid overloading
    chunk_size = 20
    for i in range(0, total_users, chunk_size):
        chunk = users[i:i+chunk_size]
        
        for user in chunk:
            # Process each user
            result = await copy_message_to_user(client, user["user_id"], message)
            if result:
                successful += 1
            else:
                failed += 1
            
            # Update status occasionally
            if (successful + failed) % 10 == 0:
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                speed = (successful + failed) / elapsed if elapsed > 0 else 0
                try:
                    await status_message.edit_text(
                        f"Broadcasting...\n"
                        f"Processed: {successful+failed}/{total_users}\n"
                        f"Successful: {successful}\n"
                        f"Failed: {failed}\n"
                        f"Speed: {speed:.1f} users/sec"
                    )
                except Exception:
                    pass
    
    # Calculate elapsed time and speed
    elapsed = (datetime.utcnow() - start_time).total_seconds()
    speed = total_users / elapsed if elapsed > 0 else 0
    
    # Send final report
    await status_message.edit_text(
        f"✅ Broadcast completed!\n\n"
        f"Total users: {total_users}\n"
        f"Successful: {successful}\n"
        f"Failed: {failed}\n"
        f"Time taken: {elapsed:.1f} seconds\n"
        f"Average speed: {speed:.1f} users/sec"
    )

@app.on_message(filters.command("stats") & filters.private)
async def stats_command(client, message):
    """Handler for detailed stats command"""
    if users_collection is None or stats_collection is None:
        await message.reply("Database not connected. Please try again later.")
        return
        
    user_id = message.from_user.id
    
    # Check if user is admin
    is_admin = await is_user_admin(user_id)
    if not is_admin:
        await message.reply("Sorry, this command is only available to admins.")
        return
    
    try:
        # Get today's stats
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_approved = stats_collection.count_documents({
            "timestamp": {"$gte": today},
            "approved": True
        })
        
        # Get total stats for last 7 days
        week_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = week_ago.replace(day=week_ago.day-7)
        week_approved = stats_collection.count_documents({
            "timestamp": {"$gte": week_ago},
            "approved": True
        })
        
        # Get new users today
        today_users = users_collection.count_documents({
            "last_activity": {"$gte": today}
        })
        
        # Get error counts
        total_errors = stats_collection.count_documents({"error": {"$ne": None}})
        
        await message.reply(
            "📊 **Detailed Statistics**\n\n"
            f"**New/Active users today:** {today_users}\n"
            f"**Approved today:** {today_approved}\n"
            f"**Approved last 7 days:** {week_approved}\n"
            f"**Total errors:** {total_errors}"
        )
    except Exception as e:
        logger.error(f"Error generating detailed stats: {e}")
        await message.reply("Error generating statistics. Check logs for details.")

@app.on_message(filters.command("fetch_users") & filters.private)
async def fetch_users_command(client, message):
    """Handler for fetch_users command"""
    if users_collection is None:
        await message.reply("Database not connected. Please try again later.")
        return
        
    user_id = message.from_user.id
    
    # Check if user is admin
    is_admin = await is_user_admin(user_id)
    if not is_admin:
        await message.reply("Sorry, this command is only available to admins.")
        return
    
    try:
        # Fetch users from database
        users = await fetch_all_users()
        
        if not users:
            await message.reply("No users found in database.")
            return
            
        # Format user data and send as a text file
        user_data = "user_id,name,username\n"
        for user in users:
            username = user.get("username", "None")
            user_data += f"{user['user_id']},{user['name']},{username}\n"
            
        # Send as a text file
        with open("users.csv", "w", encoding="utf-8") as f:
            f.write(user_data)
            
        await message.reply_document("users.csv", caption=f"Total users: {len(users)}")
        
        # Delete the file after sending
        os.remove("users.csv")
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        await message.reply("Error fetching users. Check logs for details.")

@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message):
    """Handler for broadcast command"""
    if users_collection is None:
        await message.reply("Database not connected. Please try again later.")
        return
        
    user_id = message.from_user.id
    
    # Check if user is admin
    is_admin = await is_user_admin(user_id)
    if not is_admin:
        await message.reply("Sorry, this command is only available to admins.")
        return
    
    # Check if it's a reply to another message
    if message.reply_to_message:
        # Get the replied message to broadcast
        reply_msg = message.reply_to_message
        
        # Confirm broadcast
        status_message = await message.reply("Preparing to broadcast the replied content... Fetching users...")
        
        # Get all users
        users = await fetch_all_users()
        total_users = len(users)
        
        await status_message.edit_text(f"Starting broadcast to {total_users} users...")
        
        # Counter for successful/failed sends
        successful = 0
        failed = 0
        
        # Start time for calculating speed
        start_time = datetime.utcnow()
        
        # Process users in chunks to avoid overloading
        chunk_size = 20
        for i in range(0, total_users, chunk_size):
            chunk = users[i:i+chunk_size]
            
            for user in chunk:
                # Process each user
                result = await copy_message_to_user(client, user["user_id"], reply_msg)
                if result:
                    successful += 1
                else:
                    failed += 1
                
                # Update status occasionally
                if (successful + failed) % 10 == 0:
                    elapsed = (datetime.utcnow() - start_time).total_seconds()
                    speed = (successful + failed) / elapsed if elapsed > 0 else 0
                    try:
                        await status_message.edit_text(
                            f"Broadcasting...\n"
                            f"Processed: {successful+failed}/{total_users}\n"
                            f"Successful: {successful}\n"
                            f"Failed: {failed}\n"
                            f"Speed: {speed:.1f} users/sec"
                        )
                    except Exception:
                        pass
        
        # Calculate elapsed time and speed
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        speed = total_users / elapsed if elapsed > 0 else 0
        
        # Send final report
        await status_message.edit_text(
            f"✅ Broadcast completed!\n\n"
            f"Total users: {total_users}\n"
            f"Successful: {successful}\n"
            f"Failed: {failed}\n"
            f"Time taken: {elapsed:.1f} seconds\n"
            f"Average speed: {speed:.1f} users/sec"
        )
    else:
        # Two-step process - Request the broadcast message from the admin
        await message.reply(
            "Please send the message you want to broadcast to all users.\n\n"
            "You can send text, images, videos, or any other content.\n"
            "To cancel, send /cancel\n\n"
            "TIP: You can also reply to any message with /broadcast to directly broadcast that content."
        )
        
        # Set the user's next step to receive broadcast content
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"waiting_for_broadcast": True}}
        )

async def copy_message_to_user(client, user_id, original_message):
    """Copy the message to user without any parsing"""
    try:
        # Use copy to maintain exact same format and content
        await original_message.copy(
            chat_id=user_id
        )
        return True
    except FloodWait as e:
        # Handle rate limiting
        await asyncio.sleep(e.value)
        return await copy_message_to_user(client, user_id, original_message)
    except Exception as e:
        logger.error(f"Error copying broadcast to user {user_id}: {e}")
        return False

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client, message):
    """Handler for cancel command"""
    if users_collection is None:
        await message.reply("Database not connected. Please try again later.")
        return
        
    user_id = message.from_user.id
    
    # Check if waiting for broadcast
    user = users_collection.find_one({"user_id": user_id})
    waiting = user and user.get("waiting_for_broadcast", False)
    
    if waiting:
        # Reset the waiting flag
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"waiting_for_broadcast": False}}
        )
        
        await message.reply("Broadcast cancelled.")
    else:
        await message.reply("Nothing to cancel.")

def handle_startup():
    """Function to handle bot startup tasks"""
    # Get bot info and log startup
    bot_info = app.get_me()
    logger.info(f"Bot started! Username: @{bot_info.username}")
    
    # Set bot commands
    app.set_bot_commands([
        {"command": "start", "description": "Start the bot"},
        {"command": "stats", "description": "View detailed statistics"},
        {"command": "fetch_users", "description": "Get all users data"},
        {"command": "broadcast", "description": "Send message to all users"},
        {"command": "cancel", "description": "Cancel current operation"}
    ])
    
    # Start the queue processor as a background task
    app.loop.create_task(process_queue())

# Run the bot
if __name__ == "__main__":
    try:
        logger.info("Starting bot...")
        
        # Start the bot
        app.start()
        
        # Get basic info
        me = app.get_me()
        logger.info(f"Bot started successfully: @{me.username}")
        
        # Start background tasks
        task = app.loop.create_task(process_queue())
        
        # Keep it running
        app.loop.run_forever()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user!")
    except Exception as e:
        logger.error(f"Critical error: {e}")
    finally:
        # Stop the app if it's running
        if app.is_connected:
            app.stop()
            
        # Close MongoDB connection
        if 'db_client' in globals() and db_client:
            db_client.close()
            
        logger.info("Bot resources released!")
