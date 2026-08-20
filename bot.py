from datetime import datetime
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS_FILE = "admins.txt"
SUPER_ADMIN_ID = 762076580  # Твой главный ID

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
waiting_for_appeal = set()  # Состояние для отправки анонимного обращения

# --- IMAGES ---
IMG_NIGHT = "image.png"     
IMG_EVENING = "image_2.png" 
IMG_MORNING = "image_3.png" 
IMG_DAY = "image_4.png"     
IMG_THANKS = "image_5.png"  

# --- KEYBOARDS ---
employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✍️ Написать анонимное обращение")],
        [KeyboardButton(text="🌴 Отпуска"), KeyboardButton(text="🏥 Больничные")],
        [KeyboardButton(text="⏱️ Графики и перерывы"), KeyboardButton(text="📋 Обязанности кассира")],
        [KeyboardButton(text="⚠️ Дисциплина и прогулы")]
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

# --- TEXTS ---
VACATION_TEXT = "🌴 **Информация по отпускам:**\n\n• Заявление подается за 2 недели до начала."
SICK_LEAVE_TEXT = "🏥 **Порядок по больничным листам:**\n\n• После закрытия больничного отправьте его номер в отдел кадров."
SCHEDULE_TEXT = "⏱️ **График и рабочее время:**\n\n• Используется приложение «Работа со Вкусом»."
DUTIES_TEXT = "📋 **Основные обязанности:**\n\n• Сбор заказов, контроль сроков годности."
DISCIPLINE_TEXT = "⚠️ **Дисциплина:**\n\n• Своевременно предоставляйте оправдательные документы."

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
        text = f"{greeting} 👋 Твой личный помощник во ВкусВилле на связи. Выберите нужный раздел или отправьте обращение:"
        keyboard = employee_keyboard

    try:
        await message.answer_photo(photo=FSInputFile(IMG_MORNING), caption=text, reply_markup=keyboard)
    except Exception:
        await message.answer(text, reply_markup=keyboard)

async def send_response_with_thanks(message: Message, text: str):
    try:
        await message.answer_photo(photo=FSInputFile(IMG_THANKS), caption=text, parse_mode="Markdown")
    except Exception:
        await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "✍️ Написать анонимное обращение")
async def start_appeal(message: Message):
    waiting_for_appeal.add(message.from_user.id)
    await message.answer(
        "✍️ Напишите ваш вопрос или проблему ниже. Сообщение будет отправлено администраторам **анонимно**:",
        reply_markup=cancel_keyboard,
        parse_mode="Markdown"
    )

@router.message(F.text == "🔙 Отменить и в меню")
async def cancel_action(message: Message):
    user_id = message.from_user.id
    waiting_for_appeal.discard(user_id)
    adding_admin_process.discard(user_id)
    removing_admin_process.discard(user_id)
    
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

    # --- ДОБАВЛЕНИЕ АДМИНА ---
    if user_id in adding_admin_process:
        adding_admin_process.remove(user_id)
        if text.isdigit():
            new_admin_id = int(text)
            admins.add(new_admin_id)
            save_admins(admins)
            await message.answer(f"✅ Пользователь `{new_admin_id}` успешно добавлен в администраторы!", parse_mode="Markdown", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Ошибка. ID должен состоять только из цифр.", reply_markup=manage_admins_keyboard)
        return
    
    # --- УДАЛЕНИЕ АДМИНА ---
    if user_id in removing_admin_process:
        removing_admin_process.remove(user_id)
        if text.isdigit():
            rem_id = int(text)
            if rem_id == SUPER_ADMIN_ID:
                await message.answer("❌ Нельзя удалить главного суперадмина!", reply_markup=manage_admins_keyboard)
            elif rem_id in admins:
                admins.remove(rem_id)
                save_admins(admins)
                await message.answer(f"🗑️ Пользователь `{rem_id}` удален из администраторов.", parse_mode="Markdown", reply_markup=manage_admins_keyboard)
            else:
                await message.answer("⚠️ Такого ID нет в списке администраторов.", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Ошибка. ID должен состоять только из цифр.", reply_markup=manage_admins_keyboard)
        return

    # --- ОТПРАВКА АНОНИМНОГО ОБРАЩЕНИЯ АДМИНАМ ---
    if user_id in waiting_for_appeal:
        waiting_for_appeal.remove(user_id)
        appeal_text = f"🚨 **Новое анонимное обращение от сотрудника:**\n\n{text}"
        
        for admin_id in admins:
            try:
                await bot.send_message(admin_id, appeal_text, parse_mode="Markdown")
            except Exception:
                pass
                
        await message.answer("✅ Ваше обращение успешно отправлено администраторам!", reply_markup=employee_keyboard)
        return

    # --- ДЕЙСТВИЯ АДМИНА ---
    if user_id in admins:
        if text == "🔙 В обычное меню":
            await message.answer("Переключаюсь в меню сотрудника:", reply_markup=employee_keyboard)
            return
        elif text == "📊 Статистика бота":
            await message.answer("📊 **Статистика бота:**\n\n• Обращений за сегодня: 0\n• Активных пользователей: 1+", parse_mode="Markdown")
            return
        elif text == "📁 Актуальные обращения":
            await message.answer("📁 **Актуальные обращения:**\n\nНовые обращения приходят вам в личные сообщения в реальном времени.", parse_mode="Markdown")
            return
        elif text == "📈 Отчеты по дисциплине":
            await message.answer("📈 **Отчеты по дисциплине:**\n\n• Нарушений не зафиксировано.", parse_mode="Markdown")
            return

    # --- СПРАВОЧНИК СОТРУДНИКА ---
    text_lower = text.lower()
    if "отпуск" in text_lower:
        await send_response_with_thanks(message, VACATION_TEXT)
    elif "больнич" in text_lower:
        await send_response_with_thanks(message, SICK_LEAVE_TEXT)
    elif "график" in text_lower or "перерыв" in text_lower:
        await send_response_with_thanks(message, SCHEDULE_TEXT)
    elif "обязанности" in text_lower:
        await send_response_with_thanks(message, DUTIES_TEXT)
    elif "прогул" in text_lower or "дисциплин" in text_lower:
        await send_response_with_thanks(message, DISCIPLINE_TEXT)
    else:
        if user_id in admins:
            await message.answer("Админ-панель:", reply_markup=admin_keyboard)
        else:
            await message.answer(
                "Хм, я не совсем понял запрос 🤔 Воспользуйтесь кнопками меню или нажмите **«✍️ Написать анонимное обращение»**:",
                reply_markup=employee_keyboard
            )

async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
