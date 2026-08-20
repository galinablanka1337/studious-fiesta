"""
Telegram-бот для обращений сотрудников и админ-панели.
Собраны все наработки: раздельные инструкции, строгие права, 
подтверждения, поддержка фото, оценка состояния и ответы админа.
Использует библиотеку aiogram 3.x.
"""

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not TOKEN:
  raise ValueError("Не задана переменная окружения BOT_TOKEN!")
if not ADMIN_ID_RAW:
  raise ValueError("Не задана переменная окружения ADMIN_ID!")

ADMIN_ID = int(ADMIN_ID_RAW)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ И СОСТОЯНИЯ ---
admins_set = {ADMIN_ID}
tickets_db = (
    []
)  # Структура: {"id": int, "user_id": int, "text": str, "photo": str, "status": str, "answer": str}
states_db = {}  # Фиксация самочувствия


class TicketStates(StatesGroup):
  waiting_for_ticket_text = State()
  waiting_for_state_text = State()
  waiting_for_admin_answer = State()


# --- РАЗДЕЛЬНЫЕ ИНСТРУКЦИИ ---
INSTRUCTION_EMPLOYEE = (
    "📋 **ИНСТРУКЦИЯ СОТРУДНИКА**\n\n"
    "Этот бот создан для оперативной связи с администрацией и фиксации вашего самочувствия.\n\n"
    "• **✉️ Написать обращение** — отправьте новый вопрос или проблему (можно прикрепить фото/скриншот).\n"
    "• **📂 Мои обращения** — просмотр истории ваших личных заявок и ответов.\n"
    "• **❤️ Оценить состояние** — фиксация вашего текущего рабочего самочувствия.\n"
    "• **❓ Инструкция** — это руководство пользователя."
)

INSTRUCTION_ADMIN = (
    "🛠 **ИНСТРУКЦИЯ АДМИНИСТРАТОРА**\n\n"
    "Панель управления создана для сквозного контроля и помощи команде.\n\n"
    "• **📥 Обращения сотрудников** — список всех активных тикетов (включая файлы), возможность ответить сотруднику.\n"
    "• **📖 Инстр. сотрудника** — проверка интерфейса, который видит рядовой персонал.\n"
    "• **🛠 Инстр. админа** — текущее руководство.\n\n"
    "💡 **Управление доступом:** Вы можете назначать новых помощников с помощью команды `/add_admin [ID_пользователя]`."
)


# --- КЛАВИАТУРЫ ---
def get_user_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="✉️ Написать обращение", callback_data="new_ticket"
              )
          ],
          [
              InlineKeyboardButton(
                  text="📂 Мои обращения", callback_data="my_tickets"
              )
          ],
          [
              InlineKeyboardButton(
                  text="❤️ Оценить состояние", callback_data="rate_state"
              )
          ],
          [
              InlineKeyboardButton(
                  text="❓ Инструкция", callback_data="user_help"
              )
          ],
      ]
  )


def get_admin_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="📥 Обращения сотрудников",
                  callback_data="admin_tickets",
              )
          ],
          [
              InlineKeyboardButton(
                  text="📖 Инстр. сотрудника", callback_data="user_help"
              )
          ],
          [
              InlineKeyboardButton(
                  text="🛠 Инстр. админа", callback_data="admin_help"
              )
          ],
      ]
  )


def get_confirm_ticket_kb() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="✅ Подтвердить отправку", callback_data="confirm_ticket"
              ),
              InlineKeyboardButton(
                  text="❌ Отменить", callback_data="cancel_ticket"
              ),
          ]
      ]
  )


# --- СТАРТ И РАЗДЕЛЕНИЕ РОЛЕЙ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
  await state.clear()
  user_id = message.from_user.id
  logging.info(f"Пользователь {user_id} (@{message.from_user.username}) зашел.")

  if user_id in admins_set:
    await message.answer(
        "🛠 **Панель управления администратора**\nВыберите нужный раздел:",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown",
    )
  else:
    await message.answer(
        "👋 Здравствуйте!\nЭтот бот поможет вам связаться с администрацией. Выберите"
        " действие:",
        reply_markup=get_user_keyboard(),
    )


# --- СЦЕНАРИЙ СОТРУДНИКА: НАПИСАТЬ ОБРАЩЕНИЕ (С ФОТО И ПОДТВЕРЖДЕНИЕМ) ---
@dp.callback_query(F.data == "new_ticket")
async def process_new_ticket(callback: types.CallbackQuery, state: FSMContext):
  await callback.message.answer(
      "✉️ Отправьте текст вашего обращения или прикрепите скриншот (фото) с описанием:"
  )
  await state.set_state(TicketStates.waiting_for_ticket_text)
  await callback.answer()


@dp.message(TicketStates.waiting_for_ticket_text, F.photo | F.text)
async def receive_ticket_content(message: types.Message, state: FSMContext):
  photo_id = message.photo[-1].file_id if message.photo else None
  text = message.caption if message.photo else message.text

  if not text:
    text = "[Без текстового описания, только изображение]"

  await state.update_data(ticket_text=text, ticket_photo=photo_id)

  if photo_id:
    await message.answer_photo(
        photo=photo_id,
        caption=(
            f"📝 **Ваше обращение с фото:**\n\n{text}\n\nВсё верно? Нажмите"
            " кнопку подтверждения:"
        ),
        reply_markup=get_confirm_ticket_kb(),
        parse_mode="Markdown",
    )
  else:
    await message.answer(
        f"📝 **Ваше обращение:**\n\n{text}\n\nВсё верно? Нажмите кнопку"
        " подтверждения:",
        reply_markup=get_confirm_ticket_kb(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "confirm_ticket")
async def confirm_ticket_sending(callback: types.CallbackQuery, state: FSMContext):
  data = await state.get_data()
  text = data.get("ticket_text")
  photo_id = data.get("ticket_photo")

  ticket_id = len(tickets_db) + 1
  new_ticket = {
      "id": ticket_id,
      "user_id": callback.from_user.id,
      "text": text,
      "photo": photo_id,
      "status": "active",
      "answer": "Ожидает ответа...",
  }
  tickets_db.append(new_ticket)
  await state.clear()

  await callback.message.answer(
      "✅ **Обращение успешно отправлено администрации!**\nВы можете отслеживать"
      " его статус в разделе «📂 Мои обращения».",
      parse_mode="Markdown",
  )
  await callback.answer()


@dp.callback_query(F.data == "cancel_ticket")
async def cancel_ticket_sending(
    callback: types.CallbackQuery, state: FSMContext
):
  await state.clear()
  await callback.message.answer("❌ Отправка обращения отменена.")
  await callback.answer()


# --- КНОПКА «МОИ ОБРАЩЕНИЯ» (ФИЛЬТРАЦИЯ ПО ID) ---
@dp.callback_query(F.data == "my_tickets")
async def process_my_tickets(callback: types.CallbackQuery):
  user_id = callback.from_user.id
  user_tickets = [t for t in tickets_db if t["user_id"] == user_id]

  if not user_tickets:
    await callback.answer(
        "У вас пока нет отправленных обращений.", show_alert=True
    )
    return

  for t in user_tickets:
    status_emoji = (
        "🟢 Решено" if t["status"] == "resolved" else "🟡 В работе"
    )
    msg = (
        f"🆔 **Тикет #{t['id']}** | Статус: {status_emoji}\n"
        f"Вопрос: {t['text']}\n"
        f"Ответ администратора: {t['answer']}"
    )
    if t.get("photo"):
      await callback.message.answer_photo(
          photo=t["photo"], caption=msg, parse_mode="Markdown"
      )
    else:
      await callback.message.answer(msg, parse_mode="Markdown")
  await callback.answer()


# --- СЦЕНАРИЙ: ОЦЕНИТЬ СОСТОЯНИЕ ---
@dp.callback_query(F.data == "rate_state")
async def process_rate_state(callback: types.CallbackQuery, state: FSMContext):
  await callback.message.answer(
      "❤️ Как вы оцениваете свое текущее рабочее состояние? Напишите текстом"
      " или поставьте оценку от 1 до 5:"
  )
  await state.set_state(TicketStates.waiting_for_state_text)
  await callback.answer()


@dp.message(TicketStates.waiting_for_state_text)
async def receive_state_text(message: types.Message, state: FSMContext):
  user_id = message.from_user.id
  states_db[user_id] = message.text
  await state.clear()
  await message.answer(
      "✅ Спасибо! Ваше состояние зафиксировано и передано руководству."
  )


# --- ИНСТРУКЦИИ ---
@dp.callback_query(F.data == "user_help")
async def process_user_help(callback: types.CallbackQuery):
  await callback.message.answer(INSTRUCTION_EMPLOYEE, parse_mode="Markdown")
  await callback.answer()


@dp.callback_query(F.data == "admin_help")
async def process_admin_help(callback: types.CallbackQuery):
  if callback.from_user.id in admins_set:
    await callback.message.answer(INSTRUCTION_ADMIN, parse_mode="Markdown")
  else:
    await callback.answer("⛔️ Доступ запрещен!", show_alert=True)
  await callback.answer()


# --- АДМИН-ПАНЕЛЬ: ПРОСМОТР И ОТВЕТЫ С КНОПКАМИ ---
@dp.callback_query(F.data == "admin_tickets")
async def process_admin_tickets(callback: types.CallbackQuery):
  if callback.from_user.id not in admins_set:
    await callback.answer("⛔️ Доступ запрещен", show_alert=True)
    return

  active_tickets = [t for t in tickets_db if t["status"] == "active"]
  if not active_tickets:
    await callback.message.answer(
        "📥 Активных обращений от сотрудников нет.", parse_mode="Markdown"
    )
    return

  for t in active_tickets:
    answer_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✍️ Ответить на тикет", callback_data=f"answer_{t['id']}"
                )
            ]
        ]
    )
    caption = (
        f"📥 **Тикет #{t['id']}** от ID: `{t['user_id']}`\nТекст: {t['text']}"
    )

    if t.get("photo"):
      await callback.message.answer_photo(
          photo=t["photo"],
          caption=caption,
          reply_markup=answer_kb,
          parse_mode="Markdown",
      )
    else:
      await callback.message.answer(
          text=caption, reply_markup=answer_kb, parse_mode="Markdown"
      )
  await callback.answer()


@dp.callback_query(F.data.startswith("answer_"))
async def admin_start_answer(callback: types.CallbackQuery, state: FSMContext):
  if callback.from_user.id not in admins_set:
    return

  ticket_id = int(callback.data.split("_")[1])
  await state.update_data(answering_ticket_id=ticket_id)

  await callback.message.answer(
      f"✍️ Введите текст ответа для тикета #{ticket_id}:"
  )
  await state.set_state(TicketStates.waiting_for_admin_answer)
  await callback.answer()


@dp.message(TicketStates.waiting_for_admin_answer)
async def admin_send_answer(message: types.Message, state: FSMContext):
  data = await state.get_data()
  ticket_id = data.get("answering_ticket_id")
  admin_text = message.text

  target_ticket = None
  for t in tickets_db:
    if t["id"] == ticket_id:
      target_ticket = t
      t["answer"] = admin_text
      t["status"] = "resolved"
      break

  await state.clear()

  if target_ticket:
    await message.answer(
        f"✅ Ответ по тикету #{ticket_id} успешно сохранен и отправлен"
        " сотруднику!"
    )
    try:
      await bot.send_message(
          target_ticket["user_id"],
          f"🔔 **Получен ответ по вашему обращению #{ticket_id}:**\n\n{admin_text}",
          parse_mode="Markdown",
      )
    except Exception:
      await message.answer(
          "⚠️ Не удалось отправить уведомление сотруднику (возможно, бот заблокирован)."
      )
  else:
    await message.answer("❌ Ошибка: тикет не найден.")


# --- УПРАВЛЕНИЕ АДМИНАМИ ---
@dp.message(Command("add_admin"))
async def cmd_add_admin(message: types.Message):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ Эта команда доступна только главному владельцу.")
    return

  args = message.text.split()
  if len(args) < 2:
    await message.answer(
        "⚠️ Использование: `/add_admin <ID_пользователя>`", parse_mode="Markdown"
    )
    return

  try:
    new_admin_id = int(args[1])
    admins_set.add(new_admin_id)
    await message.answer(
        f"✅ Пользователь с ID `{new_admin_id}` успешно назначен администратором.",
        parse_mode="Markdown",
    )
    logging.info(f"Главный админ добавил нового администратора: {new_admin_id}")
  except ValueError:
    await message.answer("❌ Ошибка: ID должен состоять только из цифр.")


# --- ЗАПУСК БОТА ---
async def main():
  logging.info("Бот запущен и ожидает сообщения...")
  await dp.start_polling(bot)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except (KeyboardInterrupt, SystemExit):
    logging.info("Бот остановлен.")
