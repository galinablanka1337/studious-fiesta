from datetime import datetime
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile

# --- CONFIGURATION ---
# Токен подтягивается из защищенных переменных окружения хостинга BotHost
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
    if not os.path.exists(ADMINS_FILE):
        return {SUPER_ADMIN_ID}
    with open(ADMINS_FILE, "r") as f:
        return {int(line.strip()) for line in f if line.strip().isdigit()}

def save_admins(admins):
    with open(ADMINS_FILE, "w") as f:
        for admin_id in admins:
            f.write(f"{admin_id}\n")

# --- STATES ---
adding_admin_process = set()
removing_admin_process = set()

# --- IMAGES ---
IMG_NIGHT = "image.png"     
IMG_EVENING = "image_2.png" 
IMG_MORNING = "image_3.png" 
IMG_DAY = "image_4.png"     
IMG_THANKS = "image_5.png"  

# --- KEYBOARDS ---
employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
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

# --- TEXTS ---
VACATION_TEXT = "🌴 **Информация по отпускам:**\n\n• Заявление подается за 2 недели до начала."
SICK_LEAVE_TEXT = "🏥 **Порядок по больничным листам:**\n\n• После закрытия больничного отправьте его номер в отдел кадров."
SCHEDULE_TEXT = "⏱️ **График и рабочее время:**\n\n• Используется приложение «Работа со Вкусом»."
DUTIES_TEXT = "📋 **Основные обязанности:**\n\n• Сбор заказов, контроль сроков годности."
DISCIPLINE_TEXT = "⚠️ **Дисциплина:**\n\n• Своевременно предоставляйте оправдательные документы."

# --- HANDLERS ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    hour = datetime.now().hour
    
    if 5 <= hour < 12: greeting = "Доброе утро! ☀️"
    elif 12 <= hour < 17: greeting = "Добрый день! 🍏"
    elif 17 <= hour < 23: greeting = "Добрый вечер! 🌆"
    else: greeting = "Доброй ночи! 🌙"

    if user_id in load_admins():
        await message.answer(f"{greeting} Приветствую, Администратор! 👑", reply_markup=admin_keyboard)
    else:
        try:
            await message.answer_photo(photo=FSInputFile(IMG_MORNING), caption=f"{greeting} 👋 Твой помощник на связи.", reply_markup=employee_keyboard)
        except:
            await message.answer(f"{greeting} 👋 Твой помощник на связи.", reply_markup=employee_keyboard)

async def send_response(message: Message, text: str):
    try:
        await message.answer_photo(photo=FSInputFile(IMG_THANKS), caption=text, parse_mode="Markdown")
    except:
        await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "👥 Управление админами")
async def admin_manage_menu(message: Message):
    if message.from_user.id in load_admins():
        await message.answer("👥 Меню управления администраторами:", reply_markup=manage_admins_keyboard)

@router.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if message.from_user.id in load_admins():
        admins = load_admins()
        await message.answer(f"📋 **Администраторы:**\n" + "\n".join([f"• `{aid}`" for aid in admins]), parse_mode="Markdown")

@router.message(F.text == "➕ Добавить админа")
async def ask_add_admin(message: Message):
    if message.from_user.id in load_admins():
        adding_admin_process.add(message.from_user.id)
        await message.answer("✍️ Отправьте ID нового администратора (только цифры):")

@router.message(F.text == "➖ Удалить админа")
async def ask_remove_admin(message: Message):
    if message.from_user.id in load_admins():
        removing_admin_process.add(message.from_user.id)
        await message.answer("✍️ Отправьте ID администратора для удаления:")

@router.message(F.text)
async def process_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    admins = load_admins()

    # --- ADMIN INPUT LOGIC ---
    if user_id in adding_admin_process:
        adding_admin_process.remove(user_id)
        if text.isdigit():
            admins.add(int(text))
            save_admins(admins)
            await message.answer(f"✅ ID {text} успешно добавлен в администраторы!", reply_markup=manage_admins_keyboard)
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
                await message.answer(f"🗑️ ID {rem_id} удален из администраторов.", reply_markup=manage_admins_keyboard)
            else:
                await message.answer("⚠️ Такого ID нет в списке админов.", reply_markup=manage_admins_keyboard)
        else:
            await message.answer("❌ Ошибка. ID должен состоять только из цифр.", reply_markup=manage_admins_keyboard)
        return

    # --- ADMIN ACTIONS ---
    if user_id in admins:
        if text == "🔙 Назад в админ-панель": await message.answer("Админ-панель:", reply_markup=admin_keyboard)
        elif text == "🔙 В обычное меню": await message.answer("Меню сотрудника:", reply_markup=employee_keyboard)
        elif text == "📊 Статистика бота": await message.answer("📊 Статистика: запросов за сегодня — 12")
        elif text == "📁 Актуальные обращения": await message.answer("📁 Активных обращений нет.")
        elif text == "📈 Отчеты по дисциплине": await message.answer("📈 Нарушений дисциплины не зафиксировано.")
        else: await search_logic(message)
    else:
        await search_logic(message)

async def search_logic(message: Message):
    text_lower = message.text.lower()
    if "отпуск" in text_lower: await send_response(message, VACATION_TEXT)
    elif "больнич" in text_lower: await send_response(message, SICK_LEAVE_TEXT)
    elif "график" in text_lower or "перерыв" in text_lower: await send_response(message, SCHEDULE_TEXT)
    elif "обязанности" in text_lower: await send_response(message, DUTIES_TEXT)
    elif "прогул" in text_lower or "дисциплин" in text_lower: await send_response(message, DISCIPLINES_TEXT if 'DISCIPLINES_TEXT' in globals() else DISCIPLINE_TEXT)
    else: await message.answer("Я вас не совсем понял, воспользуйтесь кнопками меню 👇", reply_markup=employee_keyboard)

async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
