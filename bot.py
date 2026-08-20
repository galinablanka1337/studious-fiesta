from datetime import datetime
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS_FILE = "admins.txt"
SUPER_ADMIN_ID = 762076580

if not TOKEN:
    raise ValueError("❌ Ошибка: Не найден BOT_TOKEN в переменных окружения хостинга!")

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

# --- PERSISTENT ADMIN STORAGE ---
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
adding_admin_process = set()
removing_admin_process = set()
waiting_for_appeal_text = set()

# Временное хранилище: {user_id: {"category": str, "text": str}}
pending_appeals = {}

# --- IMAGES ---
IMG_NIGHT = "image.png"     
IMG_EVENING = "image_2.png" 
IMG_MORNING = "image_3.png" 
IMG_DAY = "image_4.png"     
IMG_THANKS = "image_5.png"  

# --- KEYBOARDS ---
employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Новые идеи"), KeyboardButton(text="⚠️ Жалобы")],
        [KeyboardButton(text="⚔️ Конфликт"), KeyboardButton(text="🤝 Помощь сотруднику")],
        [KeyboardButton(text="🌴 Отпуска"), KeyboardButton(text="🏥 Больничные")],
        [KeyboardButton(text="⏱️ Графики и перерывы"), KeyboardButton(text="📋 Обязанности кассира")],
        [KeyboardButton(text="💬 Другие вопросы")]
    ],
    resize_keyboard=True
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика бота"), KeyboardButton(text="📁 Актуальные обращения")],
        [KeyboardButton(text="👥 Управление админами"), KeyboardButton(text="📈 Отчеты по дисциплине")],
        [KeyboardButton(text="🔙 В обычное меню")]
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

# --- TEXTS ДЛЯ СПРАВОЧНИКА ---
VACATION_TEXT = "🌴 **Информация по отпускам:**\n\n• Заявление подается за 2 недели до начала."
SICK_LEAVE_TEXT = "🏥 **Порядок по больничным листам:**\n\n• После закрытия больничного отправьте его номер в кадры."
SCHEDULE_TEXT = "⏱️ **График и рабочее время:**\n\n• Используется приложение «Работа со Вкусом»."
DUTIES_TEXT = "📋 **Основные обязанности:**\n\n• Сбор заказов, контроль сроков годности."

# --- HANDLERS ---
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
        text = f"{greeting} Приветствую, Администратор! 👑\nПанель управления активирована."
        keyboard = admin_keyboard
    else:
        text = f"{greeting} 👋 Твой личный помощник во ВкусВилле на связи. Выберите нужную категорию обращения или раздел:"
        keyboard = employee_keyboard

    try:
        await message.answer_photo(photo=FSInputFile(IMG_MORNING), caption=text, reply_markup=keyboard)
    except Exception:
        await message.answer(text, reply_markup=keyboard)

async def send_response_with_appeal_button(message: Message, text: str, category: str):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"✍️ Написать по теме: {category}", callback_data=f"cat_{category}")]]
    )
    try:
        await message.answer_photo(photo=FSInputFile(IMG_THANKS), caption=text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception:
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

# Перехват нажатий на категории или инлайн-кнопки
categories_list = ["💡 Новые идеи", "⚠️ Жалобы", "⚔️ Конфликт", "🤝 Помощь сотруднику", "💬 Другие вопросы", "🌴 Отпуска", "🏥 Больничные", "⏱️ Графики и перерывы", "📋 Обязанности кассира"]

@router.message(F.text.in_(categories_list))
async def handle_category_selection(message: Message):
    user_id = message.from_user.id
    category = message.text
    
    # Если это чисто справочные темы — выдаем справку с кнопкой, остальные сразу просят текст обращения
    if category == "🌴 Отпуска":
        await send_response_with_appeal_button(message, VACATION_TEXT, category)
        return
    elif category == "🏥 Больничные":
        await send_response_with_appeal_button(message, SICK_LEAVE_TEXT, category)
        return
    elif category == "⏱️ Графики и перерывы":
        await send_response_with_appeal_button(message, SCHEDULE_TEXT, category)
        return
    elif category == "📋 Обязанности кассира":
        await send_response_with_appeal_button(message, DUTIES_TEXT, category)
        return

    # Для категорий обращений запускаем сбор текста
    waiting_for_appeal_text.add(user_id)
    pending_appeals[user_id] = {"category": category}
    await message.answer(
        f"✍️ Вы выбрали категорию: **{category}**\n\nНапишите суть вашего обращения одним сообщением:",
        reply_markup=cancel_keyboard,
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("cat_"))
async def callback_category_selection(callback: CallbackQuery):
    user_id = callback.from_user.id
    category = callback.data.replace("cat_", "")
    
    waiting_for_appeal_text.add(user_id)
    pending_appeals[user_id] = {"category": category}
    
    await callback.message.answer(
        f"✍️ Вы выбрали категорию: **{category}**\n\nНапишите суть вашего обращения одним сообщением:",
        reply_markup=cancel_keyboard,
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(F.text == "🔙 Отменить и в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    waiting_for_appeal_text.discard(user_id)
    adding_admin_process.discard(user_id)
    removing_admin_process.discard(user_id)
    pending_appeals.pop(user_id, None)
    
    admins = load_admins()
    if user_id in admins:
        await message.answer("Возвращаюсь в админ-панель:", reply_markup=admin_keyboard)
    else:
        await message.answer("Главное меню:", reply_markup=employee_keyboard)

@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("👥 Меню управления администраторами:", reply_markup=manage_admins_keyboard)

@router.message(F.text == "🔙 Назад в админ-панель")
async def back_to_admin(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("Возвращаюсь в панель управления:", reply_markup=admin_keyboard)

@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if message.from_user.id in load_admins():
        admins = load_admins()
        admins_list_str = "\n".join([f"• `{aid}`" for aid in admins])
        await message.answer(f"📋 **Список текущих администраторов:**\n\n{admins_list_str}", parse_mode="Markdown")

@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    if message.from_user.id in load_admins():
        adding_admin_process.add(message.from_user.id)
        await message.answer("✍️ Отправьте **Telegram ID** нового администратора (только цифры):", parse_mode="Markdown", reply_markup=cancel_keyboard)

@router.message(F.text == "➖ Удалить админа")
async def ask_remove_admin(message: Message):
    if message.from_user.id in load_admins():
        removing_admin_process.add(message.from_user.id)
        await message.answer("✍️ Отправьте **Telegram ID** администратора, которого нужно удалить:", parse_mode="Markdown", reply_markup=cancel_keyboard)

@router.message(F.text)
async def process_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    admins = load_admins()

    if user_id in adding_admin_process:
        adding_admin_process.remove(user_id)
        if text.isdigit():
            admins.add(int(text))
            save_admins(admins)
            await message.answer(f"✅ Пользователь `{text}` успешно добавлен!", parse_mode="Markdown", reply_markup=manage_admins_keyboard)
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
                await message.answer(f"🗑️ Пользователь `{rem_id}` удален.", reply_markup=manage_admins_keyboard)
            else:
                await message.answer("⚠️ Такого ID нет в списке.", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Ошибка. ID должен состоять только из цифр.", reply_markup=manage_admins_keyboard)
        return

    # Обработка введенного текста обращения -> предложение выбрать анонимность
    if user_id in waiting_for_appeal_text:
        waiting_for_appeal_text.remove(user_id)
        if user_id in pending_appeals:
            pending_appeals[user_id]["text"] = text
        else:
            pending_appeals[user_id] = {"category": "💬 Другие вопросы", "text": text}

        category = pending_appeals[user_id]["category"]
        confirm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🕵️‍♂️ Отправить анонимно", callback_data="send_anon")],
                [InlineKeyboardButton(text="👤 Отправить с именем", callback_data="send_named")],
                [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_send")]
            ]
        )
        await message.answer(
            f"📁 **Категория:** {category}\n📄 **Ваш текст:**\n\n> {text}\n\nКак вы хотите отправить это обращение?",
            parse_mode="Markdown",
            reply_markup=confirm_keyboard
        )
        return

    # Действия админа
    if user_id in admins:
        if text == "🔙 В обычное меню":
            await message.answer("Переключаюсь в меню сотрудника:", reply_markup=employee_keyboard)
            return
        elif text == "📊 Статистика бота":
            await message.answer("📊 **Статистика бота:**\n\n• Обращений за сегодня: 0", parse_mode="Markdown")
            return
        elif text == "📁 Актуальные обращения":
            await message.answer("📁 **Актуальные обращения:**\n\nВсе новые заявки поступают сюда с указанием категории.", parse_mode="Markdown")
            return
        elif text == "📈 Отчеты по дисциплине":
            await message.answer("📈 **Отчеты по дисциплине:**\n\n• Нарушений не зафиксировано.", parse_mode="Markdown")
            return

    # Если бот не понял текст
    if user_id in admins:
        await message.answer("Админ-панель:", reply_markup=admin_keyboard)
    else:
        await message.answer(
            "Хм, я не совсем понял запрос 🤔 Воспользуйтесь кнопками меню ниже:",
            reply_markup=employee_keyboard
        )

# --- ФИНАЛЬНАЯ ОТПРАВКА АДМИНАМ ---
@router.callback_query(F.data.in_({"send_anon", "send_named", "cancel_send"}))
async def process_appeal_choice(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = pending_appeals.pop(user_id, None)

    if callback.data == "cancel_send":
        await callback.message.edit_text("❌ Отправка обращения отменена.", reply_markup=None)
        await callback.answer()
        return

    if not data:
        await callback.message.edit_text("⚠️ Срок действия сессии истек.", reply_markup=None)
        await callback.answer()
        return

    category = data.get("category", "💬 Другие вопросы")
    text = data.get("text", "")

    is_anon = (callback.data == "send_anon")
    if is_anon:
        header = f"🚨 **Новое АНОНИМНОЕ обращение**\n📂 **Категория:** {category}"
    else:
        user_name = callback.from_user.full_name
        username = f" (@{callback.from_user.username})" if callback.from_user.username else ""
        header = f"🚨 **Обращение от сотрудника:** {user_name}{username} (ID: `{user_id}`)\n📂 **Категория:** {category}"

    appeal_full_text = f"{header}\n\n{text}"
    admins = load_admins()

    admin_action_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить / Прочитано", callback_data="admin_ack")]]
    )

    for admin_id in admins:
        try:
            await bot.send_message(admin_id, appeal_full_text, parse_mode="Markdown", reply_markup=admin_action_keyboard)
        except Exception:
            pass

    await callback.message.edit_text("✅ Ваше обращение успешно отправлено администраторам!", reply_markup=None)
    await callback.answer()

@router.callback_query(F.data == "admin_ack")
async def admin_acknowledge(callback: CallbackQuery):
    if callback.from_user.id in load_admins():
        await callback.message.edit_text(callback.message.text + "\n\n✅ *Обращение отмечено как прочитанное.*", parse_mode="Markdown")
        await callback.answer("Статус обновлен!")
    else:
        await callback.answer("У вас нет прав администратора.", show_alert=True)

async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
