from __future__ import annotations

import asyncio
import csv
import html
import logging
import os
import sqlite3
import tempfile
import shutil
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

_default_data_dir = Path("/app/data") if Path("/app/data").exists() else Path("data")
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", str(_default_data_dir)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = Path(os.getenv("BOT_BACKUP_DIR", str(DATA_DIR / "backups")))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
# Optional second mounted/external backup location. Configure on the host.
EXTERNAL_BACKUP_DIR = os.getenv("BOT_EXTERNAL_BACKUP_DIR", "").strip()
_legacy_db = Path("bot_database.db")
_default_db = DATA_DIR / "bot_database.db"
if "BOT_DB_PATH" in os.environ:
    DB_PATH = os.environ["BOT_DB_PATH"]
else:
    # One-time migration from the old repository-root database.
    if _legacy_db.exists() and not _default_db.exists():
        import shutil as _shutil
        _shutil.copy2(_legacy_db, _default_db)
    DB_PATH = str(_default_db)
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
ADMINS_FILE = os.getenv("ADMINS_FILE", "admins.txt")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "762076580"))
TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Europe/Moscow")
try:
    BOT_TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    BOT_TZ = ZoneInfo("Europe/Moscow")

# Пути к приветственным изображениям. При необходимости их можно
# переопределить переменными окружения GREETING_MORNING/DAY/EVENING/NIGHT.
THANKS_IMAGE = os.getenv("THANKS_IMAGE", "IMG_4457.jpeg")
SPAM_IMAGE = os.getenv("SPAM_IMAGE", "IMG_10MIN_SPAM.jpeg")
NEW_APPEAL_COOLDOWN_MINUTES = 10
REMINDER_FIRST_MINUTES = 30
REMINDER_REPEAT_MINUTES = 60

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
knowledge_search_user: dict[int, bool] = {}
period_state: dict[int, str] = {}
pending_user_reply: dict[int, dict] = {}
pending_admin_reply: dict[int, dict] = {}
broadcast_draft: dict[int, dict] = {}
pending_admin_action: dict[int, dict] = {}

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
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS appeal_reminders (
            appeal_id INTEGER PRIMARY KEY,
            last_sent_at TEXT,
            reminder_count INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            appeal_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS appeal_locks (
            appeal_id INTEGER PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            locked_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            text TEXT,
            media_type TEXT,
            media_id TEXT,
            success_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0
        )
        """
    )

    # Safe migrations for the old database shipped with the original bot.
    user_cols = {row[1] for row in cur.execute("PRAGMA table_info(users)")}
    if "last_appeal_at" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN last_appeal_at TEXT")

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
    # Registration is persistent in SQLite. Removing a Telegram chat does not
    # remove this record.
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


def appeal_cooldown_remaining(user_id: int) -> int:
    """Seconds remaining before a new appeal may be created."""
    conn = db()
    row = conn.execute(
        "SELECT last_appeal_at FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return 0
    try:
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BOT_TZ)
    except ValueError:
        return 0
    remaining = int(
        (last + timedelta(minutes=NEW_APPEAL_COOLDOWN_MINUTES) - datetime.now(BOT_TZ)).total_seconds()
    )
    return max(0, remaining)


def mark_new_appeal_time(user_id: int, timestamp: str) -> None:
    conn = db()
    conn.execute("UPDATE users SET last_appeal_at=? WHERE user_id=?", (timestamp, user_id))
    conn.commit()
    conn.close()


def format_remaining(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes} мин. {secs:02d} сек."
    return f"{secs} сек."


def log_admin_action(admin_id: int, action: str, appeal_id: Optional[int] = None, details: str = "") -> None:
    conn = db()
    conn.execute(
        "INSERT INTO admin_actions(admin_id,action,appeal_id,details,created_at) VALUES(?,?,?,?,?)",
        (admin_id, action, appeal_id, details, now_str()),
    )
    conn.commit()
    conn.close()


def acquire_appeal_lock(appeal_id: int, admin_id: int) -> bool:
    conn = db()
    try:
        conn.execute(
            "INSERT INTO appeal_locks(appeal_id,admin_id,locked_at) VALUES(?,?,?)",
            (appeal_id, admin_id, now_str()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT admin_id FROM appeal_locks WHERE appeal_id=?", (appeal_id,)).fetchone()
        conn.rollback()
        return bool(row and row[0] == admin_id)
    finally:
        conn.close()


def release_appeal_lock(appeal_id: int, admin_id: Optional[int] = None) -> None:
    conn = db()
    if admin_id is None:
        conn.execute("DELETE FROM appeal_locks WHERE appeal_id=?", (appeal_id,))
    else:
        conn.execute("DELETE FROM appeal_locks WHERE appeal_id=? AND admin_id=?", (appeal_id, admin_id))
    conn.commit()
    conn.close()


def get_appeal_lock(appeal_id: int):
    conn = db()
    row = conn.execute("SELECT admin_id,locked_at FROM appeal_locks WHERE appeal_id=?", (appeal_id,)).fetchone()
    conn.close()
    return row


def safe_backup_database() -> Optional[Path]:
    """Create a consistent SQLite backup and optionally copy it to a second mounted location."""
    source = Path(DB_PATH)
    if not source.exists():
        return None
    stamp = datetime.now(BOT_TZ).strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"bot_database_{stamp}.db"
    src_conn = sqlite3.connect(str(source))
    dst_conn = sqlite3.connect(str(target))
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()
    if EXTERNAL_BACKUP_DIR:
        ext = Path(EXTERNAL_BACKUP_DIR)
        ext.mkdir(parents=True, exist_ok=True)
        ext_target = ext / target.name
        if ext.resolve() != BACKUP_DIR.resolve():
            shutil.copy2(target, ext_target)
            ext_backups = sorted(ext.glob("bot_database_*.db"))
            for old_ext in ext_backups[:-30]:
                try:
                    old_ext.unlink()
                except OSError:
                    pass
    # Keep 30 local copies.
    backups = sorted(BACKUP_DIR.glob("bot_database_*.db"))
    for old in backups[:-30]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def verify_database() -> bool:
    try:
        conn = db()
        row = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return bool(row and row[0] == "ok")
    except Exception:
        logging.exception("Проверка БД не удалась")
        return False


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
        [KeyboardButton(text="📢 Важные объявления"), KeyboardButton(text="📚 База знаний")],
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
        [KeyboardButton(text="📢 Управление объявлениями"), KeyboardButton(text="📚 База знаний")],
        [KeyboardButton(text="🔎 Поиск обращений"), KeyboardButton(text="🧰 Фильтры обращений")],
        [KeyboardButton(text="💾 Резервная копия")],
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
    rows.append([InlineKeyboardButton(text="👨‍💼 Взять обращение", callback_data=f"claim:{appeal_id}")])
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


def new_appeal_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Отправить обращение", callback_data="submit_new")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_new"),
             InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_new")],
        ]
    )


def reply_confirm_keyboard(kind: str) -> InlineKeyboardMarkup:
    prefix = "u" if kind == "user" else "a"
    send_text = "🚀 Отправить сотруднику" if kind == "admin" else "🚀 Отправить поддержке"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=send_text, callback_data=f"confirm_reply:{prefix}")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_reply:{prefix}"),
             InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_reply:{prefix}")],
        ]
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="broadcast_prepare")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="broadcast_edit"),
             InlineKeyboardButton(text="❌ Отменить", callback_data="broadcast_cancel")],
        ]
    )


def broadcast_final_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚨 ДА, ОТПРАВИТЬ ВСЕМ", callback_data="broadcast_send")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
        ]
    )


def preview_content_text(media_type: str, text: str) -> str:
    label = "📝 Текст" if media_type == "text" else ("🖼 Фото" if media_type == "photo" else "🎥 Видео")
    return f"{label}\n{html.escape(text or 'Без текста')}"


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
    knowledge_search_user.pop(user_id, None)
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
    knowledge_search_user.pop(user_id, None)
    period_state.pop(user_id, None)
    pending_user_reply.pop(user_id, None)
    pending_admin_reply.pop(user_id, None)
    broadcast_draft.pop(user_id, None)
    pending_admin_action.pop(user_id, None)
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

    if action in {"add_admin", "remove_admin"}:
        if action == "remove_admin" and target_id == SUPER_ADMIN_ID:
            await message.answer("❌ Нельзя удалить главного администратора.", reply_markup=manage_admins_keyboard)
            return
        pending_admin_action[admin_id] = {"action": action, "target_id": target_id}
        verb = "добавить" if action == "add_admin" else "удалить"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Да, {verb}", callback_data="admin_action:confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_action:cancel")],
        ])
        await message.answer(
            f"⚠️ <b>Подтвердите действие</b>\n\n"
            f"Действие: <b>{verb} администратора</b>\n"
            f"Telegram ID: <code>{target_id}</code>\n\n"
            "Изменение прав будет применено сразу.",
            reply_markup=kb,
        )
        return


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


@router.message(F.text == "💾 Резервная копия")
async def backup_menu(message: Message):
    if message.from_user.id not in load_admins():
        return
    try:
        target = safe_backup_database()
        healthy = verify_database()
        if not target:
            await message.answer("❌ Не удалось создать резервную копию.", reply_markup=admin_keyboard)
            return
        extra = "Вторая копия: настроена." if EXTERNAL_BACKUP_DIR else "⚠️ Вторая независимая копия не настроена."
        await message.answer(
            "💾 <b>РЕЗЕРВНАЯ КОПИЯ СОЗДАНА</b>\n\n"
            f"📁 {html.escape(str(target))}\n"
            f"🧪 Проверка БД: {'✅ OK' if healthy else '❌ ОШИБКА'}\n"
            f"{extra}\n\n"
            "Для максимальной защиты на BotHost подключите постоянный Volume к /app/data "
            "и отдельное постоянное хранилище для BOT_EXTERNAL_BACKUP_DIR.",
            reply_markup=admin_keyboard,
        )
        log_admin_action(message.from_user.id, "manual_backup", details=str(target))
    except Exception:
        logging.exception("Ошибка ручного backup")
        await message.answer("❌ Ошибка при создании резервной копии.", reply_markup=admin_keyboard)


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
@router.callback_query(F.data == "admin_action:confirm")
async def confirm_admin_action(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id not in load_admins():
        return
    draft = pending_admin_action.pop(admin_id, None)
    if not draft:
        await callback.answer("Действие уже обработано", show_alert=True)
        return
    target_id = draft["target_id"]
    admins = load_admins()
    if draft["action"] == "add_admin":
        admins.add(target_id)
        save_admins(admins)
        log_admin_action(admin_id, "add_admin", details=str(target_id))
        text = f"✅ Администратор <code>{target_id}</code> добавлен."
    else:
        if target_id == SUPER_ADMIN_ID:
            await callback.answer("Главного администратора удалить нельзя", show_alert=True)
            return
        if target_id in admins:
            admins.remove(target_id)
            if not admins:
                admins.add(SUPER_ADMIN_ID)
            save_admins(admins)
            log_admin_action(admin_id, "remove_admin", details=str(target_id))
            text = f"✅ Администратор <code>{target_id}</code> удалён."
        else:
            text = "❌ Такого администратора нет."
    await callback.message.answer(text, reply_markup=manage_admins_keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_action:cancel")
async def cancel_admin_action(callback: CallbackQuery):
    pending_admin_action.pop(callback.from_user.id, None)
    await callback.message.answer("❌ Изменение администраторов отменено.", reply_markup=manage_admins_keyboard)
    await callback.answer()


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
# VACATION / SICK LEAVE REFERENCE
# ============================================================
@router.message(F.text == "🌴 Отпуска")
async def vacation_info(message: Message):
    if is_user_blocked(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Как оформить отпуск", callback_data="info:vacation_apply")],
        [InlineKeyboardButton(text="💰 Отпускные", callback_data="info:vacation_pay")],
        [InlineKeyboardButton(text="📅 Первый отпуск", callback_data="info:vacation_first")],
        [InlineKeyboardButton(text="✍️ Задать вопрос", callback_data="info:ask_vacation")],
    ])
    await message.answer(
        "🌴 <b>ОТПУСК</b>\n\n"
        "Здесь собраны основные правила из памятки по отпуску.\n\n"
        "Выберите интересующий раздел:",
        reply_markup=kb,
    )


@router.message(F.text == "🏥 Больничные")
async def sick_info(message: Message):
    if is_user_blocked(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Что делать с больничным", callback_data="info:sick_send")],
        [InlineKeyboardButton(text="💰 Когда выплата", callback_data="info:sick_pay")],
        [InlineKeyboardButton(text="🌴 Пересечение с отпуском", callback_data="info:sick_vacation")],
        [InlineKeyboardButton(text="✍️ Задать вопрос", callback_data="info:ask_sick")],
    ])
    await message.answer(
        "🏥 <b>БОЛЬНИЧНЫЙ ЛИСТ</b>\n\n"
        "Выберите интересующий раздел:",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("info:"))
async def info_callback(callback: CallbackQuery):
    if is_user_blocked(callback.from_user.id):
        return
    key = callback.data.split(":", 1)[1]
    texts = {
        "vacation_apply":
            "📋 <b>Как оформить отпуск</b>\n\n"
            "Заранее предупреди лидера и/или коллег о планируемом отпуске.\n\n"
            "Напиши электронное письмо с указанием ФИО и периода отпуска с копией лидеру.\n\n"
            "⚠️ Письмо на отпуск отправляется за <b>две недели до планируемой даты отпуска</b>.\n\n"
            "📧 Москва и МО: kadri@izbenka.msk.ru\n"
            "📧 Регионы: region@vkusvill.ru\n"
            "📧 Санкт-Петербург: kdpspb@vkusvill.ru",
        "vacation_pay":
            "💰 <b>Отпускные</b>\n\n"
            "Расчёт отпускных происходит из среднего заработка расчётного периода, за который оформляется отпуск.\n\n"
            "Выплата отпускных начисляется за <b>3–4 дня до даты начала отпуска</b>.",
        "vacation_first":
            "📅 <b>Когда можно уйти в первый отпуск?</b>\n\n"
            "Первый отпуск предоставляется через <b>6 месяцев</b> с начала работы.\n"
            "Если отпуск понадобился раньше, обсуди такую возможность с лидером.",
        "sick_send":
            "📋 <b>Куда направить данные больничного?</b>\n\n"
            "Номер больничного после закрытия необходимо направить на электронную почту отдела кадров для проведения выплаты.\n\n"
            "Данные будут обработаны в течение <b>трёх календарных дней</b>.\n\n"
            "📧 Москва и МО: kadri@izbenka.msk.ru\n"
            "📧 Регионы: region@vkusvill.ru\n"
            "📧 Санкт-Петербург: kdpspb@vkusvill.ru",
        "sick_pay":
            "💰 <b>Когда производится выплата?</b>\n\n"
            "Выплаты пособий по больничному производятся частями:\n\n"
            "• первые три дня — за счёт работодателя в ближайший день выплаты зарплаты (10 или 25 числа месяца);\n"
            "• оставшиеся дни — за счёт СФР в течение 10 рабочих дней после получения данных от работодателя.",
        "sick_vacation":
            "🌴 <b>Если больничный пересекается с отпуском</b>\n\n"
            "В день закрытия больничного сотрудник отдела кадров направит заявление о переносе или продлении отпуска через КЭДО (HRlink).",
        "ask_vacation":
            "✍️ <b>Вопрос по отпуску</b>\n\nВыберите «🌴 Отпуска» ещё раз и воспользуйтесь справкой или создайте обращение по нужной категории.",
        "ask_sick":
            "✍️ <b>Вопрос по больничному</b>\n\nВыберите «🏥 Больничные» ещё раз и воспользуйтесь справкой или создайте обращение по нужной категории.",
    }
    await callback.message.answer(texts.get(key, "ℹ️ Раздел не найден."), reply_markup=employee_keyboard)
    await callback.answer()


# ============================================================
# APPEAL CREATION
# ============================================================
@router.message(F.text.in_(CATEGORIES))
async def select_category(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        return
    if message.text in {"🌴 Отпуска", "🏥 Больничные"}:
        return

    # Existing active ticket takes priority over cooldown.
    active = active_appeal_for_user(user_id)
    if active:
        await message.answer(
            f"⚠️ У вас уже есть открытое обращение №{active}. "
            "Продолжите его или закройте.",
            reply_markup=employee_ticket_keyboard(active, can_reply=True),
        )
        return

    remaining = appeal_cooldown_remaining(user_id)
    if remaining:
        text = (
            "🛡️ <b>Небольшая пауза</b>\n\n"
            "Вы уже отправили обращение.\n"
            f"Новое обращение можно будет создать через <b>{format_remaining(remaining)}</b>.\n\n"
            "💬 Если хотите продолжить текущий диалог — откройте своё обращение."
        )
        if Path(SPAM_IMAGE).exists():
            await message.answer_photo(FSInputFile(SPAM_IMAGE), caption=text, reply_markup=employee_keyboard)
        else:
            await message.answer(text, reply_markup=employee_keyboard)
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
    priority_map = {"normal": "🟢 Обычное", "important": "🟡 Важное", "urgent": "🔴 Срочное"}
    key = callback.data.split(":", 1)[1]
    new_appeal_state[user_id]["urgency"] = priority_map.get(key, "🟢 Обычное")
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
    data = new_appeal_state.get(user_id)
    if not data:
        await callback.answer("Сессия обращения устарела", show_alert=True)
        return
    requested_anon = int(callback.data.split(":", 1)[1])
    data["is_anon"] = 1 if data.get("force_anonymous") else requested_anon
    author = "🕵️ Анонимно" if data["is_anon"] else f"👤 {html.escape(callback.from_user.full_name)}"
    preview = (
        "📋 <b>ПРОВЕРЬТЕ ОБРАЩЕНИЕ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
        f"📂 <b>Категория:</b> {html.escape(data['category'])}\n"
        f"⚠️ <b>Срочность:</b> {html.escape(data.get('urgency', '🟢 Обычное'))}\n"
        f"👤 <b>Отправитель:</b> {author}\n\n"
        f"{preview_content_text(data['media_type'], data['text'])}\n\n"
        "⚠️ После подтверждения обращение будет передано поддержке."
    )
    await callback.message.answer(preview, reply_markup=new_appeal_confirm_keyboard())
    await callback.answer()


@router.callback_query(F.data == "edit_new")
async def edit_new_appeal(callback: CallbackQuery):
    data = new_appeal_state.get(callback.from_user.id)
    if not data:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    data.pop("media_type", None)
    data.pop("media_id", None)
    data.pop("text", None)
    await callback.message.answer(
        "✏️ <b>Изменение обращения</b>\n\nОтправьте исправленный текст, фото или видео.",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "submit_new")
async def submit_new_appeal(callback: CallbackQuery):
    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        return
    data = new_appeal_state.get(user_id)
    if not data:
        await callback.answer("Сессия обращения устарела", show_alert=True)
        return
    if active_appeal_for_user(user_id):
        new_appeal_state.pop(user_id, None)
        await callback.answer("У вас уже есть открытое обращение", show_alert=True)
        return
    remaining = appeal_cooldown_remaining(user_id)
    if remaining:
        new_appeal_state.pop(user_id, None)
        await callback.message.answer(
            f"⏳ Новое обращение можно будет отправить через <b>{format_remaining(remaining)}</b>.",
            reply_markup=employee_keyboard,
        )
        await callback.answer()
        return

    ts = now_str()
    is_anon = int(data.get("is_anon", 1))
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO appeals(user_id,user_name,category,urgency,is_anon,status,created_at,last_activity_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (user_id, callback.from_user.full_name, data["category"], data["urgency"],
         is_anon, "waiting_admin", ts, ts),
    )
    appeal_id = cur.lastrowid
    media_type, media_id, text = data["media_type"], data["media_id"], data["text"]
    cur.execute(
        """
        INSERT INTO messages_log(appeal_id,sender_id,sender_role,text,photo_id,video_id,media_type,created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (appeal_id, user_id, "employee", text,
         media_id if media_type == "photo" else None,
         media_id if media_type == "video" else None,
         media_type, ts),
    )
    conn.execute("UPDATE users SET last_appeal_at=? WHERE user_id=?", (ts, user_id))
    conn.commit()
    conn.close()
    new_appeal_state.pop(user_id, None)

    author = "🕵️ <b>Анонимный сотрудник</b>" if is_anon else f"👤 <b>{html.escape(callback.from_user.full_name)}</b>"
    admin_text = (
        f"🚨 <b>НОВОЕ ОБРАЩЕНИЕ №{appeal_id}</b> [{data['urgency']}]\n"
        f"📂 {html.escape(data['category'])}\n"
        f"От: {author}\n\n"
        f"💬 {html.escape(text or 'Без текста')}"
    )
    for admin_id in load_admins():
        try:
            await send_content_to_chat(admin_id, media_type, media_id, admin_text,
                                       admin_ticket_keyboard(appeal_id, can_reply=True))
        except Exception:
            logging.exception("Не удалось уведомить администратора %s", admin_id)

    thanks = (
        f"✅ <b>Спасибо за ваше обращение №{appeal_id}!</b>\n\n"
        "Обращение принято и передано поддержке.\n\n"
        "🕙 <b>Рабочее время поддержки: 10:00–22:00.</b>\n"
        "Если обращение отправлено вне рабочего времени, ответ будет дан в рабочее время.\n\n"
        "💬 После ответа поддержки здесь появится кнопка «Ответить поддержке»."
    )
    if Path(THANKS_IMAGE).exists():
        await callback.message.answer_photo(
            FSInputFile(THANKS_IMAGE), caption=thanks,
            reply_markup=employee_ticket_keyboard(appeal_id, can_reply=False)
        )
    else:
        logging.warning("Файл благодарности не найден: %s", THANKS_IMAGE)
        await callback.message.answer(thanks, reply_markup=employee_ticket_keyboard(appeal_id, can_reply=False))
    await callback.answer("Обращение отправлено")


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


@router.callback_query(F.data.startswith("claim:"))
async def claim_appeal(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row or row[6] == "closed":
        await callback.answer("Обращение закрыто или не найдено", show_alert=True)
        return
    lock = get_appeal_lock(appeal_id)
    if lock and lock[0] != admin_id:
        await callback.answer("⚠️ Обращение уже взял другой администратор.", show_alert=True)
        return
    if acquire_appeal_lock(appeal_id, admin_id):
        log_admin_action(admin_id, "claim_appeal", appeal_id)
        await callback.message.answer(
            f"🔒 <b>Обращение №{appeal_id} закреплено за вами.</b>\n"
            "Другие администраторы не смогут одновременно начать ответ.",
            reply_markup=admin_ticket_keyboard(appeal_id, can_reply=True),
        )
        await callback.answer("Обращение закреплено")
    else:
        await callback.answer("Не удалось закрепить обращение", show_alert=True)


@router.callback_query(F.data.startswith("a_reply:"))
async def admin_reply_button(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row or row[6] == "closed":
        await callback.answer("Тикет закрыт", show_alert=True)
        return
    lock = get_appeal_lock(appeal_id)
    if lock and lock[0] != admin_id:
        await callback.answer("⚠️ Сначала дождитесь завершения работы другого администратора.", show_alert=True)
        return
    if not acquire_appeal_lock(appeal_id, admin_id):
        await callback.answer("Обращение занято другим администратором", show_alert=True)
        return
    compose_admin[admin_id] = appeal_id
    log_admin_action(admin_id, "start_reply", appeal_id)
    await callback.message.answer(
        f"✍️ <b>Диалог по тикету №{appeal_id}</b>\nОтправьте одно сообщение: текст, фото или видео.\n\n"
        "👤 Сотрудник не увидит ваше имя.",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("u_close:"))
async def employee_close(callback: CallbackQuery):
    user_id = callback.from_user.id
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row or row[1] != user_id or row[6] == "closed":
        await callback.answer("Обращение недоступно", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Да, закрыть", callback_data=f"u_close_confirm:{appeal_id}")],
        [InlineKeyboardButton(text="↩️ Оставить открытым", callback_data=f"u_close_cancel:{appeal_id}")]
    ])
    await callback.message.answer("⚠️ <b>Закрыть обращение?</b>\n\nПосле закрытия диалог будет завершён, и для нового обращения будет действовать обычный лимит 10 минут.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("u_close_confirm:"))
async def employee_close_confirm(callback: CallbackQuery):
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 Да, закрыть", callback_data=f"a_close_confirm:{appeal_id}")],
        [InlineKeyboardButton(text="↩️ Оставить открытым", callback_data=f"a_close_cancel:{appeal_id}")]
    ])
    await callback.message.answer("⚠️ <b>Закрыть обращение?</b>\n\nПроверьте, что вопрос действительно решён.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("a_close_confirm:"))
async def admin_close_confirm(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    appeal_id = int(callback.data.split(":", 1)[1])
    row = get_appeal(appeal_id)
    if not row:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    set_status(appeal_id, "closed")
    release_appeal_lock(appeal_id)
    compose_admin.pop(callback.from_user.id, None)
    log_admin_action(callback.from_user.id, "close_appeal", appeal_id)
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


@router.callback_query(F.data.startswith("u_close_cancel:"))
async def employee_close_cancel(callback: CallbackQuery):
    await callback.message.answer("↩️ Обращение оставлено открытым.")
    await callback.answer()


@router.callback_query(F.data.startswith("a_close_cancel:"))
async def admin_close_cancel(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    await callback.message.answer("↩️ Обращение оставлено открытым.")
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
# ANNOUNCEMENTS / KNOWLEDGE BASE / SEARCH / FILTERS
# ============================================================
def admin_simple_cancel_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"simple_cancel:{action}")]
    ])


@router.message(F.text == "📢 Важные объявления")
async def show_announcements(message: Message):
    conn = db()
    rows = conn.execute("SELECT id,title,body,created_at FROM announcements WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    if not rows:
        await message.answer("📢 <b>Важные объявления</b>\n\nСейчас активных объявлений нет.", reply_markup=employee_keyboard)
        return
    parts = ["📢 <b>ВАЖНЫЕ ОБЪЯВЛЕНИЯ</b>"]
    for aid, title, body, created in rows:
        parts.append(f"\n━━━━━━━━━━━━━━\n📌 <b>{html.escape(title)}</b>\n{html.escape(body)}\n🕒 {created}")
    await message.answer("\n".join(parts), reply_markup=employee_keyboard)


@router.message(F.text == "📢 Управление объявлениями")
async def manage_announcements(message: Message):
    if message.from_user.id not in load_admins(): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новое объявление", callback_data="ann:new")],
        [InlineKeyboardButton(text="📋 Активные объявления", callback_data="ann:list")],
    ])
    await message.answer("📢 <b>УПРАВЛЕНИЕ ОБЪЯВЛЕНИЯМИ</b>\n\nЗакреплённые объявления хранятся в базе и не исчезают после перезапуска бота.", reply_markup=kb)


@router.callback_query(F.data == "ann:new")
async def ann_new(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    admin_process[callback.from_user.id] = "announcement_title"
    await callback.message.answer("📢 Введите заголовок объявления:", reply_markup=cancel_keyboard)
    await callback.answer()


@router.callback_query(F.data == "ann:list")
async def ann_list(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    conn=db(); rows=conn.execute("SELECT id,title,created_at FROM announcements WHERE active=1 ORDER BY id DESC").fetchall(); conn.close()
    if not rows:
        await callback.message.answer("Активных объявлений нет."); await callback.answer(); return
    for aid,title,created in rows:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Снять с публикации", callback_data=f"ann:off:{aid}")]])
        await callback.message.answer(f"📌 <b>{html.escape(title)}</b>\n🕒 {created}",reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ann:off:"))
async def ann_off(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    aid=int(callback.data.split(":")[-1]); conn=db(); conn.execute("UPDATE announcements SET active=0 WHERE id=?",(aid,)); conn.commit(); conn.close()
    await callback.message.answer("✅ Объявление снято с публикации."); await callback.answer()


@router.message(F.text == "📚 База знаний")
async def knowledge_menu(message: Message):
    is_admin = message.from_user.id in load_admins()
    buttons = [[InlineKeyboardButton(text="🔎 Найти ответ", callback_data="kb:search")]]
    if is_admin:
        buttons.extend([
            [InlineKeyboardButton(text="➕ Добавить материал", callback_data="kb:add")],
            [InlineKeyboardButton(text="📋 Управление материалами", callback_data="kb:list")],
        ])
    await message.answer(
        "📚 <b>БАЗА ЗНАНИЙ</b>\n\n"
        "Сотрудники могут искать утверждённые ответы. "
        "Администраторы могут добавлять, редактировать и архивировать материалы.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "kb:add")
async def kb_add(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        await callback.answer("Доступ только для администратора", show_alert=True); return
    admin_process[callback.from_user.id] = "kb_title"
    await callback.message.answer("📚 <b>Новый материал</b>\n\nВведите название материала:", reply_markup=cancel_keyboard)
    await callback.answer()


@router.callback_query(F.data == "kb:list")
async def kb_list(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        await callback.answer("Доступ только для администратора", show_alert=True); return
    conn = db()
    rows = conn.execute("SELECT id,title,body FROM knowledge_base WHERE active=1 ORDER BY id DESC LIMIT 30").fetchall()
    conn.close()
    if not rows:
        await callback.message.answer("📚 Активных материалов пока нет.")
        await callback.answer(); return
    await callback.message.answer("📚 <b>УПРАВЛЕНИЕ БАЗОЙ ЗНАНИЙ</b>\n\nВыберите материал:")
    for kid, title, body in rows:
        preview = (body or "").strip()
        if len(preview) > 220:
            preview = preview[:220] + "…"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"kb:edit:{kid}"),
             InlineKeyboardButton(text="🗄 Архивировать", callback_data=f"kb:archive:{kid}")]
        ])
        await callback.message.answer(
            f"📚 <b>{html.escape(title)}</b>\n\n{html.escape(preview)}",
            reply_markup=kb,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("kb:edit:"))
async def kb_edit(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        await callback.answer("Доступ только для администратора", show_alert=True); return
    try:
        kid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Некорректный материал", show_alert=True); return
    conn = db()
    row = conn.execute("SELECT id,title,body FROM knowledge_base WHERE id=? AND active=1", (kid,)).fetchone()
    conn.close()
    if not row:
        await callback.answer("Материал не найден", show_alert=True); return
    admin_process[callback.from_user.id] = f"kb_edit_body:{kid}"
    await callback.message.answer(
        f"✏️ <b>Редактирование: {html.escape(row[1])}</b>\n\n"
        "Отправьте новый текст материала. Название останется прежним.",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kb:archive:"))
async def kb_archive(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        await callback.answer("Доступ только для администратора", show_alert=True); return
    try:
        kid = int(callback.data.split(":")[-1])
    except ValueError:
        await callback.answer("Некорректный материал", show_alert=True); return
    conn = db(); row = conn.execute("SELECT title FROM knowledge_base WHERE id=? AND active=1", (kid,)).fetchone(); conn.close()
    if not row:
        await callback.answer("Материал уже архивирован или не найден", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, архивировать", callback_data=f"kb:archive_yes:{kid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="kb:archive_no"),
    ]])
    await callback.message.answer(
        f"🗄 <b>Архивировать материал?</b>\n\n«{html.escape(row[0])}»\n\n"
        "Материал исчезнет из поиска сотрудников, но останется в базе и его можно будет восстановить позже.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kb:archive_yes:"))
async def kb_archive_yes(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        await callback.answer("Доступ только для администратора", show_alert=True); return
    kid = int(callback.data.split(":")[-1])
    conn = db(); conn.execute("UPDATE knowledge_base SET active=0 WHERE id=?", (kid,)); conn.commit(); conn.close()
    log_admin_action(callback.from_user.id, "knowledge_archive", details=f"knowledge_id={kid}")
    await callback.message.answer("🗄 Материал архивирован. Он не удалён из базы и больше не показывается сотрудникам.", reply_markup=admin_keyboard)
    await callback.answer()


@router.callback_query(F.data == "kb:archive_no")
async def kb_archive_no(callback: CallbackQuery):
    await callback.message.answer("❌ Архивирование отменено.")
    await callback.answer()


@router.callback_query(F.data == "kb:search")
async def kb_search(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in load_admins():
        admin_process[user_id] = "kb_search"
    else:
        knowledge_search_user[user_id] = True
    await callback.message.answer(
        "🔎 Введите ключевое слово или фразу, которую хотите найти:",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.message(F.text == "🔎 Поиск обращений")
async def appeal_search_start(message: Message):
    if message.from_user.id not in load_admins(): return
    admin_process[message.from_user.id]="appeal_search"
    await message.answer("🔎 <b>ПОИСК ОБРАЩЕНИЙ</b>\n\nВведите номер обращения, имя сотрудника или слово из текста:", reply_markup=cancel_keyboard)


@router.message(F.text == "🧰 Фильтры обращений")
async def appeal_filters(message: Message):
    if message.from_user.id not in load_admins(): return
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Срочные", callback_data="filter:urgent"), InlineKeyboardButton(text="🟡 Важные", callback_data="filter:important")],
        [InlineKeyboardButton(text="🟢 Обычные", callback_data="filter:normal")],
        [InlineKeyboardButton(text="⏳ Ждут ответа", callback_data="filter:waiting_admin")],
        [InlineKeyboardButton(text="👤 Ждут сотрудника", callback_data="filter:waiting_user")],
        [InlineKeyboardButton(text="📂 Все открытые", callback_data="filter:open")],
    ])
    await message.answer("🧰 <b>ФИЛЬТРЫ ОБРАЩЕНИЙ</b>", reply_markup=kb)


@router.callback_query(F.data.startswith("filter:"))
async def apply_filter(callback: CallbackQuery):
    if callback.from_user.id not in load_admins(): return
    key=callback.data.split(":",1)[1]
    where="status!='closed'"; params=[]
    if key in {"urgent","important","normal"}:
        where += " AND urgency=?"; params.append({"urgent":"🔴 Срочное","important":"🟡 Важное","normal":"🟢 Обычное"}[key])
    elif key in {"waiting_admin","waiting_user"}:
        where += " AND status=?"; params.append(key)
    conn=db(); rows=conn.execute(f"SELECT id,category,urgency,user_name,is_anon,created_at,status,last_activity_at FROM appeals WHERE {where} ORDER BY id DESC LIMIT 50",params).fetchall(); conn.close()
    await send_appeal_rows(callback.message, rows, "🧰 Результат фильтра")
    await callback.answer()


async def send_appeal_rows(message: Message, rows, heading="📨 Обращения"):
    if not rows:
        await message.answer(f"{heading}\n\nНичего не найдено."); return
    await message.answer(f"{heading}\n\nНайдено: <b>{len(rows)}</b>")
    for aid,cat,urg,user_name,is_anon,created,status,last_activity in rows:
        author="🕵️ Анонимно" if is_anon else f"👤 {html.escape(user_name or 'Без имени')}"
        age=""
        try: age_minutes=int((datetime.now(BOT_TZ)-datetime.strptime(last_activity or created,"%Y-%m-%d %H:%M:%S").replace(tzinfo=BOT_TZ)).total_seconds()//60); age=f"\n⏱ Без активности: {age_minutes} мин."
        except Exception: pass
        await message.answer(f"<b>№{aid}</b> {urg}\n📂 {html.escape(cat)}\n{author}\n🕒 {created}\n📌 {status}{age}",reply_markup=admin_ticket_keyboard(aid,can_reply=status!="closed"))


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
    await message.answer("📢 <b>МАССОВАЯ РАССЫЛКА</b>\n\nОтправьте текст, фото или видео.\n\nПосле этого бот покажет <b>предпросмотр и количество получателей</b>. Ничего не будет отправлено без вашего подтверждения.", reply_markup=cancel_keyboard)



@router.callback_query(F.data == "broadcast_edit")
async def broadcast_edit(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    broadcast_draft.pop(callback.from_user.id, None)
    admin_process[callback.from_user.id] = "broadcast"
    await callback.message.answer(
        "✏️ Отправьте исправленный текст, фото или видео для рассылки.",
        reply_markup=cancel_keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery):
    if callback.from_user.id not in load_admins():
        return
    broadcast_draft.pop(callback.from_user.id, None)
    admin_process.pop(callback.from_user.id, None)
    await callback.message.answer("❌ Рассылка отменена.", reply_markup=admin_keyboard)
    await callback.answer()


@router.callback_query(F.data == "broadcast_prepare")
async def broadcast_prepare(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id not in load_admins():
        return
    draft = broadcast_draft.get(admin_id)
    if not draft:
        await callback.answer("Рассылка уже обработана или устарела", show_alert=True)
        return
    await callback.message.answer(
        "🚨 <b>ПОСЛЕДНЕЕ ПОДТВЕРЖДЕНИЕ</b>\n\n"
        f"👥 Получателей: <b>{draft['recipient_count']}</b>\n\n"
        "⚠️ После подтверждения сообщение будет отправлено всем зарегистрированным участникам.",
        reply_markup=broadcast_final_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_send")
async def broadcast_send(callback: CallbackQuery):
    admin_id = callback.from_user.id
    if admin_id not in load_admins():
        return
    draft = broadcast_draft.pop(admin_id, None)
    if not draft:
        await callback.answer("Рассылка уже обработана или устарела", show_alert=True)
        return

    conn = db()
    users = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
    conn.close()

    success = failed = 0
    failed_reasons = {}
    for uid in users:
        if is_user_blocked(uid):
            continue
        try:
            await send_content_to_chat(
                uid, draft["media_type"], draft["media_id"],
                "📢 <b>ВАЖНОЕ ОБЪЯВЛЕНИЕ</b>\n\n" + (draft["text"] or ""),
                None,
            )
            success += 1
        except Exception as exc:
            failed += 1
            reason = type(exc).__name__
            failed_reasons[reason] = failed_reasons.get(reason, 0) + 1

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO broadcasts(admin_id,created_at,text,media_type,media_id,success_count,failed_count) VALUES(?,?,?,?,?,?,?)",
        (admin_id, now_str(), draft["text"], draft["media_type"], draft["media_id"], success, failed),
    )
    conn.commit()
    conn.close()

    total = success + failed
    percent = (success / total * 100) if total else 0
    reason_text = ""
    if failed_reasons:
        reason_text = "\n\n🔎 <b>Причины ошибок:</b>\n" + "\n".join(
            f"• {html.escape(k)} — {v}" for k, v in failed_reasons.items()
        )
    log_admin_action(admin_id, "broadcast", details=f"success={success};failed={failed}")
    await callback.message.answer(
        "📢 <b>РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
        f"👥 Получателей: <b>{total}</b>\n"
        f"✅ Доставлено: <b>{success}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>\n"
        f"📈 Успешность: <b>{percent:.1f}%</b>"
        f"{reason_text}",
        reply_markup=admin_keyboard,
    )
    await callback.answer("Рассылка завершена")


@router.callback_query(F.data.startswith("confirm_reply:"))
async def confirm_reply(callback: CallbackQuery):
    user_id = callback.from_user.id
    kind = callback.data.split(":", 1)[1]
    pending = pending_user_reply if kind == "u" else pending_admin_reply
    draft = pending.pop(user_id, None)
    if not draft:
        await callback.answer("Сообщение уже обработано или устарело", show_alert=True)
        return

    appeal_id = draft["appeal_id"]
    row = get_appeal(appeal_id)
    if not row or row[6] == "closed":
        await callback.message.answer("⚠️ Обращение уже закрыто.", reply_markup=employee_keyboard if kind == "u" else admin_keyboard)
        await callback.answer()
        return

    if kind == "u":
        if row[1] != user_id or is_user_blocked(user_id):
            await callback.answer("Обращение недоступно", show_alert=True)
            return
        insert_message(appeal_id, user_id, "employee", draft["text"], draft["media_type"], draft["media_id"])
        set_status(appeal_id, "waiting_admin")
        await callback.message.answer(
            "✅ <b>Сообщение отправлено поддержке.</b>\nОжидайте ответа.",
            reply_markup=employee_ticket_keyboard(appeal_id, can_reply=False),
        )
        for admin_id in load_admins():
            try:
                await send_content_to_chat(
                    admin_id, draft["media_type"], draft["media_id"],
                    f"📩 <b>Новое сообщение по тикету №{appeal_id}</b>\n\n{html.escape(draft['text'] or 'Без текста')}",
                    admin_ticket_keyboard(appeal_id, can_reply=True),
                )
            except Exception:
                logging.exception("Ошибка уведомления администратора")
    else:
        if user_id not in load_admins():
            await callback.answer("Нет доступа", show_alert=True)
            return
        insert_message(appeal_id, user_id, "admin", draft["text"], draft["media_type"], draft["media_id"])
        set_status(appeal_id, "waiting_user")
        target_user_id = row[1]
        try:
            await send_content_to_chat(
                target_user_id, draft["media_type"], draft["media_id"],
                f"📩 <b>Ответ поддержки по тикету №{appeal_id}</b>\n\n{html.escape(draft['text'] or 'Без текста')}",
                employee_ticket_keyboard(appeal_id, can_reply=True),
            )
            await callback.message.answer(
                f"✅ <b>Ответ по тикету №{appeal_id} доставлен.</b>\nТеперь ход диалога передан сотруднику.",
                reply_markup=admin_keyboard,
            )
            release_appeal_lock(appeal_id, user_id)
            log_admin_action(user_id, "send_reply", appeal_id)
        except Exception:
            await callback.message.answer(
                "⚠️ Ответ записан в историю, но доставить его сотруднику не удалось.",
                reply_markup=admin_keyboard,
            )
    await callback.answer("Отправлено")


@router.callback_query(F.data.startswith("edit_reply:"))
async def edit_reply(callback: CallbackQuery):
    user_id = callback.from_user.id
    kind = callback.data.split(":", 1)[1]
    pending = pending_user_reply if kind == "u" else pending_admin_reply
    draft = pending.pop(user_id, None)
    if not draft:
        await callback.answer("Сообщение уже обработано или устарело", show_alert=True)
        return
    if kind == "u":
        compose_user[user_id] = draft["appeal_id"]
        await callback.message.answer("✏️ Отправьте исправленный текст, фото или видео.", reply_markup=cancel_keyboard)
    else:
        compose_admin[user_id] = draft["appeal_id"]
        await callback.message.answer("✏️ Отправьте исправленный ответ, фото или видео.", reply_markup=cancel_keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_reply:"))
async def cancel_reply(callback: CallbackQuery):
    user_id = callback.from_user.id
    kind = callback.data.split(":", 1)[1]
    pending = pending_user_reply if kind == "u" else pending_admin_reply
    draft = pending.pop(user_id, None)
    if kind == "a" and draft:
        release_appeal_lock(draft["appeal_id"], user_id)
        log_admin_action(user_id, "cancel_reply", draft["appeal_id"])
    await callback.message.answer(
        "❌ Отправка отменена.",
        reply_markup=employee_keyboard if kind == "u" else admin_keyboard,
    )
    await callback.answer()


@router.message()
async def catch_messages(message: Message):
    """Single controlled message gateway for appeal content and admin replies."""
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        return

    # Employee knowledge-base search is intentionally separate from admin_process.
    # This keeps the employee flow available without granting any admin workflow.
    if user_id not in load_admins() and knowledge_search_user.get(user_id):
        q = (message.text or "").strip()
        if not q:
            await message.answer("❌ Введите непустой запрос.", reply_markup=cancel_keyboard)
            return
        knowledge_search_user.pop(user_id, None)
        conn = db()
        rows = conn.execute(
            "SELECT title, body FROM knowledge_base WHERE active=1 ORDER BY id DESC"
        ).fetchall()
        conn.close()
        needle = q.casefold()
        matches = [row for row in rows if needle in (row[0] or "").casefold() or needle in (row[1] or "").casefold()]
        matches = matches[:20]
        if not matches:
            await message.answer(
                "🔎 <b>Ничего не найдено</b>\n\n"
                "В утверждённой базе знаний нет подходящего материала.\n\n"
                "Можно создать обращение в поддержку.",
                reply_markup=employee_keyboard,
            )
            return
        await message.answer(
            f"🔎 <b>Нашёл материалов: {len(matches)}</b>\n\n"
            "Выберите подходящий ответ из результатов ниже.",
            reply_markup=employee_keyboard,
        )
        for title, body in matches:
            await message.answer(
                f"📚 <b>{html.escape(title)}</b>\n\n{html.escape(body)}",
                reply_markup=employee_keyboard,
            )
        return

    # Admin simple text workflows: announcements, knowledge base, search.
    if user_id in load_admins() and user_id in admin_process:
        mode = admin_process[user_id]
        if mode == "announcement_title":
            admin_process[user_id] = "announcement_body"
            broadcast_draft[user_id] = {"title": message.text or "Без заголовка"}
            await message.answer("📢 Теперь отправьте текст объявления:", reply_markup=cancel_keyboard)
            return
        if mode == "announcement_body":
            draft=broadcast_draft.pop(user_id,{})
            conn=db(); conn.execute("INSERT INTO announcements(title,body,created_at,active) VALUES(?,?,?,1)",(draft.get("title","Объявление"),message.text or "",now_str())); conn.commit(); conn.close()
            admin_process.pop(user_id,None)
            await message.answer("✅ Объявление опубликовано и закреплено в разделе «Важные объявления». ",reply_markup=admin_keyboard)
            return
        if mode == "kb_title":
            admin_process[user_id]="kb_body"; broadcast_draft[user_id]={"title":message.text or "Материал"}
            await message.answer("📚 Теперь отправьте текст материала:",reply_markup=cancel_keyboard); return
        if mode == "kb_body":
            draft=broadcast_draft.pop(user_id,{})
            body=(message.text or "").strip()
            if not body:
                await message.answer("❌ Текст материала не может быть пустым."); return
            conn=db(); conn.execute("INSERT INTO knowledge_base(title,body,created_at,active) VALUES(?,?,?,1)",(draft.get("title","Материал"),body,now_str())); conn.commit(); conn.close(); admin_process.pop(user_id,None)
            log_admin_action(user_id, "knowledge_add", details=f"title={draft.get('title','Материал')}")
            await message.answer("✅ <b>Материал сохранён.</b>\n\nОн уже доступен сотрудникам через поиск базы знаний.",reply_markup=admin_keyboard); return
        if mode.startswith("kb_edit_body:"):
            try:
                kid=int(mode.split(":",1)[1])
            except ValueError:
                admin_process.pop(user_id,None); await message.answer("❌ Сессия редактирования устарела.", reply_markup=admin_keyboard); return
            body=(message.text or "").strip()
            if not body:
                await message.answer("❌ Новый текст не может быть пустым."); return
            conn=db(); row=conn.execute("SELECT title FROM knowledge_base WHERE id=? AND active=1",(kid,)).fetchone()
            if not row:
                conn.close(); admin_process.pop(user_id,None); await message.answer("❌ Материал не найден или уже архивирован.", reply_markup=admin_keyboard); return
            conn.execute("UPDATE knowledge_base SET body=? WHERE id=?",(body,kid)); conn.commit(); conn.close(); admin_process.pop(user_id,None)
            log_admin_action(user_id, "knowledge_edit", details=f"knowledge_id={kid}")
            await message.answer(f"✅ <b>Материал обновлён.</b>\n\n«{html.escape(row[0])}»",reply_markup=admin_keyboard); return
        if mode in {"kb_search","appeal_search"}:
            q=(message.text or "").strip()
            if not q:
                await message.answer("❌ Введите непустой запрос."); return
            admin_process.pop(user_id,None)
            conn=db()
            if mode == "kb_search":
                all_rows = conn.execute(
                    "SELECT title,body FROM knowledge_base WHERE active=1 ORDER BY id DESC"
                ).fetchall()
                conn.close()
                needle = q.casefold()
                rows = [r for r in all_rows if needle in (r[0] or "").casefold() or needle in (r[1] or "").casefold()][:20]
                if not rows:
                    await message.answer("🔎 Ничего не найдено в базе знаний.", reply_markup=admin_keyboard)
                    return
                await message.answer("🔎 <b>Результаты поиска</b>", reply_markup=admin_keyboard)
                for title,body in rows:
                    await message.answer(f"📚 <b>{html.escape(title)}</b>\n\n{html.escape(body)}", reply_markup=admin_keyboard)
                return
            like=f"%{q}%"
            rows=conn.execute("SELECT id,category,urgency,user_name,is_anon,created_at,status,last_activity_at FROM appeals WHERE CAST(id AS TEXT)=? OR user_name LIKE ? OR category LIKE ? OR id IN (SELECT appeal_id FROM messages_log WHERE text LIKE ?) ORDER BY id DESC LIMIT 50",(q,like,like,like)).fetchall(); conn.close()
            await send_appeal_rows(message,rows,"🔎 <b>Результат поиска</b>")
            return

    # 0) Admin broadcast mode — draft first, send only after confirmation.
    if user_id in load_admins() and admin_process.get(user_id) == "broadcast":
        content = extract_content(message)
        if content is None:
            await message.answer("❌ Для рассылки разрешены только текст, фото и видео.")
            return
        media_type, media_id, text = content
        if not text and media_type == "text":
            await message.answer("❌ Текст рассылки не может быть пустым.")
            return
        conn = db()
        recipients = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        recipient_count = sum(1 for (uid,) in recipients if not is_user_blocked(uid))
        broadcast_draft[user_id] = {
            "media_type": media_type, "media_id": media_id, "text": text,
            "recipient_count": recipient_count
        }
        admin_process.pop(user_id, None)
        preview = (
            "📢 <b>ПРЕДПРОСМОТР МАССОВОЙ РАССЫЛКИ</b>\n\n"
            f"👥 Получателей: <b>{recipient_count}</b>\n\n"
            f"{preview_content_text(media_type, text)}\n\n"
            "⚠️ Сообщение будет отправлено всем зарегистрированным участникам."
        )
        await message.answer(preview, reply_markup=broadcast_confirm_keyboard())
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
                [InlineKeyboardButton(text="🟢 Обычное", callback_data="urgency:normal"),
                 InlineKeyboardButton(text="🟡 Важное", callback_data="urgency:important")],
                [InlineKeyboardButton(text="🔴 Срочное", callback_data="urgency:urgent")],
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
        pending_user_reply[user_id] = {
            "appeal_id": appeal_id, "media_type": media_type,
            "media_id": media_id, "text": text
        }
        await message.answer(
            f"📋 <b>ПРОВЕРЬТЕ СООБЩЕНИЕ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
            f"Обращение №{appeal_id}\n\n{preview_content_text(media_type, text)}\n\n"
            "⚠️ Сообщение ещё НЕ отправлено.",
            reply_markup=reply_confirm_keyboard("user"),
        )
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
        pending_admin_reply[user_id] = {
            "appeal_id": appeal_id, "media_type": media_type,
            "media_id": media_id, "text": text
        }
        await message.answer(
            f"📋 <b>ПРОВЕРЬТЕ ОТВЕТ ПЕРЕД ОТПРАВКОЙ</b>\n\n"
            f"Обращение №{appeal_id}\n\n{preview_content_text(media_type, text)}\n\n"
            "⚠️ Ответ ещё НЕ отправлен сотруднику.",
            reply_markup=reply_confirm_keyboard("admin"),
        )
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


async def remind_waiting_admins():
    while True:
        await asyncio.sleep(300)
        try:
            now = datetime.now(BOT_TZ)
            conn = db()
            rows = conn.execute("SELECT id,user_id,category,urgency,created_at,last_activity_at,status FROM appeals WHERE status!='closed'").fetchall()
            for appeal_id,user_id,category,urgency,created,last_activity,status in rows:
                base = last_activity or created
                try:
                    age = (now - datetime.strptime(base,"%Y-%m-%d %H:%M:%S").replace(tzinfo=BOT_TZ)).total_seconds()/60
                except Exception:
                    continue
                if status != "waiting_admin" or age < REMINDER_FIRST_MINUTES:
                    continue
                r=conn.execute("SELECT last_sent_at FROM appeal_reminders WHERE appeal_id=?",(appeal_id,)).fetchone()
                should = not r or not r[0]
                if r and r[0]:
                    try: should = (now-datetime.strptime(r[0],"%Y-%m-%d %H:%M:%S").replace(tzinfo=BOT_TZ)).total_seconds()/60 >= REMINDER_REPEAT_MINUTES
                    except Exception: should=True
                if should:
                    conn.execute("INSERT INTO appeal_reminders(appeal_id,last_sent_at,reminder_count) VALUES(?,?,1) ON CONFLICT(appeal_id) DO UPDATE SET last_sent_at=excluded.last_sent_at,reminder_count=appeal_reminders.reminder_count+1",(appeal_id,now_str()))
                    for admin_id in load_admins():
                        try:
                            await bot.send_message(admin_id,f"⏱ <b>Напоминание об обращении №{appeal_id}</b>\n\n{urgency} {html.escape(category)}\nОжидает ответа уже <b>{int(age)} мин.</b>",reply_markup=admin_ticket_keyboard(appeal_id,can_reply=True))
                        except Exception: pass
            conn.commit(); conn.close()
        except Exception:
            logging.exception("Ошибка напоминаний")


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


def backup_database() -> None:
    try:
        safe_backup_database()
    except Exception:
        logging.exception("Не удалось создать резервную копию базы")


async def periodic_backup():
    while True:
        await asyncio.sleep(21600)
        backup_database()
        if not verify_database():
            logging.error("ВНИМАНИЕ: integrity_check базы данных не прошёл!")


# ============================================================
# MAIN
# ============================================================
async def main():
    dp.include_router(router)
    init_db()
    if not verify_database():
        raise RuntimeError("❌ Проверка целостности базы данных не пройдена")
    asyncio.create_task(auto_close_inactive_tickets())
    asyncio.create_task(schedule_weekly_polls())
    asyncio.create_task(periodic_backup())
    asyncio.create_task(remind_waiting_admins())
    backup_database()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
