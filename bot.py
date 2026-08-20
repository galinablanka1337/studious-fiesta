from __future__ import annotations

import asyncio
import csv
import html
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ============================================================
# CONFIG
# ============================================================
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Не найден BOT_TOKEN!")

DB_PATH = os.getenv("BOT_DB_PATH", "bot_database.db")
ADMINS_FILE = os.getenv("ADMINS_FILE", "admins.txt")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "762076580"))
TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Europe/Moscow")
try:
    BOT_TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    BOT_TZ = ZoneInfo("Europe/Moscow")

# Пути к приветственным изображениям. При необходимости их можно
# переопределить переменными окружения GREETING_MORNING/DAY/EVENING/NIGHT.
GREETING_FILES = {
    # Новые картинки приветствия:
    # morning — «Доброе утро», day — «Добрый день»,
    # evening — «Добрый вечер», night — «Доброй ночи».
    "morning": os.getenv("GREETING_MORNING", "image_morning.jpg"),
    "day": os.getenv("GREETING_DAY", "image_day.jpg"),
    "evening": os.getenv("GREETING_EVENING", "image_evening.jpg"),
    "night": os.getenv("GREETING_NIGHT", "image_night.jpg"),
}

CATEGORIES = [
    "✍️ Анонимное обращение",
    "💡 Новые идеи",
    "⚠️ Жалобы",
    "⚔️ Конфликт",
    "🤝 Помощь сотруднику",
    "💬 Другие вопросы",
    "🌴 Отпуска",
    "🏥 Больничные",
    "💰 Зарплата",
    "📅 График",
]
CATEGORY_BY_INDEX = {str(i): value for i, value in enumerate(CATEGORIES)}

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()

# Runtime state. Ticket status itself is stored in SQLite, so buttons continue
# to work after a restart even though these temporary compose states are reset.
compose_user: dict[int, int] = {}
compose_admin: dict[int, int] = {}
new_appeal_state: dict[int, dict] = {}
admin_process: dict[int, str] = {}
period_state: dict[int, str] = {}

# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def now_str() -> str:
    return datetime.now(BOT_TZ).strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            category TEXT NOT NULL,
            urgency TEXT DEFAULT '🟢 Обычное',
            is_anon INTEGER DEFAULT 1,
            status TEXT DEFAULT 'waiting_admin',
            rating INTEGER,
            created_at TEXT NOT NULL,
            closed_at TEXT,
            last_activity_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appeal_id INTEGER NOT NULL,
            sender_id INTEGER,
            sender_role TEXT NOT NULL,
            text TEXT,
            photo_id TEXT,
            video_id TEXT,
            media_type TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(appeal_id) REFERENCES appeals(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mood_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            score INTEGER,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            score INTEGER,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            joined_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id INTEGER PRIMARY KEY,
            blocked_at TEXT
        )
        """
    )

    # Safe migrations for the old database shipped with the original bot.
    appeal_cols = {row[1] for row in cur.execute("PRAGMA table_info(appeals)")}
    for name, sql in [
        ("closed_at", "ALTER TABLE appeals ADD COLUMN closed_at TEXT"),
        ("last_activity_at", "ALTER TABLE appeals ADD COLUMN last_activity_at TEXT"),
    ]:
        if name not in appeal_cols:
            cur.execute(sql)

    msg_cols = {row[1] for row in cur.execute("PRAGMA table_info(messages_log)")}
    for name, sql in [
        ("video_id", "ALTER TABLE messages_log ADD COLUMN video_id TEXT"),
        ("media_type", "ALTER TABLE messages_log ADD COLUMN media_type TEXT"),
    ]:
        if name not in msg_cols:
            cur.execute(sql)

    # Convert old photo-only records into the new media model.
    cur.execute(
        "UPDATE messages_log SET media_type='photo' WHERE media_type IS NULL AND photo_id IS NOT NULL"
    )
    cur.execute(
        "UPDATE messages_log SET media_type='text' "
        "WHERE media_type IS NULL AND photo_id IS NULL AND video_id IS NULL"
    )
    cur.execute(
        "UPDATE appeals SET status='waiting_admin' WHERE status='new'"
    )
    cur.execute(
        "UPDATE appeals SET last_activity_at=created_at "
        "WHERE last_activity_at IS NULL"
    )
    conn.commit()
    conn.close()


init_db()


def is_user_blocked(user_id: int) -> bool:
    conn = db()
    row = conn.execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def register_user(user_id: int, user_name: str) -> None:
    if is_user_blocked(user_id):
        return
    conn = db()
    conn.execute(
        """
        INSERT INTO users(user_id,user_name,joined_at) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET user_name=excluded.user_name
        """,
        (user_id, user_name, now_str()),
    )
    conn.commit()
    conn.close()


def load_admins() -> set[int]:
    admins = {SUPER_ADMIN_ID}
    path = Path(ADMINS_FILE)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().isdigit():
                admins.add(int(line.strip()))
    return admins


def save_admins(admins: set[int]) -> None:
    Path(ADMINS_FILE).write_text(
        "".join(f"{admin_id}\n" for admin_id in sorted(admins)),
        encoding="utf-8",
    )


def get_appeal(appeal_id: int):
    conn = db()
    row = conn.execute(
        """
        SELECT id,user_id,user_name,category,urgency,is_anon,status,rating,
               created_at,closed_at,last_activity_at
        FROM appeals WHERE id=?
        """,
        (appeal_id,),
    ).fetchone()
    conn.close()
    return row


def active_appeal_for_user(user_id: int) -> Optional[int]:
    conn = db()
    row = conn.execute(
        "SELECT id FROM appeals WHERE user_id=? AND status!='closed' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def insert_message(
    appeal_id: int,
    sender_id: int,
    sender_role: str,
    text: str,
    media_type: Optional[str],
    media_id: Optional[str],
) -> None:
    conn = db()
    photo_id = media_id if media_type == "photo" else None
    video_id = media_id if media_type == "video" else None
    ts = now_str()
    conn.execute(
        """
        INSERT INTO messages_log
        (appeal_id,sender_id,sender_role,text,photo_id,video_id,media_type,created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (appeal_id, sender_id, sender_role, text, photo_id, video_id, media_type, ts),
    )
    conn.execute(
        "UPDATE appeals SET last_activity_at=? WHERE id=?", (ts, appeal_id)
    )
    conn.commit()
    conn.close()


def set_status(appeal_id: int, status: str) -> None:
    conn = db()
    if status == "closed":
        conn.execute(
            "UPDATE appeals SET status=?,closed_at=?,last_activity_at=? WHERE id=?",
            (status, now_str(), now_str(), appeal_id),
        )
    else:
        conn.execute(
            "UPDATE appeals SET status=?,last_activity_at=? WHERE id=?",
            (status, now_str(), appeal_id),
        )
    conn.commit()
    conn.close()


def extract_content(message: Message):
    """Return (media_type, media_id, text) or None for forbidden content."""
    if message.text is not None:
        return "text", None, message.text.strip()
    if message.photo:
        return "photo", message.photo[-1].file_id, (message.caption or "").strip()
    if message.video:
        return "video", message.video.file_id, (message.caption or "").strip()
    return None


def is_supported_content(message: Message) -> bool:
    return extract_content(message) is not None


# ============================================================
# KEYBOARDS
# ============================================================
employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✍️ Анонимное обращение"), KeyboardButton(text="💡 Новые идеи")],
        [KeyboardButton(text="⚠️ Жалобы"), KeyboardButton(text="⚔️ Конфликт")],
        [KeyboardButton(text="🤝 Помощь сотруднику"), KeyboardButton(text="💬 Другие вопросы")],
        [KeyboardButton(text="🌴 Отпуска"), KeyboardButton(text="🏥 Больничные")],
        [KeyboardButton(text="💰 Зарплата"), KeyboardButton(text="📅 График")],
        [KeyboardButton(text="🌱 Как ваше настроение?")],
    ],
    resize_keyboard=True,
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📁 Актуальные обращения"), KeyboardButton(text="📚 История обращений")],
        [KeyboardButton(text="📊 Статистика бота"), KeyboardButton(text="📊 Отчет: Настроение")],
        [KeyboardButton(text="📈 Отчет: Частые вопросы"), KeyboardButton(text="📊 Отчет: Опрос за неделю")],
        [KeyboardButton(text="📥 Выгрузить отчет за период"), KeyboardButton(text="📥 Выгрузить обращения (CSV)")],
        [KeyboardButton(text="👥 Список участников"), KeyboardButton(text="👥 Управление админами")],
        [KeyboardButton(text="🚫 Черный список"), KeyboardButton(text="📢 Сделать рассылку")],
    ],
    resize_keyboard=True,
)

manage_admins_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить админа"), KeyboardButton(text="➖ Удалить админа")],
        [KeyboardButton(text="📋 Список админов"), KeyboardButton(text="🔙 В панель администратора")],
    ],
    resize_keyboard=True,
)

cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Возврат в меню")]], resize_keyboard=True
)


def employee_ticket_keyboard(appeal_id: int, can_reply: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_reply:
        rows.append([InlineKeyboardButton(text="✍️ Ответить поддержке", callback_data=f"u_reply:{appeal_id}")])
    rows.append([InlineKeyboardButton(text="🔒 Закрыть обращение", callback_data=f"u_close:{appeal_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ticket_keyboard(appeal_id: int, can_reply: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_reply:
        rows.append([InlineKeyboardButton(text="💬 Ответить", callback_data=f"a_reply:{appeal_id}")])
    rows.append([InlineKeyboardButton(text="📜 История", callback_data=f"history:{appeal_id}")])
    rows.append([InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"a_close:{appeal_id}")])
    rows.append([InlineKeyboardButton(text="🚫 Заблокировать автора", callback_data=f"ban:{appeal_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rating_keyboard(appeal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"⭐ {i}", callback_data=f"rate:{appeal_id}:{i}")
                for i in range(1, 6)
            ]
        ]
    )


# ============================================================
# GREETINGS
# ============================================================

def greeting_data():
    hour = datetime.now(BOT_TZ).hour
    if 5 <= hour < 12:
        return "Доброе утро! ☀️", "morning"
    if 12 <= hour < 17:
        return "Добрый день! 🍏", "day"
    if 17 <= hour < 23:
        return "Добрый вечер! 🌆", "evening"
    return "Доброй ночи! 🌙", "night"


async def send_start_screen(message: Message) -> None:
    greeting, period = greeting_data()
    photo_file = GREETING_FILES[period]
    is_admin = message.from_user.id in load_admins()
    if is_admin:
        text = (
            f"{greeting}\n\n"
            "🛠 <b>Панель администратора</b>\n\n"
            "👇 Выберите нужный раздел ниже:"
        )
        keyboard = admin_keyboard
    else:
        text = (
            f"{greeting} 👋 Рады приветствовать вас!\n\n"
            "🔒 Здесь можно безопасно обратиться в поддержку, отправить текст, фото или видео.\n\n"
            "👇 Выберите нужный раздел ниже:"
        )
        keyboard = employee_keyboard

    if Path(photo_file).exists():
        await message.answer_photo(FSInputFile(photo_file), caption=text, reply_markup=keyboard)
    else:
        logging.warning("Файл приветствия не найден: %s", photo_file)
        await message.answer(text, reply_markup=keyboard)


# ============================================================
# START / NAVIGATION
# ============================================================
@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer("❌ Доступ к сервису ограничен администрацией.")
        return
    register_user(user_id, message.from_user.full_name)
    compose_user.pop(user_id, None)
    compose_admin.pop(user_id, None)
    new_appeal_state.pop(user_id, None)
    admin_process.pop(user_id, None)
    await send_start_screen(message)


@router.message(F.text == "🔙 В панель администратора")
async def back_to_admin(message: Message):
    if message.from_user.id in load_admins():
        compose_admin.pop(message.from_user.id, None)
        admin_process.pop(message.from_user.id, None)
        await message.answer("🛠 Панель администратора:", reply_markup=admin_keyboard)


@router.message(F.text == "🔙 Возврат в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    compose_user.pop(user_id, None)
    compose_admin.pop(user_id, None)
    new_appeal_state.pop(user_id, None)
    admin_process.pop(user_id, None)
    period_state.pop(user_id, None)
    if user_id in load_admins():
        await message.answer("🛠 Возвращаемся в панель администратора:", reply_markup=admin_keyboard)
    else:
        await message.answer("🏠 Главное меню сотрудника:", reply_markup=employee_keyboard)


# ============================================================
# BLACKLIST
# ============================================================
@router.message(F.text == "🚫 Черный список")
async def black_list_menu(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    blocked = conn.execute(
        "SELECT user_id,blocked_at FROM blocked_users ORDER BY blocked_at DESC"
    ).fetchall()
    conn.close()
    text = "🚫 <b>Черный список</b>\n\n"
    if blocked:
        text += "\n".join(f"{i}. <code>{uid}</code> — с {dt}" for i, (uid, dt) in enumerate(blocked, 1))
    else:
        text += "Список пуст."
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Заблокировать по ID", callback_data="block_prompt")],
            [InlineKeyboardButton(text="➖ Разблокировать по ID", callback_data="unblock_prompt")],
        ]
    )
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "block_prompt")
async def block_prompt(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    admin_process[callback.from_user.id] = "block"
    await callback.message.answer("🚫 Введите Telegram ID пользователя для блокировки:", reply_markup=cancel_keyboard)
    await callback.answer()


@router.callback_query(F.data == "unblock_prompt")
async def unblock_prompt(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    admin_process[callback.from_user.id] = "unblock"
    await callback.message.answer("🔓 Введите Telegram ID пользователя для разблокировки:", reply_markup=cancel_keyboard)
    await callback.answer()


@router.message(F.text.regexp(r"^\d+$"))
async def admin_numeric_process(message: Message):
    admin_id = message.from_user.id
    action = admin_process.get(admin_id)
    if action not in {"block", "unblock", "add_admin", "remove_admin"}:
        return
    target_id = int(message.text)
    admin_process.pop(admin_id, None)

    if action == "block":
        if target_id == SUPER_ADMIN_ID or target_id in load_admins():
            await message.answer("❌ Нельзя заблокировать администратора системы.", reply_markup=admin_keyboard)
            return
        conn = db()
        conn.execute(
            "INSERT OR IGNORE INTO blocked_users(user_id,blocked_at) VALUES(?,?)",
            (target_id, now_str()),
        )
        conn.commit()
        conn.close()
        compose_user.pop(target_id, None)
        await message.answer(f"✅ Пользователь <code>{target_id}</code> заблокирован.", reply_markup=admin_keyboard)
        try:
            await bot.send_message(target_id, "❌ Ваш доступ к сервису поддержки ограничен администрацией.")
        except Exception:
            pass
        return

    if action == "unblock":
        conn = db()
        conn.execute("DELETE FROM blocked_users WHERE user_id=?", (target_id,))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Пользователь <code>{target_id}</code> разблокирован.", reply_markup=admin_keyboard)
        try:
            await bot.send_message(target_id, "✅ Ваш доступ к сервису поддержки восстановлен.")
        except Exception:
            pass
        return

    if action == "add_admin":
        admins = load_admins()
        admins.add(target_id)
        save_admins(admins)
        await message.answer(f"✅ Администратор <code>{target_id}</code> добавлен.", reply_markup=manage_admins_keyboard)
        return

    if action == "remove_admin":
        if target_id == SUPER_ADMIN_ID:
            await message.answer("❌ Нельзя удалить главного администратора.", reply_markup=manage_admins_keyboard)
            return
        admins = load_admins()
        if target_id in admins:
            admins.remove(target_id)
            save_admins(admins)
            await message.answer(f"✅ Администратор <code>{target_id}</code> удален.", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Такого администратора нет.", reply_markup=manage_admins_keyboard)


@router.callback_query(F.data.startswith("ban:"))
async def ban_user_from_ticket(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    target_id = row[1]
    if target_id in load_admins():
        await callback.answer("Нельзя заблокировать администратора", show_alert=True)
        return
    conn = db()
    conn.execute("INSERT OR IGNORE INTO blocked_users(user_id,blocked_at) VALUES(?,?)", (target_id, now_str()))
    conn.execute("UPDATE appeals SET status='closed',closed_at=?,last_activity_at=? WHERE id=?", (now_str(), now_str(), appeal_id))
    conn.commit()
    conn.close()
    compose_user.pop(target_id, None)
    compose_admin.pop(callback.from_user.id, None)
    try:
        await bot.send_message(target_id, "❌ Вы были заблокированы администрацией.")
    except Exception:
        pass
    await callback.message.answer(f"🚫 Пользователь тикета №{appeal_id} заблокирован, обращение закрыто.")
    await callback.answer()


# ============================================================
# REPORTS / EXPORTS
# ============================================================
async def send_csv(message: Message, headers, rows, prefix: str, caption: str):
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".csv")
    os.close(fd)
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(headers)
            writer.writerows(rows)
        await message.answer_document(FSInputFile(path), caption=caption)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@router.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM appeals").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM appeals WHERE status!='closed'").fetchone()[0]
    closed = conn.execute("SELECT COUNT(*) FROM appeals WHERE status='closed'").fetchone()[0]
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM blocked_users").fetchone()[0]
    mood = conn.execute("SELECT COUNT(*) FROM mood_logs").fetchone()[0]
    polls = conn.execute("SELECT COUNT(*) FROM weekly_polls").fetchone()[0]
    avg = conn.execute("SELECT AVG(rating) FROM appeals WHERE rating IS NOT NULL").fetchone()[0]
    conn.close()
    avg_text = f"{avg:.2f}/5 ⭐" if avg is not None else "нет оценок"
    await message.answer(
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"🚫 В черном списке: <b>{blocked}</b>\n"
        f"📨 Всего обращений: <b>{total}</b>\n"
        f"🟢 Активных: <b>{active}</b>\n"
        f"🔒 Закрытых: <b>{closed}</b>\n"
        f"⭐ Средний CSAT: <b>{avg_text}</b>\n"
        f"🌱 Оценок настроения: <b>{mood}</b>\n"
        f"📊 Ответов недельного опроса: <b>{polls}</b>"
    )


@router.message(F.text == "📊 Отчет: Настроение")
async def report_mood(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    avg, total = conn.execute("SELECT AVG(score),COUNT(*) FROM mood_logs").fetchone()
    rows = conn.execute(
        "SELECT user_name,score,created_at FROM mood_logs ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not total:
        await message.answer("📊 Отчет по настроению пока пуст.")
        return
    body = "\n".join(f"• {html.escape(name or 'Без имени')} — {score}/10 — {dt}" for name, score, dt in rows)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 CSV отчета", callback_data="reportcsv:mood")]])
    await message.answer(
        f"📊 <b>Отчет: Настроение</b>\n\n"
        f"Оценок: <b>{total}</b>\nСредний балл: <b>{avg:.1f}/10</b>\n\n{body}",
        reply_markup=kb,
    )


@router.message(F.text == "📈 Отчет: Частые вопросы")
async def report_frequent_questions(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    rows = conn.execute(
        "SELECT category,COUNT(*) cnt FROM appeals GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    if not rows:
        await message.answer("📈 Пока нет данных по обращениям.")
        return
    text = "📈 <b>Отчет: Частые вопросы</b>\n\n" + "\n".join(
        f"{i}. {html.escape(cat)} — <b>{count}</b>" for i, (cat, count) in enumerate(rows, 1)
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 CSV отчета", callback_data="reportcsv:frequent")]])
    await message.answer(text, reply_markup=kb)


@router.message(F.text == "📊 Отчет: Опрос за неделю")
async def report_weekly_poll(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    avg, total = conn.execute("SELECT AVG(score),COUNT(*) FROM weekly_polls").fetchone()
    rows = conn.execute(
        "SELECT user_name,score,created_at FROM weekly_polls ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not total:
        await message.answer("📊 Еженедельный опрос пока пуст.")
        return
    body = "\n".join(f"• {html.escape(name or 'Без имени')} — {score}/10 — {dt}" for name, score, dt in rows)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 CSV отчета", callback_data="reportcsv:weekly")]])
    await message.answer(
        f"📊 <b>Отчет: Опрос за неделю</b>\n\n"
        f"Ответов: <b>{total}</b>\nСредний балл: <b>{avg:.1f}/10</b>\n\n{body}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("reportcsv:"))
async def report_csv(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    kind = callback.data.split(":", 1)[1]
    conn = db()
    if kind == "mood":
        rows = conn.execute("SELECT user_name,score,created_at FROM mood_logs ORDER BY id DESC").fetchall()
        headers = ["Имя", "Оценка", "Дата"]
        prefix = "mood_"
        caption = "📥 CSV отчета по настроению."
    elif kind == "weekly":
        rows = conn.execute("SELECT user_name,score,created_at FROM weekly_polls ORDER BY id DESC").fetchall()
        headers = ["Имя", "Оценка недели", "Дата"]
        prefix = "weekly_"
        caption = "📥 CSV отчета по еженедельному опросу."
    else:
        rows = conn.execute("SELECT category,COUNT(*) FROM appeals GROUP BY category ORDER BY COUNT(*) DESC").fetchall()
        headers = ["Категория", "Количество обращений"]
        prefix = "frequent_"
        caption = "📥 CSV отчета по частым вопросам."
    conn.close()
    await send_csv(callback.message, headers, rows, prefix, caption)
    await callback.answer("CSV готов")


@router.message(F.text == "📥 Выгрузить обращения (CSV)")
async def export_appeals_csv(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    rows = conn.execute(
        """
        SELECT id,user_name,category,urgency,is_anon,status,rating,created_at,closed_at,last_activity_at
        FROM appeals ORDER BY id DESC
        """
    ).fetchall()
    conn.close()
    await send_csv(
        message,
        ["ID","Имя","Категория","Срочность","Анонимно","Статус","CSAT","Создано","Закрыто","Последняя активность"],
        [
            [rid, "" if anon else name, cat, urg, "Да" if anon else "Нет", "Закрыто" if status == "closed" else "Активно", rating or "", created, closed or "", last or ""]
            for rid, name, cat, urg, anon, status, rating, created, closed, last in rows
        ],
        "appeals_",
        "📥 Полная выгрузка обращений CSV.",
    )


@router.message(F.text == "📥 Выгрузить отчет за период")
async def period_export_menu(message: Message):
    if message.from_user.id not in load_admins():
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="7 дней", callback_data="period:7"), InlineKeyboardButton(text="30 дней", callback_data="period:30")],
            [InlineKeyboardButton(text="90 дней", callback_data="period:90"), InlineKeyboardButton(text="Все время", callback_data="period:all")],
        ]
    )
    await message.answer("📥 Выберите период выгрузки:", reply_markup=kb)


@router.callback_query(F.data.startswith("period:"))
async def period_export(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    value = callback.data.split(":", 1)[1]
    since = None if value == "all" else datetime.now(BOT_TZ) - timedelta(days=int(value))
    since_str = since.strftime("%Y-%m-%d %H:%M:%S") if since else None

    conn = db()
    if since_str:
        appeals = conn.execute(
            "SELECT id,user_name,category,urgency,is_anon,status,rating,created_at,closed_at FROM appeals WHERE created_at>=? ORDER BY id DESC",
            (since_str,),
        ).fetchall()
        moods = conn.execute(
            "SELECT id,user_name,score,created_at FROM mood_logs WHERE created_at>=? ORDER BY id DESC",
            (since_str,),
        ).fetchall()
        polls = conn.execute(
            "SELECT id,user_name,score,created_at FROM weekly_polls WHERE created_at>=? ORDER BY id DESC",
            (since_str,),
        ).fetchall()
    else:
        appeals = conn.execute("SELECT id,user_name,category,urgency,is_anon,status,rating,created_at,closed_at FROM appeals ORDER BY id DESC").fetchall()
        moods = conn.execute("SELECT id,user_name,score,created_at FROM mood_logs ORDER BY id DESC").fetchall()
        polls = conn.execute("SELECT id,user_name,score,created_at FROM weekly_polls ORDER BY id DESC").fetchall()
    conn.close()

    rows = []
    # Anonymous appeals never expose the employee name in exports.
    for r in appeals:
        rows.append(["Обращение", r[0], "" if r[4] else r[1], r[2], r[3], "Закрыто" if r[5] == "closed" else "Активно", r[6] or "", r[7], r[8] or ""])
    for r in moods:
        rows.append(["Настроение", r[0], r[1], "", "", f"{r[2]}/10", "", r[3], ""])
    for r in polls:
        rows.append(["Опрос за неделю", r[0], r[1], "", "", f"{r[2]}/10", "", r[3], ""])

    period_label = "все время" if value == "all" else f"последние {value} дней"
    await send_csv(
        callback.message,
        ["Тип","ID","Имя","Категория","Срочность","Статус/оценка","CSAT","Дата","Закрыто"],
        rows,
        "period_",
        f"📥 Сводная выгрузка за {period_label}.",
    )
    await callback.answer("Выгрузка готова")


@router.message(F.text == "👥 Список участников")
async def show_bot_users(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    users = conn.execute("SELECT user_id,user_name,joined_at FROM users ORDER BY joined_at DESC").fetchall()
    conn.close()
    if not users:
        await message.answer("👥 В базе пока нет участников.")
        return
    # Do not flood the chat: first 50 users plus a CSV button.
    text = f"👥 <b>Список участников: {len(users)}</b>\n\n"
    for i, (uid, name, joined) in enumerate(users[:50], 1):
        text += f"{i}. {html.escape(name or 'Без имени')} — <code>{uid}</code> — {joined}\n"
    if len(users) > 50:
        text += f"\n… еще {len(users)-50}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 CSV участников", callback_data="users_csv")]])
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "users_csv")
async def users_csv(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    conn = db()
    rows = conn.execute("SELECT user_id,user_name,joined_at FROM users ORDER BY joined_at DESC").fetchall()
    conn.close()
    await send_csv(callback.message, ["Telegram ID","Имя","Дата регистрации"], rows, "users_", "📥 Выгрузка участников.")
    await callback.answer("Готово")


# ============================================================
# ADMIN MANAGEMENT
# ============================================================
@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("👥 Управление администраторами:", reply_markup=manage_admins_keyboard)


@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if message.from_user.id not in load_admins():
        return
    await message.answer("📋 <b>Администраторы:</b>\n" + "\n".join(f"• <code>{x}</code>" for x in sorted(load_admins())))


@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    if message.from_user.id not in load_admins():
        return
    admin_process[message.from_user.id] = "add_admin"
    await message.answer("➕ Введите Telegram ID нового администратора:", reply_markup=cancel_keyboard)


@router.message(F.text == "➖ Удалить админа")
async def ask_remove_admin(message: Message):
    if message.from_user.id not in load_admins():
        return
    admin_process[message.from_user.id] = "remove_admin"
    await message.answer("➖ Введите Telegram ID администратора для удаления:", reply_markup=cancel_keyboard)


# ============================================================
# MOOD / WEEKLY POLL
# ============================================================
@router.message(F.text == "🌱 Как ваше настроение?")
async def ask_mood(message: Message):
    if is_user_blocked(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(i), callback_data=f"mood:{i}") for i in range(1, 6)],
            [InlineKeyboardButton(text=str(i), callback_data=f"mood:{i}") for i in range(6, 11)],
        ]
    )
    await message.answer("🌱 Как вы себя чувствуете сегодня? Оцените от 1 до 10:", reply_markup=kb)


@router.callback_query(F.data.startswith("mood:"))
async def save_mood(callback: CallbackQuery):
    if is_user_blocked(callback.from_user.id):
        return
    score = int(callback.data.split(":", 1)[1])
    conn = db()
    conn.execute(
        "INSERT INTO mood_logs(user_id,user_name,score,created_at) VALUES(?,?,?,?)",
        (callback.from_user.id, callback.from_user.full_name, score, now_str()),
    )
    conn.commit()
    conn.close()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(f"Спасибо! Ваша оценка настроения: {score}/10.", reply_markup=employee_keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("w_poll:"))
async def save_weekly_poll(callback: CallbackQuery):
    if is_user_blocked(callback.from_user.id):
        return
    score = int(callback.data.split(":", 1)[1])
    conn = db()
    conn.execute(
        "INSERT INTO weekly_polls(user_id,user_name,score,created_at) VALUES(?,?,?,?)",
        (callback.from_user.id, callback.from_user.full_name, score, now_str()),
    )
    conn.commit()
    conn.close()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(f"✅ Спасибо! Оценка за неделю: {score}/10.", reply_markup=employee_keyboard)
    await callback.answer()


# ============================================================
# APPEAL CREATION
# ============================================================
@router.message(F.text.in_(CATEGORIES))
async def select_category(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        return
    active = active_appeal_for_user(user_id)
    if active:
        await message.answer(
            f"⚠️ У вас уже есть открытое обращение №{active}. Сначала продолжите его или закройте.",
            reply_markup=employee_ticket_keyboard(active, can_reply=True),
        )
        return
    new_appeal_state[user_id] = {
        "category": message.text,
        "force_anonymous": message.text == "✍️ Анонимное обращение",
    }
    await message.answer(
        f"✍️ <b>Категория:</b> {html.escape(message.text)}\n\n"
        "Опишите вопрос подробно. Можно отправить <b>текст, фото или видео</b>.",
        reply_markup=cancel_keyboard,
    )


@router.callback_query(F.data.startswith("urgency:"))
async def choose_urgency(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_user_blocked(user_id) or user_id not in new_appeal_state:
        await callback.answer("Сессия обращения устарела", show_alert=True)
        return
    new_appeal_state[user_id]["urgency"] = "🔴 Срочное" if callback.data.endswith("urgent") else "🟢 Обычное"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕵️ Отправить анонимно", callback_data="anon:1")],
            [InlineKeyboardButton(text="👤 Указать мое имя", callback_data="anon:0")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_new")],
        ]
    )
    await callback.message.answer("🔒 Как отправить обращение?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "cancel_new")
async def cancel_new(callback: CallbackQuery):
    new_appeal_state.pop(callback.from_user.id, None)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("❌ Создание обращения отменено.", reply_markup=employee_keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("anon:"))
async def confirm_send_appeal(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        return
    data = new_appeal_state.pop(user_id, None)
    if not data:
        await callback.answer("Сессия обращения устарела", show_alert=True)
        return
    requested_anon = int(callback.data.split(":", 1)[1])
    is_anon = 1 if data.get("force_anonymous") else requested_anon
    ts = now_str()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO appeals(user_id,user_name,category,urgency,is_anon,status,created_at,last_activity_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (user_id, callback.from_user.full_name, data["category"], data["urgency"], is_anon, "waiting_admin", ts, ts),
    )
    appeal_id = cur.lastrowid
    media_type = data["media_type"]
    media_id = data["media_id"]
    cur.execute(
        """
        INSERT INTO messages_log(appeal_id,sender_id,sender_role,text,photo_id,video_id,media_type,created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            appeal_id, user_id, "employee", data["text"],
            media_id if media_type == "photo" else None,
            media_id if media_type == "video" else None,
            media_type, ts,
        ),
    )
    conn.commit()
    conn.close()

    author = "🕵️ Анонимный сотрудник" if is_anon else f"👤 {html.escape(callback.from_user.full_name)}"
    admin_text = (
        f"🚨 <b>Новое обращение №{appeal_id}</b> [{data['urgency']}]\n"
        f"📂 {html.escape(data['category'])}\n"
        f"От: {author}\n\n"
        f"💬 {html.escape(data['text'] or 'Без текста')}"
    )
    for admin_id in load_admins():
        try:
            await send_content_to_chat(
                admin_id, data["media_type"], data["media_id"], admin_text,
                admin_ticket_keyboard(appeal_id, can_reply=True),
            )
        except Exception:
            logging.exception("Не удалось уведомить администратора %s", admin_id)

    await callback.message.answer(
        f"✅ Обращение №{appeal_id} передано поддержке.\n\n"
        "После ответа поддержки здесь появится кнопка <b>«Ответить поддержке»</b>. "
        "Так диалог продолжается по очереди и не закрывается после одного ответа.",
        reply_markup=employee_ticket_keyboard(appeal_id, can_reply=False),
    )
    await callback.answer()




# ============================================================
# DIALOG BUTTONS
# ============================================================
@router.callback_query(F.data.startswith("u_reply:"))
async def employee_reply_button(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row or row[1] != user_id or row[6] == "closed":
        await callback.answer("Обращение недоступно", show_alert=True)
        return
    compose_user[user_id] = appeal_id
    await callback.message.answer(
        f"✍️ <b>Тикет №{appeal_id}</b>\nОтправьте одно сообщение: текст, фото или видео.",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("a_reply:"))
async def admin_reply_button(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row or row[6] == "closed":
        await callback.answer("Тикет закрыт", show_alert=True)
        return
    compose_admin[callback.from_user.id] = appeal_id
    await callback.message.answer(
        f"✍️ <b>Диалог по тикету №{appeal_id}</b>\nОтправьте одно сообщение: текст, фото или видео.",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("u_close:"))
async def employee_close(callback: CallbackQuery):
    user_id = callback.from_user.id
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row or row[1] != user_id:
        await callback.answer("Обращение недоступно", show_alert=True)
        return
    if row[6] == "closed":
        await callback.answer("Уже закрыто")
        return
    set_status(appeal_id, "closed")
    compose_user.pop(user_id, None)
    await callback.message.answer("🔒 Обращение закрыто. Оцените качество работы поддержки:", reply_markup=rating_keyboard(appeal_id))
    for admin_id in load_admins():
        try:
            await bot.send_message(admin_id, f"🔒 Сотрудник закрыл обращение №{appeal_id}.")
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("a_close:"))
async def admin_close(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    set_status(appeal_id, "closed")
    compose_admin.pop(callback.from_user.id, None)
    target_user_id = row[1]
    compose_user.pop(target_user_id, None)
    try:
        await bot.send_message(
            target_user_id,
            f"🔒 Администратор закрыл обращение №{appeal_id}.\nОцените качество решения:",
            reply_markup=rating_keyboard(appeal_id),
        )
    except Exception:
        pass
    await callback.message.answer(f"✅ Обращение №{appeal_id} закрыто.")
    await callback.answer()


@router.callback_query(F.data.startswith("rate:"))
async def save_csat_rating(callback: CallbackQuery):
    _, appeal_s, rating_s = callback.data.split(":")
    appeal_id, rating = int(appeal_s), int(rating_s)
    row = get_appeal(appeal_id)
    if not row or row[1] != callback.from_user.id or row[6] != "closed":
        await callback.answer("Оценка недоступна", show_alert=True)
        return
    if row[7] is not None:
        await callback.answer("Оценка уже сохранена")
        return
    conn = db()
    conn.execute("UPDATE appeals SET rating=? WHERE id=?", (rating, appeal_id))
    conn.commit()
    conn.close()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(f"Спасибо за оценку: {rating}/5 ⭐", reply_markup=employee_keyboard)
    await callback.answer()


# ============================================================
# ACTIVE APPEALS
# ============================================================
@router.message(F.text == "📁 Актуальные обращения")
async def show_active_appeals(message: Message):
    if message.from_user.id not in load_admins():
        return
    conn = db()
    cats = conn.execute("SELECT DISTINCT category FROM appeals WHERE status!='closed' ORDER BY category").fetchall()
    conn.close()
    kb = []
    for idx, cat in enumerate([r[0] for r in cats]):
        # Use a numeric index to keep callback_data short and safe.
        kb.append([InlineKeyboardButton(text=cat, callback_data=f"activecat:{idx}")])
    if cats:
        kb.append([InlineKeyboardButton(text="🌐 Показать все", callback_data="activecat:all")])
        # Store category list in callback-independent global is not safe across restarts;
        # instead use a callback with URL-safe base64-like escaped text below.
        # Replace numeric buttons with deterministic category IDs.
        kb = [[InlineKeyboardButton(text=r[0], callback_data=f"activecat:{CATEGORIES.index(r[0]) if r[0] in CATEGORIES else 99}")] for r in cats]
        kb.append([InlineKeyboardButton(text="🌐 Показать все", callback_data="activecat:all")])
    if not cats:
        await message.answer("📂 Активных обращений нет.")
        return
    await message.answer("📁 Выберите категорию обращений:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data.startswith("activecat:"))
async def show_filtered_appeals(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    key = callback.data.split(":", 1)[1]
    category = CATEGORY_BY_INDEX.get(key)
    conn = db()
    if key == "all" or category is None:
        rows = conn.execute(
            "SELECT id,category,urgency,user_name,is_anon,created_at,status FROM appeals WHERE status!='closed' ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,category,urgency,user_name,is_anon,created_at,status FROM appeals WHERE status!='closed' AND category=? ORDER BY id DESC",
            (category,),
        ).fetchall()
    conn.close()
    if not rows:
        await callback.message.answer("📂 В выбранной категории нет активных обращений.")
        await callback.answer()
        return
    await callback.message.answer("📁 <b>Активные обращения:</b>")
    for appeal_id, cat, urgency, user_name, is_anon, created, status in rows:
        author = "🕵️ Анонимно" if is_anon else f"👤 {html.escape(user_name or 'Без имени')}"
        await callback.message.answer(
            f"📌 <b>Обращение №{appeal_id}</b> [{urgency}]\n"
            f"📂 {html.escape(cat)}\nОт: {author}\n📅 {created}\n"
            f"Статус: <b>{status}</b>",
            reply_markup=admin_ticket_keyboard(appeal_id, can_reply=True),
        )
    await callback.answer()


# ============================================================
# HISTORY OF CLOSED TICKETS
# ============================================================
@router.message(F.text == "📚 История обращений")
async def history_list(message: Message):
    if message.from_user.id not in load_admins():
        return
    await show_history_page(message, 0)


async def show_history_page(target, page: int):
    per_page = 10
    offset = page * per_page
    conn = db()
    rows = conn.execute(
        """
        SELECT id,category,urgency,is_anon,user_name,created_at,closed_at,rating
        FROM appeals WHERE status='closed' ORDER BY id DESC LIMIT ? OFFSET ?
        """,
        (per_page, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM appeals WHERE status='closed'").fetchone()[0]
    conn.close()
    if not rows:
        await target.answer("📚 История обращений пока пуста.")
        return
    buttons = []
    for appeal_id, cat, urgency, anon, name, created, closed, rating in rows:
        label = f"№{appeal_id} • {cat[:24]} • {created[:10]}"
        if rating:
            label += f" • ⭐{rating}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"history:{appeal_id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"histpage:{page-1}"))
    if offset + per_page < total:
        nav.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"histpage:{page+1}"))
    if nav:
        buttons.append(nav)
    await target.answer(
        f"📚 <b>История обращений</b>\nСтраница {page+1}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("histpage:"))
async def history_page_callback(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    page = int(callback.data.split(":", 1)[1])
    await show_history_page(callback.message, page)
    await callback.answer()


async def send_content_to_chat(
    chat_id: int,
    media_type: Optional[str],
    media_id: Optional[str],
    text: str,
    reply_markup=None,
):
    if media_type == "photo" and media_id:
        # Caption limit is 1024; for long history entries send text separately.
        if len(text) <= 1000:
            await bot.send_photo(chat_id, media_id, caption=text, reply_markup=reply_markup)
        else:
            await bot.send_photo(chat_id, media_id, reply_markup=reply_markup)
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
    elif media_type == "video" and media_id:
        if len(text) <= 1000:
            await bot.send_video(chat_id, media_id, caption=text, reply_markup=reply_markup)
        else:
            await bot.send_video(chat_id, media_id, reply_markup=reply_markup)
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text or "Без текста", reply_markup=reply_markup)


@router.callback_query(F.data.startswith("history:"))
async def view_room_history(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    (
        _id, _uid, _uname, category, urgency, is_anon, status, rating,
        created, closed, last_activity,
    ) = row
    conn = db()
    messages = conn.execute(
        """
        SELECT sender_role,text,photo_id,video_id,media_type,created_at
        FROM messages_log WHERE appeal_id=? ORDER BY id ASC
        """,
        (appeal_id,),
    ).fetchall()
    conn.close()

    rating_text = f"⭐ {rating}/5" if rating else "не оценено"
    await callback.message.answer(
        f"📋 <b>Полная история обращения №{appeal_id}</b>\n"
        f"📂 {html.escape(category)}\n"
        f"⚠️ {urgency}\n"
        f"Статус: {'🔒 Закрыто' if status == 'closed' else '🟢 Активно'}\n"
        f"CSAT: {rating_text}\n"
        f"Создано: {created}\n"
        f"Закрыто: {closed or '—'}"
    )
    for role, text, photo_id, video_id, media_type, dt in messages:
        label = "👤 Сотрудник" if role == "employee" else "🛠 Администратор"
        body = f"[{dt}] {label}\n{html.escape(text or 'Без текста')}"
        # Keep history entries clickable for active tickets, but for closed tickets
        # only show the full content; reopening is intentionally impossible here.
        await send_content_to_chat(callback.from_user.id, media_type, photo_id or video_id, body, None)
    if status != "closed":
        await callback.message.answer("💬 Можно продолжить диалог:", reply_markup=admin_ticket_keyboard(appeal_id, can_reply=True))
    await callback.answer()


# ============================================================
# BROADCAST
# ============================================================
@router.message(F.text == "📢 Сделать рассылку")
async def start_broadcast(message: Message):
    if message.from_user.id not in load_admins():
        return
    admin_process[message.from_user.id] = "broadcast"
    await message.answer("📢 Отправьте текст, фото или видео для рассылки. Другие типы файлов запрещены.", reply_markup=cancel_keyboard)



@router.message()
async def catch_messages(message: Message):
    """Single controlled message gateway for appeal content and admin replies."""
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        return

    # 0) Admin broadcast mode.
    if user_id in load_admins() and admin_process.get(user_id) == "broadcast":
        content = extract_content(message)
        if content is None:
            await message.answer("❌ Для рассылки разрешены только текст, фото и видео.")
            return
        media_type, media_id, text = content
        if not text and media_type == "text":
            await message.answer("❌ Текст рассылки не может быть пустым.")
            return
        admin_process.pop(user_id, None)
        conn = db()
        users = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
        conn.close()
        success = failed = 0
        for uid in users:
            if is_user_blocked(uid):
                continue
            try:
                await send_content_to_chat(uid, media_type, media_id, text, None)
                success += 1
            except Exception:
                failed += 1
        await message.answer(
            f"📢 Рассылка завершена.\n✅ Успешно: {success}\n❌ Ошибок: {failed}",
            reply_markup=admin_keyboard,
        )
        return

    # 1) New appeal content.
    if user_id in new_appeal_state:
        content = extract_content(message)
        if content is None:
            await message.answer("❌ В обращении разрешены только текст, фото и видео. Отправьте один из этих типов.")
            return
        media_type, media_id, text = content
        if not text and media_type == "text":
            await message.answer("❌ Текст обращения не может быть пустым.")
            return
        new_appeal_state[user_id]["media_type"] = media_type
        new_appeal_state[user_id]["media_id"] = media_id
        new_appeal_state[user_id]["text"] = text
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🟢 Обычное", callback_data="urgency:normal"),
                    InlineKeyboardButton(text="🔴 Срочное", callback_data="urgency:urgent"),
                ],
                [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_new")],
            ]
        )
        await message.answer("⚠️ Насколько срочно нужно решить вопрос?", reply_markup=kb)
        return

    # 2) Employee is explicitly composing the next turn.
    if user_id in compose_user:
        appeal_id = compose_user.pop(user_id)
        row = get_appeal(appeal_id)
        if not row or row[1] != user_id or row[6] == "closed":
            await message.answer("⚠️ Обращение не найдено или уже закрыто.", reply_markup=employee_keyboard)
            return
        content = extract_content(message)
        if content is None:
            await message.answer("❌ В диалоге разрешены только текст, фото и видео.")
            compose_user[user_id] = appeal_id
            return
        media_type, media_id, text = content
        if not text and media_type == "text":
            await message.answer("❌ Сообщение не может быть пустым.")
            compose_user[user_id] = appeal_id
            return
        insert_message(appeal_id, user_id, "employee", text, media_type, media_id)
        set_status(appeal_id, "waiting_admin")
        await message.answer("✅ Сообщение отправлено поддержке. Ожидайте ответа.", reply_markup=employee_ticket_keyboard(appeal_id, can_reply=False))
        for admin_id in load_admins():
            try:
                await send_content_to_chat(
                    admin_id, media_type, media_id,
                    f"📩 <b>Новое сообщение по тикету №{appeal_id}</b>\n\n{html.escape(text or 'Без текста')}",
                    admin_ticket_keyboard(appeal_id, can_reply=True),
                )
            except Exception:
                logging.exception("Ошибка уведомления администратора")
        return

    # 3) Admin is explicitly composing the next turn.
    if user_id in compose_admin:
        appeal_id = compose_admin.pop(user_id)
        if user_id not in load_admins():
            return
        row = get_appeal(appeal_id)
        if not row or row[6] == "closed":
            await message.answer("⚠️ Тикет не найден или уже закрыт.", reply_markup=admin_keyboard)
            return
        content = extract_content(message)
        if content is None:
            await message.answer("❌ В ответе разрешены только текст, фото и видео.")
            compose_admin[user_id] = appeal_id
            return
        media_type, media_id, text = content
        if not text and media_type == "text":
            await message.answer("❌ Ответ не может быть пустым.")
            compose_admin[user_id] = appeal_id
            return
        insert_message(appeal_id, user_id, "admin", text, media_type, media_id)
        set_status(appeal_id, "waiting_user")
        target_user_id = row[1]
        try:
            await send_content_to_chat(
                target_user_id, media_type, media_id,
                f"📩 <b>Ответ поддержки по тикету №{appeal_id}</b>\n\n{html.escape(text or 'Без текста')}",
                employee_ticket_keyboard(appeal_id, can_reply=True),
            )
            await message.answer(
                f"✅ Ответ по тикету №{appeal_id} доставлен. Теперь ход диалога передан сотруднику.",
                reply_markup=admin_keyboard,
            )
        except Exception:
            await message.answer("⚠️ Не удалось доставить ответ сотруднику.", reply_markup=admin_keyboard)
        return

    # 4) Active ticket exists, but user tried to bypass the turn button.
    active = active_appeal_for_user(user_id)
    if active:
        if not is_supported_content(message):
            await message.answer("❌ В обращении разрешены только текст, фото и видео.")
        else:
            await message.answer(
                f"💬 Для продолжения диалога нажмите «Ответить поддержке» в обращении №{active}.",
                reply_markup=employee_ticket_keyboard(active, can_reply=True),
            )
        return

    # 5) Admin unsupported message outside a compose mode.
    if user_id in load_admins() and not message.text:
        await message.answer("ℹ️ Выберите действие в панели администратора.", reply_markup=admin_keyboard)


# ============================================================
# WEEKLY POLL SCHEDULER + AUTO-CLOSE
# ============================================================
async def schedule_weekly_polls():
    sent_key = None
    while True:
        try:
            now = datetime.now(BOT_TZ)
            key = now.strftime("%Y-%m-%d")
            if now.weekday() == 6 and now.hour == 14 and now.minute == 0 and sent_key != key:
                conn = db()
                users = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
                conn.close()
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=str(i), callback_data=f"w_poll:{i}") for i in range(1, 6)],
                        [InlineKeyboardButton(text=str(i), callback_data=f"w_poll:{i}") for i in range(6, 11)],
                    ]
                )
                for uid in users:
                    if is_user_blocked(uid):
                        continue
                    try:
                        await bot.send_message(
                            uid,
                            "📊 <b>Воскресный пульс-опрос</b>\n\nОцените от 1 до 10, как вы справились с работой на этой неделе:",
                            reply_markup=kb,
                        )
                    except Exception:
                        pass
                sent_key = key
        except Exception:
            logging.exception("Ошибка недельного опроса")
        await asyncio.sleep(20)


async def auto_close_inactive_tickets():
    while True:
        await asyncio.sleep(3600)
        try:
            threshold = (datetime.now(BOT_TZ) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            conn = db()
            rows = conn.execute(
                "SELECT id,user_id FROM appeals WHERE status!='closed' AND last_activity_at < ?",
                (threshold,),
            ).fetchall()
            for appeal_id, user_id in rows:
                conn.execute(
                    "UPDATE appeals SET status='closed',closed_at=?,last_activity_at=? WHERE id=?",
                    (now_str(), now_str(), appeal_id),
                )
            conn.commit()
            conn.close()
            for appeal_id, user_id in rows:
                compose_user.pop(user_id, None)
                try:
                    await bot.send_message(
                        user_id,
                        f"🔒 Обращение №{appeal_id} автоматически закрыто после 3 дней без активности.\nОцените работу поддержки:",
                        reply_markup=rating_keyboard(appeal_id),
                    )
                except Exception:
                    pass
                for admin_id in load_admins():
                    try:
                        await bot.send_message(admin_id, f"🔒 Обращение №{appeal_id} закрыто автоматически по тайм-ауту.")
                    except Exception:
                        pass
        except Exception:
            logging.exception("Ошибка автозакрытия")


# ============================================================
# MAIN
# ============================================================
async def main():
    dp.include_router(router)
    asyncio.create_task(auto_close_inactive_tickets())
    asyncio.create_task(schedule_weekly_polls())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
