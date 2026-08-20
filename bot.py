from datetime import datetime, timedelta
import asyncio
import logging
import os
import sqlite3
import csv
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
            urgency TEXT DEFAULT 'Обычное',
            is_anon INTEGER,
            status TEXT DEFAULT 'new',
            rating INTEGER,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appeal_id INTEGER,
            sender_id INTEGER,
            sender_role TEXT,
            text TEXT,
            photo_id TEXT,
            created_at TEXT,
            FOREIGN KEY(appeal_id) REFERENCES appeals(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mood_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            score INTEGER,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            score INTEGER,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            joined_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id INTEGER PRIMARY KEY,
            blocked_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def is_user_blocked(user_id: int) -> bool:
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def register_user(user_id, user_name):
    if is_user_blocked(user_id):
        return
    try:
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, user_name, joined_at) VALUES (?, ?, ?)",
                       (user_id, user_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
    except:
        pass

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
active_dialogues = {}
admin_dialogues = {}
adding_admin_process = set()
removing_admin_process = set()
broadcast_process = set()
blocking_user_process = set()

# --- КЛАВИАТУРЫ ---

employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✍️ Анонимное обращение"), KeyboardButton(text="💡 Новые идеи")],
        [KeyboardButton(text="⚠️ Жалобы"), KeyboardButton(text="⚔️ Конфликт")],
        [KeyboardButton(text="🤝 Помощь сотруднику"), KeyboardButton(text="💬 Другие вопросы")],
        [KeyboardButton(text="🌴 Отпуска"), KeyboardButton(text="🏥 Больничные")],
        [KeyboardButton(text="💰 Зарплата"), KeyboardButton(text="📅 График")],
        [KeyboardButton(text="🌱 Как ваше настроение?")]
    ],
    resize_keyboard=True
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📁 Актуальные обращения"), KeyboardButton(text="📊 Статистика бота")],
        [KeyboardButton(text="📊 Отчет: Настроение"), KeyboardButton(text="📈 Отчет: Частые вопросы")],
        [KeyboardButton(text="📊 Отчет: Опрос за неделю"), KeyboardButton(text="📥 Выгрузить отчет за период")],
        [KeyboardButton(text="👥 Список участников"), KeyboardButton(text="📥 Выгрузить обращения (CSV)")],
        [KeyboardButton(text="👥 Управление админами"), KeyboardButton(text="🚫 Черный список")],
        [KeyboardButton(text="📢 Сделать рассылку")]
    ],
    resize_keyboard=True
)

manage_admins_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить админа"), KeyboardButton(text="➖ Удалить админа")],
        [KeyboardButton(text="📋 Список админов"), KeyboardButton(text="🔙 В панель администратора")]
    ],
    resize_keyboard=True
)

cancel_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Возврат в меню")]], resize_keyboard=True)

# --- ФОНОВЫЕ ЗАДАЧИ ---

async def auto_close_inactive_tickets():
    while True:
        await asyncio.sleep(10800)  # Каждые 3 часа
        try:
            conn = sqlite3.connect("bot_database.db")
            cursor = conn.cursor()
            three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            
            cursor.execute("""
                SELECT a.id, a.user_id 
                FROM appeals a 
                WHERE a.status = 'new' AND (
                    SELECT MAX(m.created_at) FROM messages_log m WHERE m.appeal_id = a.id
                ) < ?
            """, (three_days_ago,))
            
            inactive_tickets = cursor.fetchall()
            
            for appeal_id, user_id in inactive_tickets:
                if is_user_blocked(user_id): continue
                cursor.execute("UPDATE appeals SET status = 'closed' WHERE id = ?", (appeal_id,))
                conn.commit()
                active_dialogues.pop(user_id, None)
                
                try:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f"⭐ {i}", callback_data=f"rate_{appeal_id}_{i}") for i in range(1, 6)]
                    ])
                    await bot.send_message(
                        user_id, 
                        f"🔒 Ваше обращение №{appeal_id} было автоматически закрыто (тайм-аут 3 дня без ответа).\nОцените качество работы поддержки:", 
                        reply_markup=kb
                    )
                except:
                    pass
                
                for admin_id in load_admins():
                    try:
                        await bot.send_message(admin_id, f"🔒 Обращение №{appeal_id} закрыто автоматически по тайм-ауту (3 дня).")
                    except:
                        pass
            conn.close()
        except Exception as e:
            logging.error(f"Ошибка в автозакрытии тикетов: {e}")

async def schedule_weekly_polls():
    while True:
        now = datetime.now()
        if now.weekday() == 6 and now.hour == 14 and now.minute == 0:
            try:
                conn = sqlite3.connect("bot_database.db")
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users")
                all_users = cursor.fetchall()
                conn.close()

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=str(i), callback_data=f"w-poll_{i}") for i in range(1, 6)],
                    [InlineKeyboardButton(text=str(i), callback_data=f"w-poll_{i}") for i in range(6, 11)]
                ])

                for (u_id,) in all_users:
                    if is_user_blocked(u_id): continue
                    try:
                        await bot.send_message(
                            u_id, 
                            "📊 Воскресный пульс-опрос\n\nОцените текущую неделю, как вы справились со своей работой от 1 до 10:", 
                            reply_markup=kb
                        )
                    except:
                        pass
            except Exception as e:
                logging.error(f"Ошибка при рассылке еженедельного опроса: {e}")
            
            await asyncio.sleep(3660)
        else:
            await asyncio.sleep(30)

# --- ОБРАБОТЧИКИ КОМАНД И НАВИГАЦИЯ ---

@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer("❌ Доступ к сервису ограничен администрацией.")
        return

    register_user(user_id, message.from_user.full_name)
    
    hour = datetime.now().hour
    if 5 <= hour < 12:
        greeting, photo_file = "Доброе утро! ☀️", "image_3.png"
    elif 12 <= hour < 17:
        greeting, photo_file = "Добрый день! 🍏", "image_4.png"
    elif 17 <= hour < 23:
        greeting, photo_file = "Добрый вечер! 🌆", "image_2.png"
    else:
        greeting, photo_file = "Доброй ночи! 🌙", "image.png"

    admins = load_admins()
    try:
        if user_id in admins:
            admin_welcome = (
                f"{greeting}\n\n"
                "🛠 Приветствую вас в панели управления!\n\n"
                "👇 Выберите нужный раздел ниже:"
            )
            await message.answer_photo(photo=FSInputFile(photo_file), caption=admin_welcome, reply_markup=admin_keyboard)
        else:
            welcome_text = (
                f"{greeting} 👋 Рады приветствовать вас!\n\n"
                "🔒 Вы находитесь в **абсолютно анонимной и безопасной среде**. "
                "Здесь вы можете открыто делиться своими мыслями, идеями или трудностями.\n\n"
                "👇 Выберите нужный раздел в меню ниже:"
            )
            await message.answer_photo(photo=FSInputFile(photo_file), caption=welcome_text, reply_markup=employee_keyboard)
    except:
        if user_id in admins:
            await message.answer(f"{greeting}\n\n🛠 Приветствую вас в панели управления!", reply_markup=admin_keyboard)
        else:
            await message.answer(f"{greeting} 👋 Рады приветствовать вас!", reply_markup=employee_keyboard)

@router.message(F.text == "🔙 В панель администратора")
async def back_to_admin(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("🛠 Панель администратора:", reply_markup=admin_keyboard)

@router.message(F.text == "🔙 Возврат в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    waiting_for_text.pop(user_id, None)
    admin_dialogues.pop(user_id, None)
    adding_admin_process.discard(user_id)
    removing_admin_process.discard(user_id)
    broadcast_process.discard(user_id)
    blocking_user_process.discard(user_id)
    
    if user_id in load_admins():
        await message.answer("🛠 Возвращаемся в панель администратора:", reply_markup=admin_keyboard)
    else:
        await message.answer("🏠 Главное меню сотрудника:", reply_markup=employee_keyboard)

# --- УПРАВЛЕНИЕ БЛОКИРОВКАМИ (ЧЕРНЫЙ СПИСОК) ---

@router.message(F.text == "🚫 Черный список")
async def black_list_menu(message: Message):
    if message.from_user.id not in load_admins(): return
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, blocked_at FROM blocked_users")
    blocked = cursor.fetchall()
    conn.close()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Заблокировать по ID", callback_data="block_user_prompt")],
        [InlineKeyboardButton(text="➖ Разблокировать по ID", callback_data="unblock_user_prompt")]
    ])

    text = "🚫 **Управление черным списком (нарушители):**\n\n"
    if blocked:
        text += "Заблокированные ID:\n"
        for idx, (u_id, dt) in enumerate(blocked, 1):
            text += f"{idx}. `{u_id}` (с {dt})\n"
    else:
        text += "Черный список пуст."

    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "block_user_prompt")
async def prompt_block_user(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    blocking_user_process.add(callback.from_user.id)
    await callback.message.answer("🚫 Введите Telegram ID пользователя, которого необходимо заблокировать:", reply_markup=cancel_keyboard)
    await callback.answer()

@router.message(F.text.isdigit() & F.from_user.id.in_(blocking_user_process))
async def execute_block_user(message: Message):
    admin_id = message.from_user.id
    blocking_user_process.remove(admin_id)
    target_id = int(message.text)

    if target_id == SUPER_ADMIN_ID or target_id in load_admins():
        await message.answer("❌ Нельзя заблокировать администратора системы!", reply_markup=admin_keyboard)
        return

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO blocked_users (user_id, blocked_at) VALUES (?, ?)", 
                   (target_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    active_dialogues.pop(target_id, None)
    await message.answer(f"✅ Пользователь с ID `{target_id}` успешно заблокирован.", reply_markup=admin_keyboard)
    
    try:
        await bot.send_message(target_id, "❌ Ваш доступ к сервису поддержки был ограничен администрацией.")
    except:
        pass

@router.callback_query(F.data == "unblock_user_prompt")
async def prompt_unblock_user(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    blocking_user_process.add(callback.from_user.id)
    await callback.message.answer("🔓 Введите Telegram ID пользователя для разблокировки:", reply_markup=cancel_keyboard)
    await callback.answer()

@router.message(F.text.isdigit() & F.from_user.id.in_(blocking_user_process))
async def execute_unblock_user(message: Message):
    admin_id = message.from_user.id
    blocking_user_process.remove(admin_id)
    target_id = int(message.text)

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (target_id,))
    conn.commit()
    conn.close()

    await message.answer(f"✅ Пользователь с ID `{target_id}` разблокирован.", reply_markup=admin_keyboard)
    try:
        await bot.send_message(target_id, "✅ Ваш доступ к сервису поддержки был восстановлен.")
    except:
        pass

@router.callback_query(F.data.startswith("ban_user_"))
async def ban_user_from_ticket(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    appeal_id = int(callback.data.split("_")[2])

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM appeals WHERE id = ?", (appeal_id,))
    row = cursor.fetchone()
    if row:
        target_id = row[0]
        cursor.execute("INSERT OR IGNORE INTO blocked_users (user_id, blocked_at) VALUES (?, ?)", 
                       (target_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        cursor.execute("UPDATE appeals SET status = 'closed' WHERE id = ?", (appeal_id,))
        conn.commit()
        
        active_dialogues.pop(target_id, None)
        try:
            await bot.send_message(target_id, "❌ Вы были заблокированы администрацией за нарушение правил сервиса.")
        except:
            pass
        await callback.message.answer(f"🚫 Пользователь тикета №{appeal_id} заблокирован и внесен в черный список.")
    conn.close()
    await callback.answer()

# --- ОТЧЕТЫ И АНАЛИТИКА ---

@router.message(F.text == "📊 Отчет: Настроение")
async def report_mood(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT AVG(score), COUNT(*) FROM mood_logs")
    res = cursor.fetchone()
    avg_score, total_moods = res[0], res[1]

    cursor.execute("SELECT user_name, score, created_at FROM mood_logs ORDER BY id DESC LIMIT 10")
    recent_moods = cursor.fetchall()
    conn.close()

    if total_moods == 0:
        await message.answer("📊 Отчет по настроению пока пуст.")
        return

    avg_str = f"{avg_score:.1f} / 10" if avg_score else "Нет данных"
    report_text = f"📊 **Отчет по самочувствию сотрудников**\n\n• Всего оценок: {total_moods}\n• Средний балл: {avg_str}\n\n📝 **Последние 10 оценок:**\n"
    for m in recent_moods:
        u_name, score, dt = m
        report_text += f"• {dt} | {u_name}: {score}/10\n"

    await message.answer(report_text)

@router.message(F.text == "📊 Отчет: Опрос за неделю")
async def report_weekly_poll(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT AVG(score), COUNT(*) FROM weekly_polls")
    res = cursor.fetchone()
    avg_score, total_polls = res[0], res[1]

    cursor.execute("SELECT user_name, score, created_at FROM weekly_polls ORDER BY id DESC LIMIT 10")
    recent_polls = cursor.fetchall()
    conn.close()

    if total_polls == 0:
        await message.answer("📊 Еженедельный опрос пока не собран или никто не ответил.")
        return

    avg_str = f"{avg_score:.1f} / 10" if avg_score else "Нет данных"
    report_text = f"📊 **Отчет по еженедельному опросу («Как справились с работой»)**\n\n• Всего ответов: {total_polls}\n• Средний балл: {avg_str}\n\n📝 **Последние ответы:**\n"
    for p in recent_polls:
        u_name, score, dt = p
        report_text += f"• {dt} | {u_name}: {score}/10\n"

    await message.answer(report_text)

@router.message(F.text == "📈 Отчет: Частые вопросы")
async def report_frequent_questions(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT category, COUNT(*) as cnt FROM appeals GROUP BY category ORDER BY cnt DESC")
    stats = cursor.fetchall()
    conn.close()

    if not stats:
        await message.answer("📈 Пока нет данных по обращениям.")
        return

    report_text = "📈 **Топ частых вопросов (по категориям):**\n\n"
    for idx, (cat, count) in enumerate(stats, start=1):
        report_text += f"{idx}. **{cat}** — {count} обращений\n"

    await message.answer(report_text)

@router.message(F.text == "📥 Выгрузить отчет за период")
async def export_period_stats_csv(message: Message):
    if message.from_user.id not in load_admins(): return
    
    filename = "period_stats_export.csv"
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT 'Обращение' as type, id, user_name, category || ' (Срочность: ' || urgency || ')', status, created_at FROM appeals")
    appeals_rows = cursor.fetchall()
    
    cursor.execute("SELECT 'Настроение' as type, id, user_name, 'Оценка: ' || score, '-', created_at FROM mood_logs")
    mood_rows = cursor.fetchall()

    cursor.execute("SELECT 'Недельный опрос' as type, id, user_name, 'Оценка недели: ' || score, '-', created_at FROM weekly_polls")
    poll_rows = cursor.fetchall()
    
    conn.close()

    with open(filename, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Тип записи", "ID", "Имя сотрудника", "Описание / Значение", "Статус", "Дата"])
        for row in appeals_rows + mood_rows + poll_rows:
            writer.writerow(row)

    await message.answer_document(
        document=FSInputFile(filename),
        caption="📥 Сводная аналитическая выгрузка за всё время для Excel."
    )

@router.message(F.text == "📥 Выгрузить обращения (CSV)")
async def export_appeals_csv(message: Message):
    if message.from_user.id not in load_admins(): return
    
    filename = "appeals_export.csv"
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_name, category, urgency, is_anon, status, rating, created_at FROM appeals ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    with open(filename, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["ID тикета", "Имя сотрудника", "Категория", "Срочность", "Анонимно", "Статус", "Оценка CSAT", "Дата создания"])
        for row in rows:
            r_id, u_name, cat, urg, is_anon, status, rating, dt = row
            writer.writerow([r_id, u_name, cat, urg, "Да" if is_anon==1 else "Нет", "Закрыто" if status=='closed' else "Новое", rating or "Нет", dt])

    await message.answer_document(document=FSInputFile(filename), caption="📥 Выгрузка обращений в формате CSV.")

# --- РАССЫЛКА И СПИСКИ ---
@router.message(F.text == "👥 Список участников")
async def show_bot_users(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, user_name, joined_at FROM users ORDER BY joined_at DESC")
    users = cursor.fetchall()
    conn.close()

    if not users:
        await message.answer("👥 В базе пока нет зарегистрированных участников.")
        return

    text = f"👥 **Список пользователей ({len(users)} чел.):**\n\n"
    for idx, (u_id, u_name, joined) in enumerate(users, start=1):
        text += f"{idx}. {u_name} (ID: `{u_id}`) — с {joined}\n"
        if len(text) > 3800:
            text += "\n...и другие."
            break
    await message.answer(text)

@router.message(F.text == "📢 Сделать рассылку")
async def start_broadcast(message: Message):
    if message.from_user.id not in load_admins(): return
    broadcast_process.add(message.from_user.id)
    await message.answer("📢 Введите текст или прикрепите фото для массовой рассылки:", reply_markup=cancel_keyboard)

@router.message(F.from_user.id.in_(broadcast_process))
async def execute_broadcast(message: Message):
    admin_id = message.from_user.id
    broadcast_process.remove(admin_id)

    photo_id = message.photo[-1].file_id if message.photo else None
    text = message.caption if message.photo and message.caption else message.text

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    success, failed = 0, 0
    for (u_id,) in users:
        if is_user_blocked(u_id): continue
        try:
            if photo_id: await bot.send_photo(u_id, photo=photo_id, caption=text)
            else: await bot.send_message(u_id, text)
            success += 1
        except:
            failed += 1

    await message.answer(f"📢 Рассылка завершена!\n✅ Успешно: {success}\n❌ Ошибок: {failed}", reply_markup=admin_keyboard)

# --- ОПРОСЫ ---

@router.message(F.text == "🌱 Как ваше настроение?")
async def ask_mood(message: Message):
    if is_user_blocked(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"mood_{i}") for i in range(1, 6)],
        [InlineKeyboardButton(text=str(i), callback_data=f"mood_{i}") for i in range(6, 11)]
    ])
    await message.answer("🌱 Как вы себя чувствуете сегодня? Оцените от 1 до 10:", reply_markup=kb)

@router.callback_query(F.data.startswith("mood_"))
async def save_mood(callback: CallbackQuery):
    if is_user_blocked(callback.from_user.id): return
    score = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO mood_logs (user_id, user_name, score, created_at) VALUES (?,?,?,?)",
                   (user_id, callback.from_user.full_name, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    try: await callback.message.delete()
    except: pass
    await callback.message.answer(f"Спасибо за честность! Ваша оценка настроения: {score}/10.", reply_markup=employee_keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("w-poll_"))
async def save_weekly_poll(callback: CallbackQuery):
    if is_user_blocked(callback.from_user.id): return
    score = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO weekly_polls (user_id, user_name, score, created_at) VALUES (?,?,?,?)",
                   (user_id, callback.from_user.full_name, score, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    try: await callback.message.delete()
    except: pass
    await callback.message.answer(f"✅ Спасибо! Ваша оценка за неделю ({score}/10) успешно сохранена.", reply_markup=employee_keyboard)
    await callback.answer()

# --- СОЗДАНИЕ ТИКЕТОВ И ДИАЛОГИ ---

@router.message(F.text.in_(["✍️ Анонимное обращение", "💡 Новые идеи", "⚠️ Жалобы", "⚔️ Конфликт", "🤝 Помощь сотруднику", "💬 Другие вопросы", "🌴 Отпуска", "🏥 Больничные", "💰 Зарплата", "📅 График"]))
async def select_category(message: Message):
    if is_user_blocked(message.from_user.id): return
    waiting_for_text[message.from_user.id] = {"category": message.text}
    await message.answer(f"✍️ Категория: **{message.text}**\n\nОпишите ваш вопрос подробно (можно прикрепить скриншот или фото):", reply_markup=cancel_keyboard)

@router.message(F.from_user.id.in_(waiting_for_text))
async def get_appeal_content(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id): return
    cat_data = waiting_for_text.pop(user_id)
    
    photo_id = message.photo[-1].file_id if message.photo else None
    text = message.caption if message.photo and message.caption else (message.text if message.text else "Без текста")
    
    pending_appeals[user_id] = {"category": cat_data["category"], "text": text, "photo_id": photo_id}
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Обычное", callback_data="urgency_normal"),
         InlineKeyboardButton(text="🔴 Срочное", callback_data="urgency_urgent")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_send")]
    ])
    await message.answer("⚠️ Насколько срочно требуется решить этот вопрос?", reply_markup=kb)

@router.callback_query(F.data == "cancel_send")
async def cancel_send_callback(callback: CallbackQuery):
    pending_appeals.pop(callback.from_user.id, None)
    try: await callback.message.delete()
    except: pass
    await callback.message.answer("❌ Создание обращения отменено.", reply_markup=employee_keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("urgency_"))
async def ask_anonymity_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_user_blocked(user_id) or user_id not in pending_appeals:
        await callback.answer("Ошибка или доступ ограничен.", show_alert=True)
        return
        
    urgency = "🔴 Срочное" if callback.data == "urgency_urgent" else "🟢 Обычное"
    pending_appeals[user_id]["urgency"] = urgency

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🕵️‍♂️ Отправить анонимно", callback_data="send_anon")],
        [InlineKeyboardButton(text="👤 Указать мое имя", callback_data="send_named")]
    ])
    await callback.message.answer("🔒 Как бы вы хотели отправить обращение?", reply_markup=kb)
    try: await callback.message.delete()
    except: pass
    await callback.answer()

@router.callback_query(F.data.in_({"send_anon", "send_named"}))
async def confirm_send_appeal(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_user_blocked(user_id): return
    data = pending_appeals.pop(user_id, None)
    if not data: return
    
    is_anon = 1 if callback.data == "send_anon" else 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO appeals (user_id, user_name, category, urgency, is_anon, status, created_at) VALUES (?,?,?,?,?,?,?)", 
        (user_id, callback.from_user.full_name, data["category"], data["urgency"], is_anon, 'new', now_str)
    )
    appeal_id = cursor.lastrowid
    
    cursor.execute(
        "INSERT INTO messages_log (appeal_id, sender_id, sender_role, text, photo_id, created_at) VALUES (?,?,?,?,?,?)",
        (appeal_id, user_id, 'employee', data["text"], data["photo_id"], now_str)
    )
    conn.commit()
    conn.close()

    active_dialogues[user_id] = appeal_id

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить в чат", callback_data=f"open_chat_{appeal_id}")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"view_room_{appeal_id}")],
        [InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"close_appeal_{appeal_id}")],
        [InlineKeyboardButton(text="🚫 Заблокировать юзера", callback_data=f"ban_user_{appeal_id}")]
    ])
    
    author_info = "🕵️‍♂️ Анонимный сотрудник" if is_anon else f"👤 {callback.from_user.full_name}"
    for admin_id in load_admins():
        try: 
            msg_text = f"🚨 Новое обращение №{appeal_id} [{data['urgency']}]\n📂 Категория: {data['category']}\nОт: {author_info}\n\n💬 Текст:\n{data['text']}"
            if data["photo_id"]: await bot.send_photo(admin_id, photo=data["photo_id"], caption=msg_text, reply_markup=admin_kb)
            else: await bot.send_message(admin_id, msg_text, reply_markup=admin_kb)
        except: pass

    try: await callback.message.delete()
    except: pass
    
    employee_active_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Вопрос решен (Закрыть тикет)")],
            [KeyboardButton(text="🔙 Возврат в меню")]
        ],
        resize_keyboard=True
    )
    
    await callback.message.answer(
        "✅ Ваше обращение успешно передано поддержке! Мы уже получили ваш вопрос и ответим в ближайшее время.", 
        reply_markup=employee_active_kb
    )
    await callback.answer()

@router.message(F.from_user.id.in_(active_dialogues))
async def employee_continue_dialog(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id): return
    appeal_id = active_dialogues[user_id]
    
    if message.text == "✅ Вопрос решен (Закрыть тикет)":
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE appeals SET status = 'closed' WHERE id = ?", (appeal_id,))
        conn.commit()
        conn.close()
        active_dialogues.pop(user_id, None)

        for admin_id in load_admins():
            try: await bot.send_message(admin_id, f"🔒 Сотрудник закрыл обращение №{appeal_id}.")
            except: pass

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ {i}", callback_data=f"rate_{appeal_id}_{i}") for i in range(1, 6)]
        ])
        await message.answer("🔒 Ваше обращение закрыто. Оцените работу поддержки:", reply_markup=kb)
        return

    photo_id = message.photo[-1].file_id if message.photo else None
    text = message.caption if message.photo and message.caption else (message.text if message.text else "Без текста")
        
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages_log (appeal_id, sender_id, sender_role, text, photo_id, created_at) VALUES (?,?,?,?,?,?)",
                   (appeal_id, user_id, 'employee', text, photo_id, now_str))
    conn.commit()
    conn.close()

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"open_chat_{appeal_id}")],
        [InlineKeyboardButton(text="📜 История", callback_data=f"view_room_{appeal_id}")],
        [InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"close_appeal_{appeal_id}")],
        [InlineKeyboardButton(text="🚫 Заблокировать юзера", callback_data=f"ban_user_{appeal_id}")]
    ])
    for admin_id in load_admins():
        try:
            alert_text = f"📩 Сообщение от сотрудника по тикету №{appeal_id}:\n{text}"
            if photo_id: await bot.send_photo(admin_id, photo=photo_id, caption=alert_text, reply_markup=admin_kb)
            else: await bot.send_message(admin_id, alert_text, reply_markup=admin_kb)
        except: pass

    await message.answer("✅ Сообщение отправлено администраторам.")

@router.callback_query(F.data.startswith("rate_"))
async def save_csat_rating(callback: CallbackQuery):
    parts = callback.data.split("_")
    appeal_id, rating = int(parts[1]), int(parts[2])

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE appeals SET rating = ? WHERE id = ?", (rating, appeal_id))
    conn.commit()
    conn.close()

    try: await callback.message.delete()
    except: pass
    await callback.message.answer(f"Спасибо за вашу оценку ({rating}/5)!", reply_markup=employee_keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("open_chat_"))
async def admin_open_chat(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    appeal_id = int(callback.data.split("_")[2])
    admin_dialogues[callback.from_user.id] = appeal_id
    await callback.message.answer(f"✍️ Режим диалога по тикету №{appeal_id}. Пишите ответы сотруднику (сколько угодно сообщений). Для выхода нажмите кнопку ниже:", reply_markup=cancel_keyboard)
    await callback.answer()

@router.message(F.from_user.id.in_(admin_dialogues))
async def admin_continue_dialog(message: Message):
    admin_id = message.from_user.id
    
    if message.text == "🔙 Возврат в меню":
        admin_dialogues.pop(admin_id, None)
        await message.answer("🛠 Возвращаемся в панель администратора:", reply_markup=admin_keyboard)
        return

    appeal_id = admin_dialogues[admin_id]

    photo_id = message.photo[-1].file_id if message.photo else None
    text = message.caption if message.photo and message.caption else (message.text if message.text else "Без текста")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, status FROM appeals WHERE id = ?", (appeal_id,))
    row = cursor.fetchone()
    
    if not row or row[1] == 'closed':
        await message.answer("⚠️ Тикет не найден или уже закрыт.")
        admin_dialogues.pop(admin_id, None)
        conn.close()
        return
        
    target_user_id = row[0]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO messages_log (appeal_id, sender_id, sender_role, text, photo_id, created_at) VALUES (?,?,?,?,?,?)",
                   (appeal_id, admin_id, 'admin', text, photo_id, now_str))
    conn.commit()
    conn.close()

    try:
        reply_notify = f"📩 Ответ поддержки по тикету №{appeal_id}:\n\n{text}"
        if photo_id: await bot.send_photo(target_user_id, photo=photo_id, caption=reply_notify)
        else: await bot.send_message(target_user_id, reply_notify)
        await message.answer(f"✅ Ответ по тикету №{appeal_id} доставлен. Можете писать следующее сообщение.")
    except:
        await message.answer("⚠️ Не удалось доставить сообщение пользователю.")

@router.callback_query(F.data.startswith("close_appeal_"))
async def admin_close_ticket_cb(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    appeal_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM appeals WHERE id = ?", (appeal_id,))
    row = cursor.fetchone()
    cursor.execute("UPDATE appeals SET status = 'closed' WHERE id = ?", (appeal_id,))
    conn.commit()
    conn.close()

    if row:
        target_user_id = row[0]
        active_dialogues.pop(target_user_id, None)
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"⭐ {i}", callback_data=f"rate_{appeal_id}_{i}") for i in range(1, 6)]
            ])
            await bot.send_message(target_user_id, f"🔒 Администратор закрыл обращение №{appeal_id}.\nОцените качество решения:", reply_markup=kb)
        except: pass

    admin_dialogues.pop(callback.from_user.id, None)
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except: pass
    await callback.message.answer(f"✅ Обращение №{appeal_id} закрыто.")
    await callback.answer()

@router.message(F.text == "📁 Актуальные обращения")
async def show_active_appeals(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT category FROM appeals WHERE status = 'new'")
    cats = cursor.fetchall()
    conn.close()

    if not cats:
        await message.answer("📂 Актуальных обращений нет.")
        return

    kb_list = [[InlineKeyboardButton(text=c[0], callback_data=f"filter_cat_{c[0]}")] for c in cats]
    kb_list.append([InlineKeyboardButton(text="🌐 Показать все", callback_data="filter_cat_ALL")])
    await message.answer("📁 Выберите категорию обращений:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

@router.callback_query(F.data.startswith("filter_cat_"))
async def show_filtered_appeals(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    cat_filter = callback.data.replace("filter_cat_", "")

    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    if cat_filter == "ALL":
        cursor.execute("SELECT id, category, urgency, user_name, is_anon, created_at FROM appeals WHERE status = 'new' ORDER BY id DESC")
    else:
        cursor.execute("SELECT id, category, urgency, user_name, is_anon, created_at FROM appeals WHERE status = 'new' AND category = ? ORDER BY id DESC", (cat_filter,))
    appeals = cursor.fetchall()
    conn.close()

    try: await callback.message.delete()
    except: pass

    if not appeals:
        await callback.message.answer("📂 В выбранной категории нет активных тикетов.")
        return

    for app in appeals:
        app_id, category, urgency, user_name, is_anon, created_at = app
        author = "🕵️‍♂️ Анонимно" if is_anon else f"👤 {user_name}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📜 История переписки", callback_data=f"view_room_{app_id}")],
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"open_chat_{app_id}")],
            [InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"close_appeal_{app_id}")],
            [InlineKeyboardButton(text="🚫 Заблокировать юзера", callback_data=f"ban_user_{app_id}")]
        ])
        await callback.message.answer(f"📌 Обращение №{app_id} [{urgency}]\n📂 {category}\nОт: {author}\n📅 {created_at}", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("view_room_"))
async def view_room_history(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    appeal_id = int(callback.data.split("_")[2])
    
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT category, urgency, status, rating, created_at FROM appeals WHERE id = ?", (appeal_id,))
    app_info = cursor.fetchone()
    cursor.execute("SELECT sender_role, text, photo_id, created_at FROM messages_log WHERE appeal_id = ? ORDER BY id ASC", (appeal_id,))
    messages = cursor.fetchall()
    conn.close()

    if not app_info:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    category, urgency, status, rating, created_at = app_info
    st_text = f"Закрыто (Оценка: {rating}/5)" if status == 'closed' and rating else ("Закрыто" if status == 'closed' else "Активно")
    
    await callback.message.answer(f"📋 **История тикета №{appeal_id} [{urgency}]**\nКатегория: {category}\nСтатус: {st_text}\nСоздано: {created_at}\n-----------------------------------")

    for msg in messages:
        role, text, photo_id, dt = msg
        sender_label = "👤 Сотрудник" if role == 'employee' else "🛠 Администратор"
        msg_body = f"[{dt}] {sender_label}:\n{text}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ответить в тикет", callback_data=f"open_chat_{appeal_id}")],
            [InlineKeyboardButton(text="🚫 Заблокировать автора", callback_data=f"ban_user_{appeal_id}")]
        ]) if status != 'closed' else None

        if photo_id: await callback.message.answer_photo(photo=photo_id, caption=msg_body, reply_markup=kb)
        else: await callback.message.answer(msg_body, reply_markup=kb)
    await callback.answer()

@router.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if message.from_user.id not in load_admins(): return
    conn = sqlite3.connect("bot_database.db")
    cursor = conn.cursor()
    total = cursor.execute("SELECT COUNT(*) FROM appeals").fetchone()[0]
    new_count = cursor.execute("SELECT COUNT(*) FROM appeals WHERE status = 'new'").fetchone()[0]
    polls_count = cursor.execute("SELECT COUNT(*) FROM weekly_polls").fetchone()[0]
    users_count = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    blocked_count = cursor.execute("SELECT COUNT(*) FROM blocked_users").fetchone()[0]
    avg_rating = cursor.execute("SELECT AVG(rating) FROM appeals WHERE rating IS NOT NULL").fetchone()[0]
    conn.close()
    
    avg_str = f"{avg_rating:.1f} ⭐" if avg_rating else "Нет оценок"
    await message.answer(f"📊 **Общая статистика:**\n\n• Пользователей: {users_count}\n• В черном списке: {blocked_count}\n• Всего тикетов: {total}\n• Активных: {new_count}\n• Ответов на опрос: {polls_count}\n• Средний CSAT: {avg_str}")

@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("👥 Управление администраторами:", reply_markup=manage_admins_keyboard)

@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if message.from_user.id not in load_admins(): return
    admins = load_admins()
    await message.answer(f"📋 Список ID администраторов:\n" + "\n".join(map(str, admins)))

@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    if message.from_user.id not in load_admins(): return
    adding_admin_process.add(message.from_user.id)
    await message.answer("➕ Введите Telegram ID нового администратора:", reply_markup=cancel_keyboard)

@router.message(F.text.isdigit() & F.from_user.id.in_(adding_admin_process))
async def add_admin_action(message: Message):
    user_id = message.from_user.id
    adding_admin_process.remove(user_id)
    new_admin_id = int(message.text)
    
    admins = load_admins()
    admins.add(new_admin_id)
    save_admins(admins)
    await message.answer(f"✅ Администратор с ID {new_admin_id} добавлен!", reply_markup=manage_admins_keyboard)

@router.message(F.text == "➖ Удалить админа")
async def ask_remove_admin(message: Message):
    if message.from_user.id not in load_admins(): return
    removing_admin_process.add(message.from_user.id)
    await message.answer("➖ Введите Telegram ID администратора для удаления:", reply_markup=cancel_keyboard)

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
    asyncio.create_task(auto_close_inactive_tickets())
    asyncio.create_task(schedule_weekly_polls())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
