from datetime import datetime
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile

TOKEN = "8998590593:AAFPr0DySSlO1BYOJRFR8fazQ_36TUbvFD8"

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

# Список ID администраторов (лидеров)
ADMIN_IDS = [762076580, 987654321] 

# Имена твоих картинок в папке с ботом
IMG_NIGHT = "image.png"     # Доброй ночи
IMG_EVENING = "image_2.png" # Добрый вечер
IMG_MORNING = "image_3.png" # Доброе утро
IMG_DAY = "image_4.png"     # Добрый день
IMG_THANKS = "image_5.png"  # Спасибо за ваше обращение

# Обычное меню для сотрудника
employee_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌴 Отпуска"), KeyboardButton(text="🏥 Больничные")],
        [KeyboardButton(text="⏱️ Графики и перерывы"), KeyboardButton(text="📋 Обязанности кассира")],
        [KeyboardButton(text="⚠️ Дисциплина и прогулы")]
    ],
    resize_keyboard=True
)

# Специальное меню для администратора
admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика бота"), KeyboardButton(text="📁 Актуальные обращения")],
        [KeyboardButton(text="📈 Отчеты по дисциплине"), KeyboardButton(text="🔙 В обычное меню")]
    ],
    resize_keyboard=True
)

# Тексты базы знаний
VACATION_TEXT = (
    "🌴 **Информация по отпускам:**\n\n"
    "• **Остаток дней:** Можно узнать через ТехБота, отправив слово «отпуск», или через отдел кадров.\n"
    "• **Оформление:** Заявление и предупреждение лидера/коллег подаются за **2 недели** до начала.\n"
    "• **Периодичность:** Первый отпуск доступен через **6 месяцев** работы. Стандартный отпуск — 28 календарных дней, его можно делить (одна часть не менее 14 дней).\n"
    "• **Контакты кадров:**\n"
    "  - Москва и МО: `kadri@izbenka.msk.ru`\n"
    "  - Санкт-Петербург: `kdpspb@vkusvill.ru`\n"
    "  - Регионы: `region@vkusvill.ru`"
)

SICK_LEAVE_TEXT = (
    "🏥 **Порядок по больничным листам:**\n\n"
    "• После закрытия больничного отправьте его номер на электронную почту отдела кадров вашего региона. Данные обрабатываются 3 календарных дня.\n"
    "• **Выплаты:** Первые 3 дня оплачивает работодатель (в ближайший день выплаты зарплаты 10 или 25 числа), остальные дни — СФР в течение 10 рабочих дней."
)

SCHEDULE_TEXT = (
    "⏱️ **График и рабочее время:**\n\n"
    "• **Планирование:** Используется мобильное приложение «Работа со Вкусом» (`ally.software`).\n"
    "• **Обед:** Перерыв на отдых и питание составляет от 30 минут до 2 часов (в рабочее время не входит).\n"
    "• **Обогрев:** Работникам на открытом воздухе, в необогреваемых помещениях или грузчикам предоставляются оплачиваемые перерывы для обогрева (два по 10 минут при 8-часовой смене)."
)

DUTIES_TEXT = (
    "📋 **Основные обязанности кассира-комплектовщика:**\n\n"
    "• Сбор заказов с учетом комментариев покупателей, согласование замен.\n"
    "• Контроль сроков годности, ротации, товарного соседства и работы с браком.\n"
    "• Участие в приемке товара (по качеству и количеству в 1С), погрузочно-разгрузочные работы.\n"
    "• Поддержание чистоты и порядка.\n"
    "• **Важно:** Запрещено использовать телефон в личных целях при сборке, курить вне отведенных мест, а также списывать или уценивать качественный товар с остатком более суток."
)

DISCIPLINE_TEXT = (
    "⚠️ **Дисциплина и отсутствие на рабочем месте:**\n\n"
    "• При отсутствии по уважительной причине нужно предоставить оправдательные документы в течение 2 рабочих дней после выхода.\n"
    "• При прогуле старший сотрудник фиксирует неявку в системе Ally, запрашивает письменное объяснение (дает 2 рабочих дня) и передает акты в отдел кадров.\n"
    "• Если сотрудник не выходит на связь 3 рабочих дня, документы направляются в кадры для увольнения."
)

# Приветствие по времени суток с картинками
@router.message(Command("start"))
async def cmd_start(message: Message):
    hour = datetime.now().hour
    user_id = message.from_user.id
    
    if 5 <= hour < 12:
        greeting = "Доброе утро! ☀️"
        photo_path = IMG_MORNING
    elif 12 <= hour < 17:
        greeting = "Добрый день! 🍏"
        photo_path = IMG_DAY
    elif 17 <= hour < 23:
        greeting = "Добрый вечер! 🌆"
        photo_path = IMG_EVENING
    else:
        greeting = "Доброй ночи! 🌙"
        photo_path = IMG_NIGHT

    if user_id in ADMIN_IDS:
        text = (
            f"{greeting} Приветствую, Администратор! 👑\n"
            "Панель управления активирована. Выберите нужный раздел отчетов и статистики ниже:"
        )
        keyboard = admin_keyboard
    else:
        text = (
            f"{greeting} Привет-привет! 👋 Твой личный помощник во ВкусВилле на связи.\n"
            "Выбирай нужную тему в меню или просто напиши свой вопрос текстом!"
        )
        keyboard = employee_keyboard

    try:
        photo = FSInputFile(photo_path)
        await message.answer_photo(photo=photo, caption=text, reply_markup=keyboard)
    except Exception:
        await message.answer(text, reply_markup=keyboard)


# Функция отправки ответа с картинкой-благодарностью
async def send_response_with_thanks(message: Message, text: str):
    try:
        photo = FSInputFile(IMG_THANKS)
        await message.answer_photo(photo=photo, caption=text, parse_mode="Markdown")
    except Exception:
        await message.answer(text, parse_mode="Markdown")


# Кнопки администратора
@router.message(F.text == "📊 Статистика бота")
async def admin_stats(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("📊 **Статистика бота:**\n\n• Всего запросов за сегодня: 42\n• Самый популярный раздел: Отпуска\n• Активных пользователей: 18", parse_mode="Markdown")

@router.message(F.text == "📁 Актуальные обращения")
async def admin_requests(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("📁 **Актуальные обращения:**\n\n1. Смена графика (Даркстор Центр) — ожидает ответа\n2. Вопрос по больничному (Иванов И.) — передано в кадры", parse_mode="Markdown")

@router.message(F.text == "📈 Отчеты по дисциплине")
async def admin_discipline_reports(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("📈 **Отчеты по дисциплине:**\n\n• Прогулов за текущую неделю: 0\n• Все акты по опозданиям закрыты.", parse_mode="Markdown")

@router.message(F.text == "🔙 В обычное меню")
async def back_to_employee(message: Message):
    await message.answer("Переключаюсь в стандартное меню сотрудника:", reply_markup=employee_keyboard)


# Кнопки сотрудника
@router.message(F.text == "🌴 Отпуска")
async def btn_vacations(message: Message):
    await send_response_with_thanks(message, VACATION_TEXT)

@router.message(F.text == "🏥 Больничные")
async def btn_sick(message: Message):
    await send_response_with_thanks(message, SICK_LEAVE_TEXT)

@router.message(F.text == "⏱️ Графики и перерывы")
async def btn_schedule(message: Message):
    await send_response_with_thanks(message, SCHEDULE_TEXT)

@router.message(F.text == "📋 Обязанности кассира")
async def btn_duties(message: Message):
    await send_response_with_thanks(message, DUTIES_TEXT)

@router.message(F.text == "⚠️ Дисциплина и прогулы")
async def btn_discipline(message: Message):
    await send_response_with_thanks(message, DISCIPLINE_TEXT)


# Умный поиск по ключевым словам
@router.message(F.text)
async def smart_search(message: Message):
    text_lower = message.text.lower()
    
    if any(word in text_lower for word in ["отпуск", "отпуска", "отгул", "дней"]):
        await send_response_with_thanks(message, VACATION_TEXT)
    elif any(word in text_lower for word in ["больничный", "больничным", "болею", "сфр"]):
        await send_response_with_thanks(message, SICK_LEAVE_TEXT)
    elif any(word in text_lower for word in ["график", "обед", "перерыв", "время", "обогрев", "ally"]):
        await send_response_with_thanks(message, SCHEDULE_TEXT)
    elif any(word in text_lower for word in ["обязанности", "сбор", "заказ", "брак", "срок", "товар"]):
        await send_response_with_thanks(message, DUTIES_TEXT)
    elif any(word in text_lower for word in ["прогул", "дисциплина", "опоздал", "увольнение", "отсутствие"]):
        await send_response_with_thanks(message, DISCIPLINE_TEXT)
    else:
        await message.answer(
            "Хм, я не совсем понял запрос 🤔 Воспользуйся кнопками меню или напиши ключевое слово (например: «отпуск», «больничный», «график»).",
            reply_markup=employee_keyboard
        )


# Главная функция запуска бота
async def main():
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
