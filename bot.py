import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных окружения!")

if not ADMIN_ID:
    raise RuntimeError("Не задан ADMIN_ID в переменных окружения!")

ADMIN_ID = int(ADMIN_ID)

DB_FILE = "feedback.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            photo_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            admin_answer TEXT
        )
    """)
    conn.commit()
    conn.close()


def create_appeal(user_id, category, text, photo_file_id=None):
    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO appeals
        (user_id, category, text, photo_file_id, status, created_at)
        VALUES (?, ?, ?, ?, 'new', ?)
        """,
        (
            user_id,
            category,
            text,
            photo_file_id,
            datetime.now().strftime("%d.%m.%Y %H:%M"),
        )
    )
    appeal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return appeal_id


def get_appeal(appeal_id):
    conn = get_db()
    result = conn.execute(
        "SELECT * FROM appeals WHERE id = ?",
        (appeal_id,)
    ).fetchone()
    conn.close()
    return result


def get_appeals_by_status(status):
    conn = get_db()
    result = conn.execute(
        "SELECT * FROM appeals WHERE status = ? ORDER BY id DESC",
        (status,)
    ).fetchall()
    conn.close()
    return result


def get_all_appeals():
    conn = get_db()
    result = conn.execute(
        "SELECT * FROM appeals ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return result


def update_status(appeal_id, status):
    conn = get_db()
    conn.execute(
        "UPDATE appeals SET status = ? WHERE id = ?",
        (status, appeal_id)
    )
    conn.commit()
    conn.close()


def save_admin_answer(appeal_id, answer):
    conn = get_db()
    conn.execute(
        """
        UPDATE appeals
        SET admin_answer = ?, status = 'in_work'
        WHERE id = ?
        """,
        (answer, appeal_id)
    )
    conn.commit()
    conn.close()


# ============================================================
# КАТЕГОРИИ
# ============================================================

CATEGORIES = {
    "complaint": "😕 Жалоба",
    "improvement": "💡 Улучшение работы",
    "question": "❓ Вопрос",
    "thanks": "🙏 Благодарность",
    "problem": "🏪 Проблема на рабочем месте",
    "schedule": "📅 График и организация работы",
    "salary": "💰 Зарплата и выплаты",
    "other": "🔹 Другое",
}


# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Новое обращение", callback_data="new_appeal")],
            [InlineKeyboardButton(text="ℹ️ Как это работает", callback_data="about")],
        ]
    )


def categories_keyboard():
    buttons = []
    for key, name in CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"category:{key}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]
        ]
    )


def photo_choice_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📷 Добавить фото", callback_data="add_photo")],
            [InlineKeyboardButton(text="➡️ Продолжить без фото", callback_data="without_photo")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]
        ]
    )


def confirmation_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ ПОДТВЕРДИТЬ ОТПРАВКУ", callback_data="confirm_send")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="edit_appeal")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]
        ]
    )


def admin_panel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Новые", callback_data="admin_list:new")],
            [InlineKeyboardButton(text="🟡 В работе", callback_data="admin_list:in_work")],
            [InlineKeyboardButton(text="✅ Закрытые", callback_data="admin_list:closed")],
            [InlineKeyboardButton(text="📚 Все обращения", callback_data="admin_list:all")],
        ]
    )


def admin_appeal_keyboard(appeal_id, status):
    buttons = []
    if status == "new":
        buttons.append([InlineKeyboardButton(text="🟡 Взять в работу", callback_data=f"admin_work:{appeal_id}")])

    if status != "closed":
        buttons.append([InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply:{appeal_id}")])
        buttons.append([InlineKeyboardButton(text="✅ Закрыть", callback_data=f"admin_close:{appeal_id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_back_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_list_keyboard(appeals):
    buttons = []
    for appeal in appeals:
        status_icon = {"new": "📥", "in_work": "🟡", "closed": "✅"}.get(appeal["status"], "📌")
        button_text = f"{status_icon} #{appeal['id']} • {appeal['created_at']} • {appeal['category']}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"admin_view:{appeal['id']}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Панель руководителя", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================
# СОСТОЯНИЯ
# ============================================================

class AppealState(StatesGroup):
    waiting_text = State()
    waiting_photo = State()
    waiting_photo_upload = State()
    confirmation = State()


class AdminReplyState(StatesGroup):
    waiting_answer = State()


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👨‍💼 <b>Панель руководителя</b>\n\nВыберите раздел:",
            reply_markup=admin_panel_keyboard()
        )
    else:
        await message.answer(
            "👋 <b>Анонимная обратная связь</b>\n\n"
            "Здесь вы можете оставить обращение руководителю анонимно.\n\n"
            "🔒 Ваше имя и username руководителю не показываются.\n\n"
            "Выберите действие:",
            reply_markup=main_menu()
        )


# ============================================================
# НОВОЕ ОБРАЩЕНИЕ
# ============================================================

@dp.callback_query(F.data == "new_appeal")
async def new_appeal(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "📋 <b>Выберите категорию обращения:</b>",
        reply_markup=categories_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("category:"))
async def category_handler(callback: CallbackQuery, state: FSMContext):
    category_key = callback.data.split(":", 1)[1]
    category = CATEGORIES.get(category_key, "🔹 Другое")

    await state.update_data(category=category)
    await state.set_state(AppealState.waiting_text)

    await callback.message.edit_text(
        f"<b>{category}</b>\n\n"
        "📝 Напишите текст обращения.\n\n"
        "После этого я покажу вам готовое обращение перед отправкой.\n\n"
        "🔒 Руководителю оно будет отправлено только после вашего подтверждения.",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@dp.message(AppealState.waiting_text, F.text)
async def receive_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("⚠️ Напишите текст обращения.")
        return

    await state.update_data(text=text)
    await state.set_state(AppealState.waiting_photo)

    await message.answer(
        "📷 Хотите добавить фотографию?\n\nФотография не обязательна.",
        reply_markup=photo_choice_keyboard()
    )


@dp.callback_query(AppealState.waiting_photo, F.data == "add_photo")
async def add_photo(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📷 <b>Отправьте фотографию.</b>\n\n"
        "После фотографии появится предварительный просмотр обращения."
    )
    await state.set_state(AppealState.waiting_photo_upload)
    await callback.answer()


@dp.callback_query(AppealState.waiting_photo, F.data == "without_photo")
async def without_photo(callback: CallbackQuery, state: FSMContext):
    await state.update_data(photo=None)
    await show_preview(callback.message, state)
    await callback.answer()


@dp.message(AppealState.waiting_photo_upload, F.photo)
async def receive_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(photo=photo_id)
    await show_preview(message, state)


async def show_preview(target_message, state: FSMContext):
    data = await state.get_data()
    category = data.get("category", "🔹 Другое")
    text = data.get("text", "")
    photo = data.get("photo")

    preview = (
        "📋 <b>ПРЕДПРОСМОТР ОБРАЩЕНИЯ</b>\n\n"
        f"📂 <b>Категория:</b>\n{category}\n\n"
        f"📝 <b>Текст:</b>\n{text}\n\n"
    )

    if photo:
        preview += "📷 <b>Фотография:</b> прикреплена\n\n"
    else:
        preview += "📷 <b>Фотография:</b> нет\n\n"

    preview += (
        "⚠️ <b>Обратите внимание:</b>\n"
        "После нажатия «Подтвердить отправку» обращение будет передано руководителю."
    )

    await state.set_state(AppealState.confirmation)

    if photo:
        await target_message.answer_photo(
            photo=photo,
            caption=preview,
            reply_markup=confirmation_keyboard()
        )
    else:
        await target_message.answer(
            preview,
            reply_markup=confirmation_keyboard()
        )


@dp.callback_query(AppealState.confirmation, F.data == "confirm_send")
async def confirm_send(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get("category")
    text = data.get("text")
    photo = data.get("photo")

    if not category or not text:
        await callback.answer("Обращение заполнено не полностью.", show_alert=True)
        return

    appeal_id = create_appeal(
        user_id=callback.from_user.id,
        category=category,
        text=text,
        photo_file_id=photo
    )

    appeal = get_appeal(appeal_id)
    await send_to_admin(appeal)
    await state.clear()

    await callback.message.answer(
        f"✅ <b>Обращение #{appeal_id} отправлено.</b>\n\n"
        "Руководитель получил его анонимно.\n\n"
        "Если руководитель ответит, ответ придёт сюда.",
        reply_markup=main_menu()
    )
    await callback.answer("Обращение отправлено!")


@dp.callback_query(AppealState.confirmation, F.data == "edit_appeal")
async def edit_appeal(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get("category", "🔹 Другое")

    await state.set_state(AppealState.waiting_text)
    await callback.message.answer(
        f"✏️ <b>Редактирование</b>\n\nКатегория: {category}\n\nНапишите новый текст обращения.",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Обращение отменено.\n\nНичего руководителю не отправлено.",
        reply_markup=main_menu()
    )
    await callback.answer()


@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Обращение отменено.\n\nНичего руководителю не отправлено.",
        reply_markup=main_menu()
    )


# ============================================================
# ОТПРАВКА РУКОВОДИТЕЛЮ
# ============================================================

async def send_to_admin(appeal):
    status_text = "📥 Новое"
    message_text = (
        "📩 <b>НОВОЕ АНОНИМНОЕ ОБРАЩЕНИЕ</b>\n\n"
        f"🔢 <b>Номер:</b> #{appeal['id']}\n"
        f"📅 <b>Дата:</b> {appeal['created_at']}\n"
        f"📂 <b>Категория:</b> {appeal['category']}\n"
        f"📌 <b>Статус:</b> {status_text}\n\n"
        f"📝 <b>Текст:</b>\n{appeal['text']}\n\n"
        "🔒 Данные сотрудника скрыты."
    )

    keyboard = admin_appeal_keyboard(appeal["id"], appeal["status"])

    if appeal["photo_file_id"]:
        await bot.send_photo(
            ADMIN_ID,
            photo=appeal["photo_file_id"],
            caption=message_text,
            reply_markup=keyboard
        )
    else:
        await bot.send_message(
            ADMIN_ID,
            message_text,
            reply_markup=keyboard
        )


# ============================================================
# ПАНЕЛЬ РУКОВОДИТЕЛЯ
# ============================================================

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "👨‍💼 <b>ПАНЕЛЬ РУКОВОДИТЕЛЯ</b>\n\nВыберите раздел:",
        reply_markup=admin_panel_keyboard()
    )


@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await callback.message.edit_text(
        "👨‍💼 <b>ПАНЕЛЬ РУКОВОДИТЕЛЯ</b>\n\nВыберите раздел:",
        reply_markup=admin_panel_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_list:"))
async def admin_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    mode = callback.data.split(":", 1)[1]

    if mode == "new":
        appeals = get_appeals_by_status("new")
        title = "📥 НОВЫЕ ОБРАЩЕНИЯ"
    elif mode == "in_work":
        appeals = get_appeals_by_status("in_work")
        title = "🟡 ОБРАЩЕНИЯ В РАБОТЕ"
    elif mode == "closed":
        appeals = get_appeals_by_status("closed")
        title = "✅ ЗАКРЫТЫЕ ОБРАЩЕНИЯ"
    else:
        appeals = get_all_appeals()
        title = "📚 ВСЕ ОБРАЩЕНИЯ"

    if not appeals:
        await callback.message.edit_text(
            f"<b>{title}</b>\n\nЗдесь пока нет обращений.",
            reply_markup=admin_panel_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"<b>{title}</b>\n\nВыберите обращение:",
        reply_markup=admin_list_keyboard(appeals)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_view:"))
async def admin_view(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    appeal_id = int(callback.data.split(":")[1])
    appeal = get_appeal(appeal_id)

    if not appeal:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    status_names = {
        "new": "📥 Новое",
        "in_work": "🟡 В работе",
        "closed": "✅ Закрыто"
    }

    text = (
        f"📩 <b>ОБРАЩЕНИЕ #{appeal['id']}</b>\n\n"
        f"📅 <b>Дата:</b> {appeal['created_at']}\n"
        f"📂 <b>Категория:</b> {appeal['category']}\n"
        f"📌 <b>Статус:</b> {status_names.get(appeal['status'])}\n\n"
        f"📝 <b>Текст:</b>\n{appeal['text']}\n\n"
        "🔒 Сотрудник анонимен."
    )

    if appeal["admin_answer"]:
        text += f"\n\n💬 <b>Последний ответ:</b>\n{appeal['admin_answer']}"

    if appeal["photo_file_id"]:
        await callback.message.answer_photo(
            photo=appeal["photo_file_id"],
            caption=text,
            reply_markup=admin_appeal_keyboard(appeal_id, appeal["status"])
        )
    else:
        await callback.message.edit_text(
            text,
            reply_markup=admin_appeal_keyboard(appeal_id, appeal["status"])
        )

    await callback.answer()


@dp.callback_query(F.data.startswith("admin_work:"))
async def admin_work(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    appeal_id = int(callback.data.split(":")[1])
    appeal = get_appeal(appeal_id)

    if not appeal:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    update_status(appeal_id, "in_work")
    await callback.answer("Обращение взято в работу.")
    await callback.message.edit_reply_markup(
        reply_markup=admin_appeal_keyboard(appeal_id, "in_work")
    )


@dp.callback_query(F.data.startswith("admin_reply:"))
async def admin_reply(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    appeal_id = int(callback.data.split(":")[1])
    appeal = get_appeal(appeal_id)

    if not appeal:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    await state.set_state(AdminReplyState.waiting_answer)
    await state.update_data(appeal_id=appeal_id)

    await callback.message.answer(
        f"💬 <b>Ответ на обращение #{appeal_id}</b>\n\n"
        "Напишите ответ сотруднику.\n\n"
        "Ответ будет отправлен через бота анонимно.\n\n"
        "Для отмены используйте /cancel"
    )
    await callback.answer()


@dp.message(AdminReplyState.waiting_answer, F.text)
async def receive_admin_answer(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    data = await state.get_data()
    appeal_id = data.get("appeal_id")
    appeal = get_appeal(appeal_id)

    if not appeal:
        await message.answer("❌ Обращение не найдено.")
        await state.clear()
        return

    answer = message.text.strip()
    if not answer:
        await message.answer("⚠️ Ответ не может быть пустым.")
        return

    save_admin_answer(appeal_id, answer)

    try:
        await bot.send_message(
            appeal["user_id"],
            f"💬 <b>Ответ руководителя</b>\n\n"
            f"По обращению #{appeal_id}:\n\n"
            f"{answer}"
        )
        await message.answer(f"✅ Ответ по обращению #{appeal_id} отправлен сотруднику.")
    except Exception:
        logging.exception("Ошибка отправки ответа")
        await message.answer("⚠️ Не удалось отправить ответ сотруднику.")

    await state.clear()


@dp.callback_query(F.data.startswith("admin_close:"))
async def admin_close(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    appeal_id = int(callback.data.split(":")[1])
    appeal = get_appeal(appeal_id)

    if not appeal:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    update_status(appeal_id, "closed")

    try:
        await bot.send_message(
            appeal["user_id"],
            f"✅ <b>Обращение #{appeal_id} закрыто.</b>\n\nСпасибо за обратную связь."
        )
    except Exception:
        logging.exception("Не удалось уведомить сотрудника")

    await callback.answer("Обращение закрыто.")
    await callback.message.edit_reply_markup(
        reply_markup=admin_appeal_keyboard(appeal_id, "closed")
    )


@dp.callback_query(F.data == "admin_back_list")
async def admin_back_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    appeals = get_all_appeals()
    if not appeals:
        await callback.message.edit_text(
            "📚 <b>Все обращения</b>\n\nОбращений пока нет.",
            reply_markup=admin_panel_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📚 <b>Все обращения</b>\n\nВыберите обращение:",
        reply_markup=admin_list_keyboard(appeals)
    )
    await callback.answer()


@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ <b>Как работает бот</b>\n\n"
        "1️⃣ Вы выбираете категорию.\n\n"
        "2️⃣ Пишете обращение.\n\n"
        "3️⃣ При желании добавляете фото.\n\n"
        "4️⃣ Бот показывает предварительный просмотр.\n\n"
        "5️⃣ Вы нажимаете «Подтвердить отправку».\n\n"
        "6️⃣ Только после подтверждения обращение поступает руководителю.\n\n"
        "🔒 Ваше имя и username руководителю не показываются.",
        reply_markup=main_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "📋 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=main_menu()
    )
    await callback.answer()


@dp.message(StateFilter(None))
async def fallback(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Используйте /admin для панели руководителя.")
        return
    await message.answer("Выберите действие:", reply_markup=main_menu())


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    init_db()
    logging.info("🤖 Анонимный бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
