from datetime import datetime
import asyncio
import logging
import os
import sqlite3
from collections import defaultdict
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, BotCommand

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS_FILE = "admins.txt"
SUPER_ADMIN_ID = 762076580

if not TOKEN:
    raise ValueError("❌ Ошибка: Не найден BOT_TOKEN!")

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            category TEXT,
            text TEXT,
            is_anon INTEGER,
            status TEXT DEFAULT 'new',
            admin_id INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- ADMINS ---
def load_admins():
    admins = {SUPER_ADMIN_ID}
    if os.path.exists(ADMINS_FILE):
        with open(ADMINS_FILE, "r") as f:
            for line in f:
                if line.strip().isdigit():
                    admins.add(int(line.strip()))
    return admins

def save_admins(admins):
    with open(ADMINS_FILE, "w") as f:
        for admin_id in admins:
            f.write(f"{admin_id}\n")

# --- STATES ---
waiting_for_text = {}
pending_appeals = {}
admin_reply_states = {}
pending_admin_replies = {}
adding_admin_process = set()
removing_admin_process = set()

# --- KEYBOARDS ---
employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✍️ Анонимное обращение"), KeyboardButton(text="💡 Новые идеи")],
        [KeyboardButton(text="⚠️ Жалобы"), KeyboardButton(text="⚔️ Конфликт")],
        [KeyboardButton(text="🤝 Помощь сотруднику"), KeyboardButton(text="💬 Другие вопросы")],
        [KeyboardButton(text="🌴 Отпуска"), KeyboardButton(text="🏥 Больничные")]
    ],
    resize_keyboard=True
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📁 Актуальные обращения"), KeyboardButton(text="📊 Статистика бота")],
        [KeyboardButton(text="📁 Обращения за месяц"), KeyboardButton(text="👥 Управление админами")],
        [KeyboardButton(text="🔙 В меню сотрудника")]
    ],
    resize_keyboard=True
)

manage_admins_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить админа"), KeyboardButton(text="➖ Удалить админа")],
        [KeyboardButton(text="📋 Список админов"), KeyboardButton(text="🔙 Назад в админ-панель")]
    ],
    resize_keyboard=True
)

cancel_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Отменить и в меню")]], resize_keyboard=True)

# --- START HANDLER ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    hour = datetime.now().hour
    user_id = message.from_user.id
    
    # ПРИВЯЗКА КАРТИНОК ПО ВРЕМЕНИ
    # Image.png - ночное, Image_2.png - вечер, Image_3.png - утро, Image_4.png - день
    if 5 <= hour < 12:
        greeting, photo_file = "Доброе утро! ☀️", "image_3.png"
    elif 12 <= hour < 17:
        greeting, photo_file = "Добрый день! 🍏", "image_4.png"
    elif 17 <= hour < 23:
        greeting, photo_file = "Добрый вечер! 🌆", "image_2.png"
    else:
        greeting, photo_file = "Доброй ночи! 🌙", "image.png"

    admins = load_admins()
    if user_id in admins:
        await message.answer_photo(photo=FSInputFile(photo_file), caption=f"{greeting} Панель администратора активна:", reply_markup=admin_keyboard)
    else:
        await message.answer_photo(photo=FSInputFile(photo_file), caption=f"{greeting} 👋 Твой личный помощник на связи.\n\n👇 Выберите нужный раздел:", reply_markup=employee_keyboard)

@router.message(F.text == "🔙 В меню сотрудника")
async def back_to_emp(message: Message):
    await message.answer("Меню сотрудника:", reply_markup=employee_keyboard)

@router.message(F.text == "🔙 Отменить и в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    waiting_for_text.pop(user_id, None)
    if user_id in load_admins():
        await message.answer("Админ-панель:", reply_markup=admin_keyboard)
    else:
        await message.answer("Главное меню:", reply_markup=employee_keyboard)

# --- CATEGORY HANDLERS ---
@router.message(F.text.in_(["✍️ Анонимное обращение", "💡 Новые идеи", "⚠️ Жалобы", "⚔️ Конфликт", "🤝 Помощь сотруднику", "💬 Другие вопросы", "🌴 Отпуска", "🏥 Больничные"]))
async def select_category(message: Message):
    waiting_for_text[message.from_user.id] = {"category": message.text}
    await message.answer(f"✍️ Вы выбрали: {message.text}\nНапишите ваш вопрос одним сообщением:", reply_markup=cancel_keyboard)

@router.message(F.text & F.from_user.id.in_(waiting_for_text))
async def get_appeal_text(message: Message):
    user_id = message.from_user.id
    cat_data = waiting_for_text.pop(user_id)
    pending_appeals[user_id] = {"category": cat_data["category"], "text": message.text}
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Анонимно", callback_data="send_anon")],
        [InlineKeyboardButton(text="👤 С именем", callback_data="send_named")]
    ])
    await message.answer("📄 Проверьте текст обращения и выберите способ отправки:", reply_markup=kb)

# --- CONFIRM & SEND (IMAGE_5.PNG) ---
@router.callback_query(F.data.in_({"send_anon", "send_named"}))
async def confirm_send_appeal(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = pending_appeals.pop(user_id, None)
    if not data: return
    
    # Сохранение в БД
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO appeals (user_id, user_name, category, text, is_anon, created_at) VALUES (?,?,?,?,?,?)", 
                   (user_id, callback.from_user.full_name, data["category"], data["text"], 1 if callback.data == "send_anon" else 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    appeal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Уведомление админам
    for admin_id in load_admins():
        try: await bot.send_message(admin_id, f"🚨 Новое обращение №{appeal_id}\n📂 {data['category']}\n{data['text']}")
        except: pass

    await callback.message.edit_text("✅ Обращение успешно отправлено!")
    # БЛАГОДАРНОСТЬ
    await callback.message.answer_photo(photo=FSInputFile("image_5.png"), caption="Спасибо, что вы с нами! Мы на связи и готовы ПОМОЧЬ 💚")
    await callback.answer()

# --- ADMIN PANEL LOGIC (STATS, LIST, ETC) ---
@router.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    count = conn.cursor().execute("SELECT COUNT(*) FROM appeals").fetchone()[0]
    conn.close()
    await message.answer(f"📊 Всего обращений: {count}")

@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("Управление админами:", reply_markup=manage_admins_keyboard)

@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    admins = load_admins()
    await message.answer(f"📋 Админы: {', '.join(map(str, admins))}")

@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    adding_admin_process.add(message.from_user.id)
    await message.answer("Введите ID нового админа:")

@router.message(F.text.isdigit() & F.from_user.id.in_(adding_admin_process))
async def add_admin_action(message: Message):
    admins = load_admins()
    admins.add(int(message.text))
    save_admins(admins)
    adding_admin_process.remove(message.from_user.id)
    await message.answer("✅ Админ добавлен!")

# --- MAIN RUN ---
async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main()
