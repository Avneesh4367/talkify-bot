import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

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
user_warnings = {}        # user_id -> int
user_info = {}            # user_id -> {'username': str, 'first_name': str}
pending_requests = {}     # requester_id -> partner_id

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

def get_chat_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔄 Next"), KeyboardButton(text="⛔ Stop")],
            [KeyboardButton(text="🚨 Report"), KeyboardButton(text="🤝 Connect")]
        ],
        resize_keyboard=True
    )

def get_connect_inline_kb(requester_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"accept_{requester_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{requester_id}")
            ]
        ]
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
            await bot.send_message(user1, "🎉 You are now connected!", reply_markup=get_chat_kb())
        except Exception:
            pass
            
        try:
            await bot.send_message(user2, "🎉 You are now connected!", reply_markup=get_chat_kb())
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
            user_state.pop(partner_id, None)
        except Exception:
            pass
            
    if user_id in waiting_queue:
        waiting_queue.remove(user_id)

    user_state[user_id] = "gender"
    user_info[user_id] = {
        'username': message.from_user.username,
        'first_name': message.from_user.first_name
    }
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
        text = message.text

        if text == "🔄 Next":
            # End current chat
            active_chats.pop(user_id, None)
            active_chats.pop(partner, None)
            
            # Add them back to waiting_queue
            user_state[user_id] = "waiting"
            user_state[partner] = "waiting"
            waiting_queue.append(user_id)
            waiting_queue.append(partner)
            
            await message.answer("🔍 Finding new partner...", reply_markup=ReplyKeyboardRemove())
            try:
                await bot.send_message(partner, "⚠️ Partner requested Next. Finding new partner...", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
            
            await try_match()
            return

        elif text == "⛔ Stop":
            # End chat and do NOT reconnect
            active_chats.pop(user_id, None)
            active_chats.pop(partner, None)
            
            user_state.pop(user_id, None)
            user_state[partner] = "waiting"
            waiting_queue.append(partner)
            
            await message.answer("⛔ Chat stopped.", reply_markup=ReplyKeyboardRemove())
            try:
                await bot.send_message(partner, "⚠️ Partner stopped chat. Finding new partner...", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
                
            await try_match()
            return

        elif text == "🚨 Report":
            # Add warning to partner and end chat
            user_warnings[partner] = user_warnings.get(partner, 0) + 1
            
            active_chats.pop(user_id, None)
            active_chats.pop(partner, None)
            
            user_state[user_id] = "waiting"
            user_state.pop(partner, None)
            waiting_queue.append(user_id)
            
            await message.answer("⚠️ User reported.", reply_markup=ReplyKeyboardRemove())
            try:
                await bot.send_message(partner, "⚠️ You have been reported and the chat has ended.", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
                
            await try_match()
            return

        elif text == "🤝 Connect":
            # Check if a request is already pending
            if pending_requests.get(user_id) == partner:
                return await message.answer("⚠️ You already sent a request.")
            
            pending_requests[user_id] = partner
            await message.answer("Request sent to partner.")
            try:
                await bot.send_message(
                    partner,
                    "🤝 Your partner wants to connect privately. Do you want to share your Telegram IDs?",
                    reply_markup=get_connect_inline_kb(user_id)
                )
            except Exception:
                pass
            return
            
        # Normal chat forwarding
        try:
            await bot.send_message(partner, message.text)
        except Exception:
            # Drop chat on failed delivery (e.g. user blocked bot)
            active_chats.pop(user_id, None)
            active_chats.pop(partner, None)
            user_state.pop(user_id, None)
            user_state.pop(partner, None)
            await message.answer("Message failed. The partner might have left.", reply_markup=ReplyKeyboardRemove())


# 6. Callback handlers for Connection Requests
@dp.callback_query(F.data.startswith("accept_"))
async def handle_accept(call: CallbackQuery):
    requester_id = int(call.data.split("_")[1])
    approver_id = call.from_user.id
    
    # Safety Check: still in chat?
    if active_chats.get(approver_id) != requester_id:
        await call.answer("❌ This request is no longer valid.", show_alert=True)
        await call.message.delete()
        return
        
    # Share IDs/Usernames
    req_info = user_info.get(requester_id, {})
    app_info = user_info.get(approver_id, {})
    
    req_mention = f"@{req_info['username']}" if req_info.get('username') else f"ID: {requester_id}"
    app_mention = f"@{app_info['username']}" if app_info.get('username') else f"ID: {approver_id}"
    
    try:
        await bot.send_message(
            requester_id,
            f"✅ Connection approved!\n\nYou can now message each other privately:\n\nUser: {app_mention}"
        )
        await bot.send_message(
            approver_id,
            f"✅ Connection approved!\n\nYou can now message each other privately:\n\nUser: {req_mention}"
        )
    except Exception:
        pass
        
    pending_requests.pop(requester_id, None)
    await call.message.edit_text("✅ ID shared!")
    await call.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def handle_reject(call: CallbackQuery):
    requester_id = int(call.data.split("_")[1])
    
    try:
        await bot.send_message(requester_id, "❌ Your connection request was rejected.")
    except Exception:
        pass
        
    pending_requests.pop(requester_id, None)
    await call.message.edit_text("❌ Request rejected.")
    await call.answer()


# 7. fallback handler (silent)
@dp.message()
async def fallback(message: Message):
    return


# ==========================================
# MAIN ENTRY POINT
# ==========================================
async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    print("BOT STARTED")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
