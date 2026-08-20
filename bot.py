from datetime import datetime
import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

TOKEN = os.getenv("BOT_TOKEN")
ADMINS_FILE = "admins.txt"
SUPER_ADMIN_ID = 762076580

if not TOKEN:
    raise ValueError("❌ Ошибка: Не найден BOT_TOKEN!")

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

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
            photo_id TEXT,
            is_anon INTEGER,
            status TEXT DEFAULT 'new',
            admin_id INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

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

waiting_for_text = {}
pending_appeals = {}
admin_reply_states = {}
pending_admin_confirm = {}
adding_admin_process = set()
removing_admin_process = set()

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

@router.message(Command("start"))
async def cmd_start(message: Message):
    hour = datetime.now().hour
    user_id = message.from_user.id
    
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
    user_id = message.from_user.id
    if user_id in load_admins():
        await message.answer("Панель администратора:", reply_markup=admin_keyboard)
    else:
        await message.answer("Меню сотрудника:", reply_markup=employee_keyboard)

@router.message(F.text == "🔙 Назад в админ-панель")
async def back_to_admin(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("Панель администратора:", reply_markup=admin_keyboard)

@router.message(F.text == "🔙 Отменить и в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    waiting_for_text.pop(user_id, None)
    admin_reply_states.pop(user_id, None)
    pending_admin_confirm.pop(user_id, None)
    adding_admin_process.discard(user_id)
    removing_admin_process.discard(user_id)
    
    if user_id in load_admins():
        await message.answer("Админ-панель:", reply_markup=admin_keyboard)
    else:
        await message.answer("Главное меню:", reply_markup=employee_keyboard)

@router.message(F.text.in_(["✍️ Анонимное обращение", "💡 Новые идеи", "⚠️ Жалобы", "⚔️ Конфликт", "🤝 Помощь сотруднику", "💬 Другие вопросы", "🌴 Отпуска", "🏥 Больничные"]))
async def select_category(message: Message):
    waiting_for_text[message.from_user.id] = {"category": message.text}
    await message.answer(f"✍️ Вы выбрали: {message.text}\nНапишите ваш вопрос (можно прикрепить фото):", reply_markup=cancel_keyboard)

# Обработка текста или фото от сотрудника
@router.message((F.text | F.photo) & F.from_user.id.in_(waiting_for_text))
async def get_appeal_content(message: Message):
    user_id = message.from_user.id
    cat_data = waiting_for_text.pop(user_id)
    
    text = message.caption if message.photo else message.text
    if not text:
        text = "Без текста (только фото)"
        
    photo_id = message.photo[-1].file_id if message.photo else None
    
    pending_appeals[user_id] = {
        "category": cat_data["category"], 
        "text": text, 
        "photo_id": photo_id
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить и отправить", callback_data="proceed_send")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_send")]
    ])
    
    if photo_id:
        await message.answer_photo(photo=photo_id, caption=f"📄 Категория: {cat_data['category']}\nТекст: {text}\n\nВсё верно? Отправляем?", reply_markup=kb)
    else:
        await message.answer(f"📄 Категория: {cat_data['category']}\nТекст: {text}\n\nВсё верно? Отправляем?", reply_markup=kb)

@router.callback_query(F.data == "cancel_send")
async def cancel_send_callback(callback: CallbackQuery):
    pending_appeals.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ Отправка отменена.")
    await callback.message.answer("Главное меню:", reply_markup=employee_keyboard)
    await callback.answer()

@router.callback_query(F.data == "proceed_send")
async def ask_anonymity_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in pending_appeals:
        await callback.answer("Данные устарели, начните заново.", show_alert=True)
        return
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Анонимно", callback_data="send_anon")],
        [InlineKeyboardButton(text="👤 С именем", callback_data="send_named")]
    ])
    await callback.message.edit_text("Выберите способ отправки обращения:", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.in_({"send_anon", "send_named"}))
async def confirm_send_appeal(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = pending_appeals.pop(user_id, None)
    if not data: 
        return
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO appeals (user_id, user_name, category, text, photo_id, is_anon, created_at) VALUES (?,?,?,?,?,?,?)", 
        (user_id, callback.from_user.full_name, data["category"], data["text"], data["photo_id"], 1 if callback.data == "send_anon" else 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    appeal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить на обращение", callback_data=f"reply_appeal_{appeal_id}")]
    ])
    
    author_info = "🕵️‍♂️ Анонимный сотрудник" if callback.data == "send_anon" else f"👤 {callback.from_user.full_name}"
    for admin_id in load_admins():
        try: 
            msg_text = f"🚨 Новое обращение №{appeal_id}\n📂 Категория: {data['category']}\nОт: {author_info}\n\n💬 Текст:\n{data['text']}"
            if data["photo_id"]:
                await bot.send_photo(admin_id, photo=data["photo_id"], caption=msg_text, reply_markup=admin_kb)
            else:
                await bot.send_message(admin_id, msg_text, reply_markup=admin_kb)
        except: 
            pass

    await callback.message.edit_text("✅ Обращение успешно отправлено!")
    await callback.message.answer_photo(photo=FSInputFile("image_5.png"), caption="Спасибо, что вы с нами! Мы на связи и готовы ПОМОЧЬ 💚")
    await callback.answer()

# --- ЛОГИКА ОТВЕТОВ АДМИНА С ПОДТВЕРЖДЕНИЕМ И ФОТО ---
@router.callback_query(F.data.startswith("reply_appeal_"))
async def start_admin_reply(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        await callback.answer("У вас нет прав администратора.", show_alert=True)
        return
    
    appeal_id = int(callback.data.split("_")[2])
    admin_reply_states[callback.from_user.id] = appeal_id
    
    await callback.message.answer(f"✍️ Введите ответ на обращение №{appeal_id} (можно прикрепить фото):", reply_markup=cancel_keyboard)
    await callback.answer()

@router.message((F.text | F.photo) & F.from_user.id.in_(admin_reply_states))
async def get_admin_reply_content(message: Message):
    admin_id = message.from_user.id
    appeal_id = admin_reply_states.pop(admin_id)
    
    text = message.caption if message.photo else message.text
    photo_id = message.photo[-1].file_id if message.photo else None
    
    pending_admin_confirm[admin_id] = {
        "appeal_id": appeal_id,
        "text": text,
        "photo_id": photo_id
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить ответ сотруднику", callback_data="confirm_admin_reply")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_admin_reply")]
    ])
    
    preview_msg = f"📤 Проверьте ответ на обращение №{appeal_id}:\n\n{text}"
    if photo_id:
        await message.answer_photo(photo=photo_id, caption=preview_msg, reply_markup=kb)
    else:
        await message.answer(preview_msg, reply_markup=kb)

@router.callback_query(F.data == "cancel_admin_reply")
async def cancel_admin_reply_cb(callback: CallbackQuery):
    pending_admin_confirm.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ Отправка ответа отменена.")
    await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    await callback.answer()

@router.callback_query(F.data == "confirm_admin_reply")
async def execute_admin_reply(callback: CallbackQuery):
    admin_id = callback.from_user.id
    data = pending_admin_confirm.pop(admin_id, None)
    if not data:
        await callback.answer("Данные устарели.", show_alert=True)
        return
        
    appeal_id = data["appeal_id"]
    reply_text = data["text"]
    photo_id = data["photo_id"]

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, category FROM appeals WHERE id = ?", (appeal_id,))
    row = cursor.fetchone()
    
    if row:
        target_user_id, category = row
        cursor.execute("UPDATE appeals SET status = 'closed', admin_id = ? WHERE id = ?", (admin_id, appeal_id))
        conn.commit()
        
        try:
            full_reply = f"📩 Получен ответ от администратора по вашему обращению №{appeal_id} ({category}):\n\n{reply_text}"
            if photo_id:
                await bot.send_photo(target_user_id, photo=photo_id, caption=full_reply)
            else:
                await bot.send_message(target_user_id, full_reply)
            await callback.message.edit_text("✅ Ответ успешно отправлен сотруднику!")
            await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
        except:
            await callback.message.edit_text("⚠️ Не удалось отправить сообщение сотруднику (возможно, он заблокировал бота).")
            await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    else:
        await callback.message.edit_text("❌ Ошибка: обращение не найдено в базе данных.")
        await callback.message.answer("Админ-панель:", reply_markup=admin_keyboard)
    conn.close()
    await callback.answer()

@router.message(F.text == "📁 Актуальные обращения")
async def show_active_appeals(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, category, text, photo_id, created_at, is_anon, user_name FROM appeals WHERE status = 'new'")
    appeals = cursor.fetchall()
    conn.close()

    if not appeals:
        await message.answer("📂 Нет актуальных (новых) обращений.")
        return

    for app in appeals:
        app_id, category, text, photo_id, created_at, is_anon, user_name = app
        author = "🕵️‍♂️ Анонимно" if is_anon else f"👤 {user_name}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_appeal_{app_id}")]
        ])
        msg_text = f"📌 Обращение №{app_id}\n📂 {category}\nОт: {author}\n📅 {created_at}\n\n{text}"
        if photo_id:
            await message.answer_photo(photo=photo_id, caption=msg_text, reply_markup=kb)
        else:
            await message.answer(msg_text, reply_markup=kb)

@router.message(F.text == "📁 Обращения за месяц")
async def show_monthly_appeals(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, category, status, created_at FROM appeals ORDER BY id DESC LIMIT 20")
    appeals = cursor.fetchall()
    conn.close()

    if not appeals:
        await message.answer("📂 Записей за последнее время нет.")
        return

    text_msg = "📁 Последние обращения в системе:\n\n"
    for app in appeals:
        app_id, category, status, created_at = app
        st_icon = "🟢 Закрыто" if status == 'closed' else "🟡 Новое"
        text_msg += f"• №{app_id} | {category} | {st_icon} | {created_at}\n"
    await message.answer(text_msg)

@router.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    total = cursor.execute("SELECT COUNT(*) FROM appeals").fetchone()[0]
    new_count = cursor.execute("SELECT COUNT(*) FROM appeals WHERE status = 'new'").fetchone()[0]
    conn.close()
    await message.answer(f"📊 Статистика бота:\n\n• Всего обращений: {total}\n• Активных (новых): {new_count}")

@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("Управление админами:", reply_markup=manage_admins_keyboard)

@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if message.from_user.id not in load_admins(): return
    admins = load_admins()
    await message.answer(f"📋 Список ID администраторов:\n" + "\n".join(map(str, admins)))

@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    if message.from_user.id not in load_admins(): return
    adding_admin_process.add(message.from_user.id)
    await message.answer("Введите Telegram ID нового администратора (число):", reply_markup=cancel_keyboard)

@router.message(F.text.isdigit() & F.from_user.id.in_(adding_admin_process))
async def add_admin_action(message: Message):
    user_id = message.from_user.id
    adding_admin_process.remove(user_id)
    new_admin_id = int(message.text)
    
    admins = load_admins()
    admins.add(new_admin_id)
    save_admins(admins)
    await message.answer(f"✅ Администратор с ID {new_admin_id} успешно добавлен!", reply_markup=manage_admins_keyboard)

@router.message(F.text == "➖ Удалить админа")
async def ask_remove_admin(message: Message):
    if message.from_user.id not in load_admins(): return
    removing_admin_process.add(message.from_user.id)
    await message.answer("Введите Telegram ID администратора для удаления:", reply_markup=cancel_keyboard)

@router.message(F.text.isdigit() & F.from_user.id.in_(removing_admin_process))
async def remove_admin_action(message: Message):
    user_id = message.from_user.id
    removing_admin_process.remove(user_id)
    target_id = int(message.text)
    
    if target_id == SUPER_ADMIN_ID:
        await message.answer("❌ Нельзя удалить главного администратора!", reply_markup=manage_admins_keyboard)
        return
        
    admins = load_admins()
    if target_id in admins:
        admins.remove(target_id)
        save_admins(admins)
        await message.answer(f"✅ Администратор с ID {target_id} удален.", reply_markup=manage_admins_keyboard)
    else:
        await message.answer("❌ Такого администратора нет в списке.", reply_markup=manage_admins_keyboard)

async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
