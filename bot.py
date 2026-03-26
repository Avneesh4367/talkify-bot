import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# Handle aiogram 3.x version differences
try:
    from aiogram.client.default import DefaultBotProperties
    HAS_DEFAULT_PROPERTIES = True
except ImportError:
    HAS_DEFAULT_PROPERTIES = False

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = "8788443709:AAFC3Iys6921Ja6Aoi_VLvsRUXE9jKC1bRY"

# ==========================================
# STATE & DATA STRUCTURES
# ==========================================
user_state = {}           # user_id -> "gender" | "preference" | "chatting"
user_gender = {}          # user_id -> str
user_preference = {}      # user_id -> str
waiting_queue = []        # list of user_id
active_chats = {}         # user_id -> partner_id

# ==========================================
# BOT & DISPATCHER SETUP
# ==========================================
if HAS_DEFAULT_PROPERTIES:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
else:
    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")

dp = Dispatcher()

# ==========================================
# KEYBOARDS
# ==========================================
def get_gender_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Boy"), KeyboardButton(text="Girl")],
            [KeyboardButton(text="Prefer not to say")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_preference_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Boys"), KeyboardButton(text="Girls"), KeyboardButton(text="Everyone")],
            [KeyboardButton(text="Gay"), KeyboardButton(text="Lesbian")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

# ==========================================
# MATCHMAKING LOGIC
# ==========================================
async def try_match():
    # Simple matching system that connects the first two users in the queue
    if len(waiting_queue) >= 2:
        user1 = waiting_queue.pop(0)
        user2 = waiting_queue.pop(0)
        
        active_chats[user1] = user2
        active_chats[user2] = user1
        
        user_state[user1] = "chatting"
        user_state[user2] = "chatting"
        
        try:
            await bot.send_message(user1, "🎉 You are now connected!", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass
            
        try:
            await bot.send_message(user2, "🎉 You are now connected!", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass

# ==========================================
# CORE HANDLERS (Sequential Flow)
# ==========================================

# 1. /start handler
@dp.message(F.text == "/start")
async def start(message: Message):
    user_id = message.from_user.id
    
    # Clean up previous chat if re-starting
    if user_id in active_chats:
        partner_id = active_chats.pop(user_id)
        active_chats.pop(partner_id, None)
        try:
            await bot.send_message(partner_id, "⚠️ Your partner left.", reply_markup=ReplyKeyboardRemove())
            user_state[partner_id] = None
        except Exception:
            pass
            
    if user_id in waiting_queue:
        waiting_queue.remove(user_id)

    user_state[user_id] = "gender"
    await message.answer("👋 Welcome to Talkify!\n\nWhat is your gender?", reply_markup=get_gender_kb())


# 2. onboarding (gender selection)
@dp.message(lambda message: user_state.get(message.from_user.id) == "gender")
async def process_gender(message: Message):
    user_id = message.from_user.id
    text = message.text
    
    if text not in ["Boy", "Girl", "Prefer not to say"]:
        return await message.answer("Please use the buttons provided.", reply_markup=get_gender_kb())
        
    user_gender[user_id] = text
    user_state[user_id] = "preference"
    await message.answer("Who do you want to connect with?", reply_markup=get_preference_kb())


# 3. preference selection
@dp.message(lambda message: user_state.get(message.from_user.id) == "preference")
async def process_preference(message: Message):
    user_id = message.from_user.id
    text = message.text

    if text not in ["Boys", "Girls", "Gay", "Lesbian", "Everyone"]:
        return await message.answer("Please use the buttons provided.", reply_markup=get_preference_kb())
        
    user_preference[user_id] = text
    
    # 4. Matching system addition
    user_state[user_id] = "waiting" # transitional state
    waiting_queue.append(user_id)
    
    await message.answer("✅ Setup complete! Finding a partner...", reply_markup=ReplyKeyboardRemove())
    
    # Trigger matching check
    await try_match()


# 5. chat forwarding
@dp.message(lambda message: user_state.get(message.from_user.id) == "chatting")
async def chat_handler(message: Message):
    user_id = message.from_user.id

    if user_id in active_chats:
        partner = active_chats[user_id]
        
        try:
            await bot.send_message(partner, message.text)
        except Exception:
            # Drop chat on failed delivery (e.g. user blocked bot)
            active_chats.pop(user_id, None)
            active_chats.pop(partner, None)
            user_state[user_id] = None
            user_state[partner] = None
            await message.answer("Message failed. The partner might have left.")


# 6. fallback handler (silent)
@dp.message()
async def fallback(message: Message):
    return


# ==========================================
# MAIN ENTRY POINT
# ==========================================
async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print("BOT STARTED 🔥")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
