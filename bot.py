from datetime import datetime
import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, BotCommand

# --- CONFIG ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS_FILE = "admins.txt"
SUPER_ADMIN_ID = 762076580

if not TOKEN:
    raise ValueError("❌ Ошибка: Не найден BOT_TOKEN в переменных окружения хостинга!")

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

# --- ADMINскай СОХРАНИТЕЛЬ ---
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
        [KeyboardButton(text="📊 Статистика бота"), KeyboardButton(text="📁 Обращения за месяц")],
        [KeyboardButton(text="👥 Управление админами"), KeyboardButton(text="🔙 В меню сотрудника")]
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

cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Отменить и в меню")]],
    resize_keyboard=True
)

# --- УСТАНОВКА КНОПКИ МЕНЮ В ТЕЛЕГРАМЕ ---
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота / Главное меню")
    ]
    await bot.set_my_commands(commands)

# --- HANDLERS: START ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    hour = datetime.now().hour
    user_id = message.from_user.id
    
    if 5 <= hour < 12: greeting = "Доброе утро! ☀️"
    elif 12 <= hour < 17: greeting = "Добрый день! 🍏"
    elif 17 <= hour < 23: greeting = "Добрый вечер! 🌆"
    else: greeting = "Доброй ночи! 🌙"

    admins = load_admins()
    if user_id in admins:
        await message.answer(f"{greeting} Панель администратора активна:", reply_markup=admin_keyboard)
    else:
        await message.answer(f"{greeting} 👋 Твой личный помощник во ВкусВилле на связи.\n\n👇 Выберите нужный раздел или категорию обращения ниже:", reply_markup=employee_keyboard)

@router.message(F.text == "🔙 В меню сотрудника")
async def back_to_emp(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("Меню сотрудника:", reply_markup=employee_keyboard)

@router.message(F.text == "🔙 Отменить и в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    waiting_for_text.pop(user_id, None)
    pending_appeals.pop(user_id, None)
    admin_reply_states.pop(user_id, None)
    pending_admin_replies.pop(user_id, None)
    adding_admin_process.discard(user_id)
    removing_admin_process.discard(user_id)
    
    if user_id in load_admins():
        await message.answer("Админ-панель:", reply_markup=admin_keyboard)
    else:
        await message.answer("Главное меню:", reply_markup=employee_keyboard)

# --- HANDLERS: EMPLOYEE APPEALS ---
categories = ["✍️ Анонимное обращение", "💡 Новые идеи", "⚠️ Жалобы", "⚔️ Конфликт", "🤝 Помощь сотруднику", "💬 Другие вопросы", "🌴 Отпуска", "🏥 Больничные"]

@router.message(F.text.in_(categories))
async def select_category(message: Message):
    user_id = message.from_user.id
    cat = message.text
    if cat == "🌴 Отпуска":
        await message.answer("🌴 **Информация по отпускам:** заявление подается за 2 недели.", parse_mode="Markdown")
        return
    elif cat == "🏥 Больничные":
        await message.answer("🏥 **Больничные:** отправьте номер закрытого больничного в кадры.", parse_mode="Markdown")
        return

    waiting_for_text[user_id] = {"category": cat}
    await message.answer(f"✍️ Вы выбрали: **{cat}**\n\nНапишите ваш вопрос или проблему одним сообщением:", reply_markup=cancel_keyboard, parse_mode="Markdown")

@router.message(F.text & F.from_user.id.in_(waiting_for_text))
async def get_appeal_text(message: Message):
    user_id = message.from_user.id
    cat_data = waiting_for_text.pop(user_id)
    pending_appeals[user_id] = {"category": cat_data["category"], "text": message.text}
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Отправить анонимно", callback_data="send_anon")],
        [InlineKeyboardButton(text="👤 Отправить с именем", callback_data="send_named")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_send")]
    ])
    await message.answer(f"📄 **Проверьте текст обращения:**\n\n> {message.text}\n\nКак отправить?", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data.in_({"send_anon", "send_named"}))
async def confirm_send_appeal(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = pending_appeals.pop(user_id, None)
    if not data:
        await callback.answer("Сессия истекла.", show_alert=True)
        return

    is_anon = (callback.data == "send_anon")
    user = callback.from_user
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO appeals (user_id, user_name, category, text, is_anon, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'new', ?)
    """, (user.id, user.full_name, data["category"], data["text"], 1 if is_anon else 0, now))
    appeal_id = cursor.lastrowid
    conn.commit()
    conn.close()

    if is_anon:
        header = f"🚨 **Новое АНОНИМНОЕ обращение (ID: {appeal_id})**\n📂 **Категория:** {data['category']}"
    else:
        username = f" (@{user.username})" if user.username else ""
        header = f"🚨 **Обращение от сотрудника (ID: {appeal_id})**: {user.full_name}{username}\n📂 **Категория:** {data['category']}"

    full_text = f"{header}\n\n{data['text']}"
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Взять в работу / Ответить", callback_data=f"take_{appeal_id}")]
    ])

    for admin_id in load_admins():
        try:
            await bot.send_message(admin_id, full_text, parse_mode="Markdown", reply_markup=admin_kb)
        except Exception:
            pass

    await callback.message.edit_text("✅ Ваше обращение успешно отправлено администраторам!", reply_markup=None)
    try:
        await callback.message.answer_photo(photo=FSInputFile("image_5.png"), caption="Спасибо, что вы с нами! Мы на связи и готовы ПОМОЧЬ 💚")
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "cancel_send")
async def cancel_send(callback: CallbackQuery):
    pending_appeals.pop(callback.from_user.id, None)
    await callback.message.edit_text("❌ Отправка отменена.", reply_markup=None)
    await callback.answer()

# --- HANDLERS: ADMIN ACTIONS & REPLIES ---
@router.callback_query(F.data.startswith("take_"))
async def take_appeal(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id not in load_admins():
        await callback.answer("У вас нет прав администратора.", show_alert=True)
        return

    appeal_id = int(callback.data.split("_")[1])

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status, admin_id, user_id FROM appeals WHERE id = ?", (appeal_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        await callback.answer("Обращение не найдено в базе.", show_alert=True)
        return

    status, current_admin, target_user_id = row

    if status == 'in_progress' and current_admin != admin_id:
        conn.close()
        await callback.answer("⚠️ Это обращение уже взял в работу другой администратор!", show_alert=True)
        return
    elif status == 'closed':
        conn.close()
        await callback.answer("⚠️ Это обращение уже закрыто.", show_alert=True)
        return

    cursor.execute("UPDATE appeals SET status = 'in_progress', admin_id = ? WHERE id = ?", (admin_id, appeal_id))
    conn.commit()
    conn.close()

    admin_reply_states[admin_id] = {"appeal_id": appeal_id, "target_user_id": target_user_id}
    
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 В работе у вас", callback_data="noop")],
        [InlineKeyboardButton(text="✅ Завершить диалог", callback_data=f"close_{appeal_id}")]
    ]))
    await callback.message.answer(f"✍️ Вы взяли обращение **№{appeal_id}** в работу.\n\nОтправьте текстом, картинкой или файлом ваш ответ (перед отправкой бот попросит подтверждение).", parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("close_"))
async def close_appeal(callback: CallbackQuery):
    admin_id = callback.from_user.id
    appeal_id = int(callback.data.split("_")[1])

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE appeals SET status = 'closed' WHERE id = ?", (appeal_id,))
    conn.commit()
    conn.close()

    admin_reply_states.pop(admin_id, None)
    pending_admin_replies.pop(admin_id, None)
    await callback.message.edit_text(callback.message.text + "\n\n✅ *Диалог закрыт администратором.*", parse_mode="Markdown", reply_markup=None)
    await callback.answer("Обращение закрыто!")

@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer("Обращение уже обрабатывается.", show_alert=True)

@router.message(F.from_user.id.in_(admin_reply_states))
async def admin_prepare_reply(message: Message):
    admin_id = message.from_user.id
    state = admin_reply_states.get(admin_id)
    if not state:
        return

    pending_admin_replies[admin_id] = {
        "appeal_id": state["appeal_id"],
        "target_user_id": state["target_user_id"],
        "msg": message
    }

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, отправить сотруднику", callback_data="confirm_admin_reply")],
        [InlineKeyboardButton(text="❌ Отменить / Переписать", callback_data="cancel_admin_reply")]
    ])

    await message.answer("⚠️ **Проверьте ваш ответ перед отправкой:**\nЭто сообщение увидит сотрудник.", reply_markup=confirm_kb, parse_mode="Markdown")

@router.callback_query(F.data.in_({"confirm_admin_reply", "cancel_admin_reply"}))
async def admin_reply_confirmation(callback: CallbackQuery):
    admin_id = callback.from_user.id
    reply_data = pending_admin_replies.pop(admin_id, None)

    if callback.data == "cancel_admin_reply":
        await callback.message.edit_text("❌ Отправка ответа отменена. Можете отправить другой вариант.", reply_markup=None)
        await callback.answer()
        return

    if not reply_data:
        await callback.message.edit_text("⚠️ Данные устарели или сессия истекла.", reply_markup=None)
        await callback.answer()
        return

    target_user_id = reply_data["target_user_id"]
    appeal_id = reply_data["appeal_id"]
    msg = reply_data["msg"]

    try:
        if msg.photo:
            await bot.send_photo(target_user_id, photo=msg.photo[-1].file_id, caption=f"💬 **Ответ от администрации по обращению №{appeal_id}:**\n{msg.caption or ''}", parse_mode="Markdown")
        elif msg.document:
            await bot.send_document(target_user_id, document=msg.document.file_id, caption=f"💬 **Ответ от администрации по обращению №{appeal_id}:**\n{msg.caption or ''}", parse_mode="Markdown")
        elif msg.text:
            await bot.send_message(target_user_id, f"💬 **Ответ от администрации по обращению №{appeal_id}:**\n\n{msg.text}", parse_mode="Markdown")
        
        await callback.message.edit_text("✅ Ответ успешно доставлен сотруднику!", reply_markup=None)
    except Exception as e:
        await callback.message.edit_text(f"❌ Не удалось отправить ответ пользователю (возможно, он заблокировал бота): {e}", reply_markup=None)
    
    await callback.answer()

# --- ADMIN PANEL BUTTONS ---
@router.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if message.from_user.id not in load_admins(): return
    today = datetime.now().strftime("%Y-%m-%d")
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM appeals WHERE created_at LIKE ?", (f"{today}%",))
    count_today = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM appeals")
    count_total = cursor.fetchone()[0]
    conn.close()

    await message.answer(f"📊 **Статистика бота:**\n\n• Обращений за сегодня: {count_today}\n• Всего обращений за всё время: {count_total}", parse_mode="Markdown")

@router.message(F.text == "📁 Обращения за месяц")
async def admin_month_appeals(message: Message):
    if message.from_user.id not in load_admins(): return
    current_month = datetime.now().strftime("%Y-%m")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, category, status, created_at FROM appeals WHERE created_at LIKE ? ORDER BY id DESC LIMIT 20", (f"{current_month}%",))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer(f"📁 За текущий месяц ({current_month}) обращений пока нет.")
        return

    text = f"📁 **Обращения за {current_month} (последние 20):**\n\n"
    for r in rows:
        status_emoji = "🆕" if r[2] == 'new' else ("🟡" if r[2] == 'in_progress' else "✅")
        text += f"{status_emoji} **№{r[0]}** | [{r[1]}] | Статус: `{r[2]}` | {r[3]}\n"

    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("👥 Управление администраторами:", reply_markup=manage_admins_keyboard)

@router.message(F.text == "🔙 Назад в админ-панель")
async def back_to_admin(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("Панель управления:", reply_markup=admin_keyboard)

@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if message.from_user.id in load_admins():
        admins = load_admins()
        lst = "\n".join([f"• `{aid}`" for aid in admins])
        await message.answer(f"📋 **Список администраторов:**\n\n{lst}", parse_mode="Markdown")

@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    if message.from_user.id in load_admins():
        adding_admin_process.add(message.from_user.id)
        await message.answer("✍️ Отправьте **Telegram ID** нового администратора (только цифры):", parse_mode="Markdown", reply_markup=cancel_keyboard)

@router.message(F.text == "➖ Удалить админа")
async def ask_remove_admin(message: Message):
    if message.from_user.id in load_admins():
        removing_admin_process.add(message.from_user.id)
        await message.answer("✍️ Отправьте **Telegram ID** администратора для удаления:", parse_mode="Markdown", reply_markup=cancel_keyboard)

@router.message(F.text)
async def process_admin_mutations(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    admins = load_admins()

    if user_id in adding_admin_process:
        adding_admin_process.remove(user_id)
        if text.isdigit():
            admins.add(int(text))
            save_admins(admins)
            await message.answer(f"✅ Администратор `{text}` успешно добавлен!", parse_mode="Markdown", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Ошибка. ID должен состоять только из цифр.", reply_markup=manage_admins_keyboard)
        return

    if user_id in removing_admin_process:
        removing_admin_process.remove(user_id)
        if text.isdigit():
            rem_id = int(text)
            if rem_id == SUPER_ADMIN_ID:
                await message.answer("❌ Нельзя удалить главного суперадмина!", reply_markup=manage_admins_keyboard)
            elif rem_id in admins:
                admins.remove(rem_id)
                save_admins(admins)
                await message.answer(f"🗑️ Администратор `{rem_id}` удален.", reply_markup=manage_admins_keyboard)
            else:
                await message.answer("⚠️ Такого ID нет в списке.", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Ошибка. ID должен состоять только из цифр.", reply_markup=manage_admins_keyboard)
        return

# --- ЛОВУШКА ДЛЯ ПУСТОГО ДИАЛОГА (ЕСЛИ НАПИСАЛИ ЛЮБОЙ ТЕКСТ ВМЕСТО СТАРТА) ---
@router.message()
async def catch_all_messages(message: Message):
    # Если пользователь написал что-то случайное в пустом чате, запускаем приветствие
    await cmd_start(message)

# --- MAIN RUN ---
async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await set_bot_commands(bot) # Устанавливаем кнопку Меню в Telegram
    print("🤖 Бот с кнопкой меню и авто-стартом успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
