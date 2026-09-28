import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    PollAnswerHandler,
    filters,
    ContextTypes,
)

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = -1004469487979
# -----------------

ASK_NAME, ASK_PURPOSE, ASK_SOURCE = range(3)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- АНКЕТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Привет! Давай познакомимся. Как тебя зовут?")
    return ASK_NAME

async def ask_purpose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Приятно познакомиться! А зачем ты хочешь вступить в нашу группу?")
    return ASK_PURPOSE

async def ask_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["purpose"] = update.message.text
    await update.message.reply_text("Понятно. А откуда ты узнал о нашей группе?")
    return ASK_SOURCE

async def finish_survey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["source"] = update.message.text
    user = update.effective_user

    survey_text = (
        f"📋 <b>Новая заявка на вступление</b>\n\n"
        f"<b>Имя:</b> {context.user_data['name']}\n"
        f"<b>Цель:</b> {context.user_data['purpose']}\n"
        f"<b>Источник:</b> {context.user_data['source']}\n\n"
        f"Пользователь: {user.mention_html()}"
    )

    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=survey_text,
            parse_mode="HTML"
        )

        message = await context.bot.send_poll(
            chat_id=GROUP_CHAT_ID,
            question=f"Принять {context.user_data['name']} в группу?",
            options=["✅ За", "❌ Против"],
            is_anonymous=False,
            allows_multiple_answers=False,
        )

        context.bot_data[message.poll.id] = {
            "chat_id": GROUP_CHAT_ID,
            "message_id": message.message_id,
            "votes_yes": 0,
            "votes_no": 0,
            "voters": {},
            "user_id": user.id,
            "user_name": context.user_data['name'],
        }

        await update.message.reply_text(
            "Спасибо! Твоя анкета отправлена на рассмотрение. Результат придёт в этот чат."
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке в группу: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуй позже.")

    context.user_data.clear()
    return ConversationHandler.END

# --- ГОЛОСОВАНИЕ ---
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    poll_id = answer.poll_id

    if poll_id not in context.bot_data:
        return

    poll_data = context.bot_data[poll_id]
    user_id = answer.user.id
    selected = answer.option_ids[0] if answer.option_ids else None

    # Убираем старый голос пользователя, если он менял
    old = poll_data["voters"].get(user_id)
    if old == 0:
        poll_data["votes_yes"] -= 1
    elif old == 1:
        poll_data["votes_no"] -= 1

    if selected == 0:
        poll_data["votes_yes"] += 1
        poll_data["voters"][user_id] = 0
    elif selected == 1:
        poll_data["votes_no"] += 1
        poll_data["voters"][user_id] = 1

    total = poll_data["votes_yes"] + poll_data["votes_no"]

    # Условие: 2/3 от проголосовавших "За" (и минимум 3 голоса)
    if total >= 3 and poll_data["votes_yes"] >= (2 * total / 3):
        try:
            invite_link = await context.bot.create_chat_invite_link(
                chat_id=GROUP_CHAT_ID,
                member_limit=1,
                name=f"invite_{poll_data['user_id']}"
            )

            await context.bot.send_message(
                chat_id=poll_data["user_id"],
                text=f"🎉 Поздравляем! Твоя заявка одобрена.\n\nВот твоя персональная ссылка для входа:\n{invite_link.invite_link}"
            )

            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"✅ Заявка от {poll_data['user_name']} одобрена!"
            )

            del context.bot_data[poll_id]

        except Exception as e:
            logger.error(f"Ошибка при одобрении: {e}")

    # Условие: большинство "Против" — отклоняем
    elif total >= 3 and poll_data["votes_no"] > poll_data["votes_yes"]:
        try:
            await context.bot.send_message(
                chat_id=poll_data["user_id"],
                text="😔 К сожалению, участники проголосовали против твоей заявки."
            )
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"❌ Заявка от {poll_data['user_name']} отклонена."
            )
            del context.bot_data[poll_id]
        except Exception as e:
            logger.error(f"Ошибка при отклонении: {e}")

# --- ЗАПУСК ---
def main() -> None:
    application = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_purpose)],
            ASK_PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_source)],
            ASK_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, finish_survey)],
        },
        fallbacks=[],
    )

    application.add_handler(conv_handler)
    application.add_handler(PollAnswerHandler(receive_poll_answer))

    application.run_polling()

if __name__ == "__main__":
    main()
