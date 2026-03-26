import asyncio
import logging
import sys
import time

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

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
ADMIN_ID = 1779566638  # Admin's numerical Telegram ID

# ==========================================
# DATA STRUCTURES
# ==========================================
users: set[int] = set()               # Set of all user IDs who started the bot
online_users: set[int] = set()        # (Optional) users recently active
last_active: dict[int, float] = dict()        # user_id -> timestamp
waiting_queue: list[int] = list()      # List of user IDs waiting for a match
active_chats: dict[int, int] = dict()       # Mapping of user_id -> partner_id
user_preferences: dict[int, dict] = dict()   # user_id -> {'username': str}
# user_state is managed powerfully and natively by aiogram's FSMContext!
user_gender: dict[int, str] = dict()        # user_id -> str
user_preference: dict[int, str] = dict()    # user_id -> str
pending_requests: dict[int, int] = dict()   # partner_id -> requester_id (for connection requests)

user_warnings: dict[int, int] = dict()      # user_id -> integer count of warnings
banned_users: set[int] = set()        # Set of user IDs that have been permanently banned
notified_users: set[int] = set()      # Users who were told to use /start already
has_matched_before: set[int] = set()  # Users who have matched at least once
match_lock = asyncio.Lock()           # Mutex for parallel try_match calls

# ==========================================
# HELPER DEFS
# ==========================================
def is_banned(user_id: int) -> bool:
    """Checks if a user is currently permanently banned."""
    print(f"[DEBUG] Checking ban status for {user_id}. Current banned_users: {banned_users}")
    return user_id in banned_users

class RegState(StatesGroup):
    waiting_for_gender = State()
    waiting_for_preference = State()


# ==========================================
# BOT & DISPATCHER SETUP
# ==========================================
if HAS_DEFAULT_PROPERTIES:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
else:
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)

# MemoryStorage is standard for FSM states
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)


# ==========================================
# UI KEYBOARDS
# ==========================================
def get_menu_btn() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⚙️ Menu")]], resize_keyboard=True)

def get_options_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Next 🔄"), KeyboardButton(text="Stop ⛔")],
            [KeyboardButton(text="Connect 🤝"), KeyboardButton(text="Report 🚨")],
            [KeyboardButton(text="Back ⬅️"), KeyboardButton(text="🧹 Clear Chat")]
        ],
        resize_keyboard=True
    )

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
# MATCHING LOGIC
# ==========================================
def is_match(user1: int, user2: int) -> bool:
    if user1 == user2: return False
    
    g1 = user_gender.get(user1, "Prefer not to say")
    p1 = user_preference.get(user1, "Everyone")
    
    g2 = user_gender.get(user2, "Prefer not to say")
    p2 = user_preference.get(user2, "Everyone")

    def is_compatible(p_self, g_other):
        if p_self == "Everyone": return True
        if p_self == "Boys": return g_other == "Boy"
        if p_self == "Girls": return g_other == "Girl"
        if p_self == "Gay": return g_other == "Boy"
        if p_self == "Lesbian": return g_other == "Girl"
        return False
        
    return is_compatible(p1, g2) and is_compatible(p2, g1)

async def animate_match(uid: int) -> bool:
    try:
        # step 1 typing
        await bot.send_chat_action(chat_id=uid, action="typing")
        await asyncio.sleep(2)

        # step 2 temporary message
        msg = await bot.send_message(uid, "🔍 Finding best match...")
        await asyncio.sleep(2)

        # step 3 remove message
        await bot.delete_message(chat_id=uid, message_id=msg.message_id)

        # step 4 typing again
        await bot.send_chat_action(chat_id=uid, action="typing")
        await asyncio.sleep(1.5)

        # final connect
        if uid in has_matched_before:
            text = "🎉 You are now connected to a new partner!"
        else:
            text = "🎉 Connected! Say hi."
            has_matched_before.add(uid)
            
        await bot.send_message(uid, text, reply_markup=get_menu_btn())
        return True
    except Exception as e:
        logging.warning(f"Failed to animate/send to user ({uid}): {e}")
        return False

async def reconnect_flow(uid: int):
    try:
        await bot.send_chat_action(chat_id=uid, action="typing")
        await asyncio.sleep(2.0)
        if uid in waiting_queue:
            await bot.send_message(uid, "⏳ Waiting for a new user...")
    except:
        pass

async def try_match():
    await asyncio.sleep(1.5)

    async with match_lock:
        i = 0
        while i < len(waiting_queue):
            u1 = waiting_queue[i]
            
            j = i + 1
            matched = False
            while j < len(waiting_queue):
                u2 = waiting_queue[j]
                
                if is_match(u1, u2):
                    waiting_queue.pop(j)
                    waiting_queue.pop(i)
                
                active_chats[u1] = u2
                active_chats[u2] = u1
                
                success_u1, success_u2 = await asyncio.gather(
                    animate_match(u1),
                    animate_match(u2)
                )

                if not success_u1 and not success_u2:
                    active_chats.pop(u1, None)
                    active_chats.pop(u2, None)
                elif not success_u1:
                    active_chats.pop(u1, None)
                    active_chats.pop(u2, None)
                    if not is_banned(u2):
                        waiting_queue.insert(0, u2) 
                        try: 
                            await bot.send_message(u2, "⚠️ Your partner left. Finding a new person for you...", reply_markup=ReplyKeyboardRemove())
                            asyncio.create_task(reconnect_flow(u2))
                        except: pass
                elif not success_u2:
                    active_chats.pop(u1, None)
                    active_chats.pop(u2, None)
                    if not is_banned(u1):
                        waiting_queue.insert(0, u1) 
                        try: 
                            await bot.send_message(u1, "⚠️ Your partner left. Finding a new person for you...", reply_markup=ReplyKeyboardRemove())
                            asyncio.create_task(reconnect_flow(u1))
                        except: pass
                        
                matched = True
                break 
                
            j += 1
            
        if not matched:
            i += 1


# ==========================================
# IN-CHAT ACTIONS
# ==========================================
async def handle_next(uid: int):
    if uid in active_chats:
        partner_id = active_chats.pop(uid)
        active_chats.pop(partner_id, None)

        try:
            await bot.send_message(uid, "Left chat. Finding another person best for you...", reply_markup=ReplyKeyboardRemove())
            if uid not in waiting_queue and not is_banned(uid):
                waiting_queue.append(uid)
                asyncio.create_task(reconnect_flow(uid))
        except Exception: pass 

        try:
            await bot.send_message(partner_id, "⚠️ Your partner left. Finding a new person for you...", reply_markup=ReplyKeyboardRemove())
            if partner_id not in waiting_queue and not is_banned(partner_id):
                waiting_queue.append(partner_id)
                asyncio.create_task(reconnect_flow(partner_id))
        except Exception: pass 

        asyncio.create_task(try_match())
    elif uid in waiting_queue:
        pass
    else:
        if not is_banned(uid):
            waiting_queue.append(uid)
            try:
                await bot.send_message(uid, "Finding another person best for you...", reply_markup=ReplyKeyboardRemove())
                asyncio.create_task(try_match())
            except:
                if uid in waiting_queue: waiting_queue.remove(uid)

async def handle_stop(uid: int):
    if uid in waiting_queue:
        waiting_queue.remove(uid)

    if uid in active_chats:
        partner_id = active_chats.pop(uid)
        active_chats.pop(partner_id, None)

        try:
            await bot.send_message(partner_id, "⚠️ Your partner left. Finding a new person for you...", reply_markup=ReplyKeyboardRemove())
            if partner_id not in waiting_queue and not is_banned(partner_id):
                waiting_queue.append(partner_id)
                asyncio.create_task(reconnect_flow(partner_id))
        except Exception:
            if partner_id in waiting_queue: waiting_queue.remove(partner_id)

        asyncio.create_task(try_match())

    try:
        await bot.send_message(uid, "You have stopped chatting. Type /start to find someone new.", reply_markup=ReplyKeyboardRemove())
    except: pass

async def handle_connect(uid: int):
    if uid not in active_chats:
        try: await bot.send_message(uid, "You need to be in a chat to connect with someone.")
        except: pass
        return

    partner_id = active_chats[uid]
    pending_requests[partner_id] = uid

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Accept ✅", callback_data=f"conn_accept_{uid}"),
            InlineKeyboardButton(text="Reject ❌", callback_data=f"conn_reject_{uid}")
        ]
    ])

    try:
        await bot.send_message(partner_id, "This user wants to connect. Do you approve?", reply_markup=kb)
        await bot.send_message(uid, "Connection request sent. 🤝")
    except Exception: pass

async def handle_report(uid: int):
    if uid in active_chats:
        partner_id = active_chats.pop(uid)
        active_chats.pop(partner_id, None)

        warnings = user_warnings.get(partner_id, 0) + 1
        user_warnings[partner_id] = warnings

        if warnings >= 5:
            banned_users.add(partner_id)
            if partner_id in waiting_queue: waiting_queue.remove(partner_id)
            print(f"[BAN] User ID {partner_id} was permanently banned (5 warnings).")
            try: await bot.send_message(partner_id, "❌ You have been permanently banned due to multiple violations.", reply_markup=ReplyKeyboardRemove())
            except: pass
        else:
            remaining = 5 - warnings
            print(f"[WARNING] User ID {partner_id} warned ({warnings}/5).")
            try:
                await bot.send_message(
                    partner_id, 
                    f"⚠️ Warning {warnings}/5. Please follow rules or you will be banned.\nYou have {remaining} warning(s) left.", 
                    reply_markup=ReplyKeyboardRemove()
                )
            except: pass

        try:
            await bot.send_message(uid, "User reported. You are safe. 🛡️ Finding a new partner for you...", reply_markup=ReplyKeyboardRemove())
            if uid not in waiting_queue and not is_banned(uid):
                waiting_queue.append(uid)
            asyncio.create_task(try_match())
        except: pass
    else:
        try: await bot.send_message(uid, "You are not in a chat right now.")
        except: pass

async def handle_clearchat(message: Message, state: FSMContext):
    uid = message.from_user.id
    if uid in waiting_queue:
        waiting_queue.remove(uid)

    if uid in active_chats:
        partner_id = active_chats.pop(uid)
        active_chats.pop(partner_id, None)

        try:
            await bot.send_message(partner_id, "⚠️ Your partner ended the chat.", reply_markup=ReplyKeyboardRemove())
            if partner_id not in waiting_queue and not is_banned(partner_id):
                waiting_queue.append(partner_id)
                asyncio.create_task(reconnect_flow(partner_id))
        except Exception:
            if partner_id in waiting_queue: waiting_queue.remove(partner_id)

        asyncio.create_task(try_match())

    user_gender.pop(uid, None)
    user_preference.pop(uid, None)
    notified_users.discard(uid)
    has_matched_before.discard(uid)
    await state.clear()

    await message.answer("🧹 Chat cleared! Starting fresh...", reply_markup=ReplyKeyboardRemove())
    await asyncio.sleep(0.5)
    await message.answer("What is your gender?", reply_markup=get_gender_kb())
    await state.set_state(RegState.waiting_for_gender)


# ==========================================
# ADMIN COMMANDS
# ==========================================
@router.message(Command("unban"))
async def cmd_unban(message: Message):
    """Admin command to unban a user."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ You are not allowed to use this command")
        return

    parts = message.text.split()

    if len(parts) < 2:
        await message.answer("Usage: /unban user_id")
        return

    try:
        user_id = int(parts[1])

        if is_banned(user_id):
            banned_users.discard(user_id) 
            user_warnings[user_id] = 0
            
            print(f"[DEBUG] User {user_id} successfully unbanned! Current banned_users: {banned_users}")

            await message.answer(f"User {user_id} has been successfully unbanned")

            try:
                await bot.send_message(
                    user_id,
                    "You have been unbanned by admin.\n\n"
                    "This is your final warning.\n"
                    "If you violate rules again, strict action will be taken."
                )
            except:
                await message.answer("User cannot be messaged (they might have blocked the bot).")

        else:
            await message.answer("⚠️ User is not banned")

    except ValueError:
        await message.answer("❌ Invalid user ID. It must be a number.")
    except Exception:
         await message.answer("❌ Error processing command.")


# ==========================================
# STATUS & INVITE SYSTEM
# ==========================================
@router.message(Command("status"))
async def cmd_status(message: Message):
    uid = message.from_user.id
    if is_banned(uid): return
    
    current_time = time.time()
    last_active[uid] = current_time
    
    online_count = sum(1 for t in last_active.values() if current_time - t < 300)
    total_users = len(users)
    
    await message.answer(f"📊 Talkify Live Stats:\n\n👥 Total Users: {total_users}\n🟢 Online Now: {online_count}")

@router.message(Command("invite"))
async def cmd_invite(message: Message):
    uid = message.from_user.id
    if is_banned(uid): return

    last_active[uid] = time.time()
    
    btn = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Share Bot", url="https://t.me/TalkifyConnectBot")
    ]])
    
    await message.answer(
        "🚀 Invite your friends and grow the community!\n\n"
        "Share this link:\nhttps://t.me/TalkifyConnectBot",
        reply_markup=btn
    )

# ==========================================
# REGISTRATION SYSTEM
# ==========================================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id

    if is_banned(uid):
        return await message.answer("🚫 You are banned from using this bot.", reply_markup=ReplyKeyboardRemove())

    users.add(uid)
    identity = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    
    if uid not in user_preferences: user_preferences[uid] = {}
    user_preferences[uid]['username'] = identity

    if uid in active_chats: await handle_stop(uid)
    elif uid in waiting_queue: waiting_queue.remove(uid)

    last_active[uid] = time.time()
    notified_users.discard(uid)
    await message.answer(
        "👋 Welcome to Talkify!\n\n"
        "Let's set up your profile quickly.\n"
        "Invite friends to get better matches!",
        reply_markup=ReplyKeyboardRemove()
    )
    await asyncio.sleep(0.5)
    await message.answer("What is your gender?", reply_markup=get_gender_kb())
    await state.set_state(RegState.waiting_for_gender)

@router.message(RegState.waiting_for_gender)
async def process_gender(message: Message, state: FSMContext):
    uid = message.from_user.id
    if is_banned(uid): return

    text = message.text.strip() if message.text else ""
    if text not in ["Boy", "Girl", "Prefer not to say"]:
        return
        
    user_gender[uid] = text
    
    await message.answer("Who do you want to connect with?", reply_markup=get_preference_kb())
    await state.set_state(RegState.waiting_for_preference)

@router.message(RegState.waiting_for_preference)
async def process_preference(message: Message, state: FSMContext):
    uid = message.from_user.id
    if is_banned(uid): return

    text = message.text.strip() if message.text else ""
    if text not in ["Boys", "Girls", "Gay", "Lesbian", "Everyone"]:
        return
        
    user_preference[uid] = text

    await state.clear() 
    await message.answer("✅ Setup complete! Finding a partner...", reply_markup=ReplyKeyboardRemove())
    
    if uid not in waiting_queue:
        waiting_queue.append(uid)
    asyncio.create_task(try_match())

# ==========================================
# CALLBACKS & MAIN HANDLER
# ==========================================
@router.callback_query(F.data.startswith("conn_"))
async def on_connect_response(call: CallbackQuery):
    parts = call.data.split("_")
    action = parts[1]
    requester_id = int(parts[2])
    uid = call.from_user.id

    if is_banned(uid):
        try: await call.answer("🚫 You are banned from using this bot.", show_alert=True)
        except: pass
        return

    if uid not in active_chats or active_chats[uid] != requester_id:
        try:
            await call.answer("This request is no longer valid.", show_alert=True)
            await call.message.edit_reply_markup(reply_markup=None)
        except: pass
        return

    if pending_requests.get(uid) != requester_id:
        try:
            await call.answer("Request expired.", show_alert=True)
            await call.message.edit_reply_markup(reply_markup=None)
        except: pass
        return

    if action == "accept":
        u1_name = user_preferences[uid].get('username', 'Unknown')
        u2_name = user_preferences[requester_id].get('username', 'Unknown')
        try:
            await bot.send_message(uid, f"Connection accepted! ✅\nYou can now chat in DM.\nPartner: {u2_name}")
            await bot.send_message(requester_id, f"Connection accepted! ✅\nYou can now chat in DM.\nPartner: {u1_name}")
        except: pass
        pending_requests.pop(uid, None)

    elif action == "reject":
        try:
            await bot.send_message(requester_id, "Request was rejected. ❌")
            await bot.send_message(uid, "You rejected the request. ❌")
        except: pass
        pending_requests.pop(uid, None)

    try:
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer()
    except: pass


@router.message()
async def on_message_received(message: Message, state: FSMContext):
    uid = message.from_user.id
    last_active[uid] = time.time()

    # Core execution block
    if is_banned(uid):
        return await message.answer("🚫 You are banned from using this bot.", reply_markup=ReplyKeyboardRemove())

    curr_state = await state.get_state()
    if curr_state: return
        
    text = message.text

    if text == "⚙️ Menu":
        return await message.answer("⚙️ Options Menu", reply_markup=get_options_menu())
    elif text == "Back ⬅️":
        return await message.answer("Retracted menu.", reply_markup=get_menu_btn())
    elif text == "Next 🔄":
        return await handle_next(uid)
    elif text == "Stop ⛔":
        return await handle_stop(uid)
    elif text == "Connect 🤝":
        return await handle_connect(uid)
    elif text == "Report 🚨":
        return await handle_report(uid)
    elif text == "🧹 Clear Chat" or text == "/clearchat":
        return await handle_clearchat(message, state)

    if uid in active_chats:
        partner_id = active_chats[uid]
        try:
            await bot.copy_message(
                chat_id=partner_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
        except Exception as e:
            err_msg = str(e).lower()
            logging.warning(f"Failed to forward message from {uid} to {partner_id}: {e}")
            if "forbidden" in err_msg or "blocked" in err_msg or "not found" in err_msg or "deactivated" in err_msg:
                await _disconnect_unreachable(uid, partner_id)
    elif uid not in waiting_queue:
        if uid not in notified_users:
            try:
                await message.answer("Use /start to find a partner.", reply_markup=ReplyKeyboardRemove())
                notified_users.add(uid)
            except:
                pass

async def _disconnect_unreachable(active_uid: int, blocked_uid: int):
    active_chats.pop(active_uid, None)
    active_chats.pop(blocked_uid, None)
    try:
        await bot.send_message(active_uid, "⚠️ Your partner left. Finding a new person for you...", reply_markup=ReplyKeyboardRemove())
        if active_uid not in waiting_queue and not is_banned(active_uid):
            waiting_queue.append(active_uid)
            asyncio.create_task(reconnect_flow(active_uid))
        asyncio.create_task(try_match())
    except: pass


# ==========================================
# MAIN ENTRY POINT
# ==========================================
async def main():
    print("BOT STARTED 🔥")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
