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

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получаем токены из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS_ENV = os.getenv("ADMIN_ID", "")

# Превращаем строку с админами в список чисел
ADMIN_IDS = []
for admin_str in ADMINS_ENV.split(","):
    admin_str = admin_str.strip()
    if admin_str.isdigit():
        ADMIN_IDS.append(int(admin_str))

if not BOT_TOKEN or not ADMIN_IDS:
    logging.error("Не заданы BOT_TOKEN или ADMIN_ID в переменных окружения!")

DB_FILE = "feedback.db"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ==========================================
# БАЗА ДАННЫХ
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT,
            text TEXT,
            status TEXT DEFAULT 'Новое 📥',
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


# ==========================================
# МАШИНА СОСТОЯНИЙ (FSM)
# ==========================================
class FeedbackState(StatesGroup):
    choosing_category = State()
    waiting_for_text = State()
    replying_to_user = State()


# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Новое обращение", callback_data="new_ticket"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📂 Мои обращения", callback_data="my_tickets"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💡 Частые вопросы (FAQ)", callback_data="faq_menu"
                )
            ],
        ]
    )


def categories_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Зарплата и выплаты", callback_data="cat_Зарплата"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 График и условия труда", callback_data="cat_Условия"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💡 Идеи и улучшение процессов",
                    callback_data="cat_Идеи",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Жалоба / Конфликт", callback_data="cat_Жалоба"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Другое", callback_data="cat_Другое"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в меню", callback_data="back_main"
                )
            ],
        ]
    )


def faq_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❓ Как рассчитываются премии?", callback_data="faq_1"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Куда обращаться по форме?", callback_data="faq_2"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад в меню", callback_data="back_main"
                )
            ],
        ]
    )


# ==========================================
# ОБРАБОТЧИКИ КОМАНД И КНОПОК
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "💚 <b>Добро пожаловать в официальный сервис обратной связи ВкусВилл!</b>\n\n"
        "Здесь вы можете безопасно и анонимно поделиться своей идеей, задать вопрос или сообщить о проблеме. Ваше мнение помогает нам становиться лучше каждый день!\n\n"
        "Выберите действие ниже:",
        reply_markup=main_menu(),
    )


# --- СТАТИСТИКА ДЛЯ АДМИНОВ (/stats) ---
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Всего обращений
    cursor.execute("SELECT COUNT(*) FROM tickets")
    total = cursor.fetchone()[0]

    # Новые
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE status LIKE '%Новое%'")
    new_count = cursor.fetchone()[0]

    # Решенные
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE status LIKE '%Решено%'")
    resolved_count = cursor.fetchone()[0]

    # По категориям
    cursor.execute(
        "SELECT category, COUNT(*) FROM tickets GROUP BY category"
    )
    categories = cursor.fetchall()

    conn.close()

    stats_text = (
        f"📊 <b>Статистика сервиса обратной связи ВкусВилл</b>\n\n"
        f"📌 Всего обращений: <b>{total}</b>\n"
        f"📥 Новых (ожидают): <b>{new_count}</b>\n"
        f"✅ Решенных: <b>{resolved_count}</b>\n\n"
        f"📂 <b>По категориям:</b>\n"
    )

    for cat, count in categories:
        stats_text += f"• {cat}: <b>{count}</b>\n"

    await message.answer(stats_text)


@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "💚 <b>Главное меню ВкусВилл:</b>\nВыберите действие:",
        reply_markup=main_menu(),
    )
    await callback.answer()


# --- FAQ ---
@dp.callback_query(F.data == "faq_menu")
async def show_faq(callback: CallbackQuery):
    await callback.message.edit_text(
        "💡 <b>Частые вопросы (FAQ):</b>\nВыберите интересующую тему:",
        reply_markup=faq_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"faq_1", "faq_2"}))
async def show_faq_answer(callback: CallbackQuery):
    if callback.data == "faq_1":
        text = (
            "💰 <b>Как рассчитываются премии?</b>\n\n"
            "Премиальная часть зависит от выполнения показателей конкретной торговой точки / отдела, "
            "соблюдения стандартов качества и личного вклада."
        )
    else:
        text = (
            "👕 <b>Куда обращаться по поводу формы?</b>\n\n"
            "По вопросам выдачи и замены одежды обратитесь к вашему управляющему или напишите через раздел «📋 Новое обращение»."
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ К списку вопросов", callback_data="faq_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В главное меню", callback_data="back_main"
                )
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# --- СОЗДАНИЕ ОБРАЩЕНИЯ ---
@dp.callback_query(F.data == "new_ticket")
async def new_ticket_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FeedbackState.choosing_category)
    await callback.message.edit_text(
        "📋 <b>Создание обращения</b>\n\nВыберите категорию вашего вопроса:",
        reply_markup=categories_menu(),
    )
    await callback.answer()


@dp.callback_query(
    StateFilter(FeedbackState.choosing_category), F.data.startswith("cat_")
)
async def process_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split("_")[1]
    await state.update_data(category=category)
    await state.set_state(FeedbackState.waiting_for_text)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить", callback_data="back_main"
                )
            ]
        ]
    )
    await callback.message.edit_text(
        f"📂 Категория: <b>{category}</b>\n\n"
        "✍️ Напишите текст вашего обращения одним сообщением:",
        reply_markup=cancel_kb,
    )
    await callback.answer()


@dp.message(StateFilter(FeedbackState.waiting_for_text))
async def process_text(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("category")
    text = message.text
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tickets (user_id, category, text, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, category, text, created_at),
    )
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        f"✅ <b>Ваше обращение успешно отправлено!</b>\n\n"
        f"🆔 Номер тикета: <code>#{ticket_id}</code>\n"
        f"📂 Категория: {category}\n"
        f"Статус: На рассмотрении 📥\n\n"
        f"Вы можете отслеживать статус в разделе «📂 Мои обращения».",
        reply_markup=main_menu(),
    )

    # Рассылаем уведомление ВСЕМ администраторам с уникальным ID сообщения (сохраняем в память/базу если нужно, либо шлем индивидуально)
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить",
                    callback_data=f"reply_{ticket_id}_{message.from_user.id}",
                )
            ]
        ]
    )

    # Словарь или таблица для хранения message_id сообщений админам (чтобы потом их редактировать)
    # Сделаем проще: пришлем каждому админу уведомление
    for admin_id in ADMIN_IDS:
        try:
            sent_msg = await bot.send_message(
                admin_id,
                f"🚨 <b>Новое анонимное обращение #{ticket_id}</b>\n\n"
                f"📂 <b>Категория:</b> {category}\n"
                f"📅 <b>Дата:</b> {created_at}\n\n"
                f"💬 <b>Текст:</b>\n{text}",
                reply_markup=admin_kb,
            )
            # Сохраняем ID сообщения админа в базу, чтобы потом убрать у них кнопку
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS admin_notifications (ticket_id INTEGER, admin_id INTEGER, message_id INTEGER)"
            )
            cur.execute(
                "INSERT INTO admin_notifications VALUES (?, ?, ?)",
                (ticket_id, admin_id, sent_msg.message_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")


# --- МОИ ОБРАЩЕНИЯ ---
@dp.callback_query(F.data == "my_tickets")
async def show_my_tickets(callback: CallbackQuery):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, category, status, created_at FROM tickets WHERE user_id = ? ORDER BY id DESC LIMIT 5",
        (callback.from_user.id,),
    )
    tickets = cursor.fetchall()
    conn.close()

    if not tickets:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ В меню", callback_data="back_main"
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            "📂 У вас пока нет активных обращений.", reply_markup=kb
        )
        await callback.answer()
        return

    text = "📂 <b>Ваши последние обращения:</b>\n\n"
    for t_id, cat, status, date in tickets:
        text += f"🔹 <b>#{t_id}</b> | {cat}\n📅 {date}\nСтатус: {status}\n\n"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Создать еще", callback_data="new_ticket"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В меню", callback_data="back_main"
                )
            ],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# --- ОТВЕТ АДМИНА И УДАЛЕНИЕ КНОПОК У ДРУГИХ ---
@dp.callback_query(F.data.startswith("reply_"))
async def admin_start_reply(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет прав администратора!", show_alert=True)
        return

    parts = callback.data.split("_")
    ticket_id = parts[1]
    target_user_id = int(parts[2])

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    if "Решено" in row[0]:
        conn.close()
        await callback.answer(
            "⚠️ Этот тикет уже обработан другим администратором!", show_alert=True
        )
        # Убираем кнопку у текущего нажавшего, раз он уже не актуален
        try:
            await callback.message.edit_text(
                callback.message.text
                + "\n\n❌ <i>Тикет уже закрыт другим администратором.</i>"
            )
        except Exception:
            pass
        return

    conn.close()

    await state.set_state(FeedbackState.replying_to_user)
    await state.update_data(
        target_user_id=target_user_id, ticket_id=ticket_id
    )

    await callback.message.answer(
        f"✍️ Введите текст ответа для тикета <b>#{ticket_id}</b>:"
    )
    await callback.answer()


@dp.message(StateFilter(FeedbackState.replying_to_user))
async def admin_send_reply(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    ticket_id = data.get("ticket_id")
    reply_text = message.text

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    row = cursor.fetchone()

    if row and "Решено" in row[0]:
        conn.close()
        await state.clear()
        await message.answer("⚠️ Ошибка: этот тикет уже был закрыт ранее.")
        return

    # Меняем статус на «Решено ✅»
    cursor.execute(
        "UPDATE tickets SET status = 'Решено ✅' WHERE id = ?", (ticket_id,)
    )

    # Достаем все сохраненные сообщения этого тикета для других админов
    cursor.execute(
        "SELECT admin_id, message_id FROM admin_notifications WHERE ticket_id = ?",
        (ticket_id,),
    )
    notifications = cursor.fetchall()
    conn.commit()
    conn.close()

    await state.clear()

    # Убираем кнопки «Ответить» у ВСЕХ администраторов (включая того, кто ответил)
    for adm_id, msg_id in notifications:
        try:
            await bot.edit_message_reply_markup(
                chat_id=adm_id,
                message_id=msg_id,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Обработано (Закрыто)",
                                callback_data="ignored",
                            )
                        ]
                    ]
                ),
            )
        except Exception:
            pass

    # Отправляем ответ пользователю
    try:
        await bot.send_message(
            target_user_id,
            f"📬 <b>Получен ответ по вашему обращению #{ticket_id}:</b>\n\n"
            f"{reply_text}\n\n"
            f"<i>Спасибо, что помогаете делать ВкусВилл лучше! 💚</i>",
        )
        await message.answer(
            f"✅ Ответ успешно отправлен! Тикет #{ticket_id} закрыт, у других администраторов уведомление обновилось."
        )
    except Exception as e:
        await message.answer(
            f"⚠️ Не удалось отправить сообщение пользователю: {e}"
        )


@dp.callback_query(F.data == "ignored")
async def ignored_cb(callback: CallbackQuery):
    await callback.answer(
        "Этот тикет уже обработан кем-то из коллег.", show_alert=True
    )


# --- ЗАПУСК БОТА ---
async def main():
    init_db()
    logging.info("🤖 Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
