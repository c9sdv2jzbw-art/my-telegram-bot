import os
import html
import logging
from collections import deque
from datetime import datetime
from math import ceil
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    PollAnswerHandler,
    ChatJoinRequestHandler,
    TypeHandler,
    filters,
    ContextTypes,
)

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = -1004344602549
ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID", "357312670"))

RULES_TEXT = (
    "👋 <b>Привет!</b>\n\n"
    "Прежде чем подать заявку в чат для владельцев Mini в Новосибирске, пожалуйста, ознакомься с правилами чата:\n\n"
    "1. Уважай других участников — без оскорблений и агрессии. Бережем атмосферу чата ❤️\n"
    "2. Никакого спама, рекламы и сторонних ссылок без согласования.\n"
    "3. Не разглашай личную информацию участников.\n"
    "4. Придерживайся темы чата.\n"
    "5. Держи «руль» ровно! Если возникли разногласия с кем-то из участников, перенесите обсуждение в личные сообщения. Пусть общий чат остается территорией позитива и Mini.\n"
    "6. Уважай время других участников: соблюдай режим тишины с 22:00 до 10:00 по НСК, в пятницу и субботу с 24:00 по НСК.\n\n"
    "<b>Согласен(на) с правилами?</b> Напиши «Да» или «Нет»."
)

VOTE_NOTE = (
    "ℹ️ <i>Напоминаем: для одобрения заявки нужно 1/4 голосов «За» от числа участников группы. "
    "У каждого участника есть право вето — любой голос «Против» блокирует вступление.</i>"
)

OK_KEYWORDS = {"да", "yes", "ага", "ок", "ok", "согласен", "согласна"}
NO_KEYWORDS = {"нет", "no", "не"}
SKIP_KEYWORDS = {"продолжить", "дальше", "пропустить", "skip", "continue"}

ASK_AGREE, ASK_NAME, ASK_SOURCE, ASK_ABOUT, ASK_ABOUT_TEXT = range(5)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- ЛОГИРОВАНИЕ АКТИВНОСТИ ---
async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    if user.id == ADMIN_ID:
        return

    if update.message and update.message.text:
        text = update.message.text
        if text.startswith("/") and not text.startswith("/start"):
            return

    kind = "?"
    if update.message:
        if update.message.text:
            kind = "start" if update.message.text.startswith("/start") else "text"
        elif update.message.photo:
            kind = "photo"
        else:
            kind = "message"
    elif update.callback_query:
        kind = "button"
    elif update.poll_answer:
        kind = "vote"
    elif update.chat_join_request:
        kind = "join_request"

    logger.info(f"UPDATE | {user.full_name} (@{user.username}) | id={user.id} | {kind}")

    activity = context.bot_data.setdefault("activity_log", deque(maxlen=50))
    activity.append({
        "time": datetime.now(),
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username,
        "kind": kind,
    })


# --- АНКЕТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_id = user.id

    # Если пользователь ждал мотивацию — снимаем
    awaiting = context.bot_data.get("awaiting_motivation", set())
    awaiting.discard(user_id)

    # Очищаем возможный старый прогресс
    context.bot_data.get("progress", {}).pop(user_id, None)

    # Создаём свежий прогресс
    progress = context.bot_data.setdefault("progress", {}).setdefault(user_id, {})
    progress["started"] = datetime.now()
    progress["mention"] = user.mention_html()

    await update.message.reply_text(RULES_TEXT, parse_mode="HTML")
    return ASK_AGREE


async def wrong_type_text_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Пожалуйста, ответь текстом 🙂 На этом шаге фото не подойдёт."
    )


async def wrong_type_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Пожалуйста, отправь текст или фото с подписью 🙂 "
        "Другие типы сообщений на этом шаге не подойдут."
    )


async def wrong_type_about_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Пожалуйста, напиши текстом пару слов о себе (или напиши «продолжить») 🙂"
    )


async def agree(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    answer = update.message.text.strip().lower()
    progress = context.bot_data.get("progress", {}).get(user_id, {})

    if answer in OK_KEYWORDS:
        await update.message.reply_text("Отлично! Как тебя зовут?")
        return ASK_NAME
    elif answer in NO_KEYWORDS:
        context.bot_data.get("progress", {}).pop(user_id, None)
        await update.message.reply_text(
            "Жаль. Без согласия с правилами вступить нельзя. Если передумаешь — напиши /start."
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text("Пожалуйста, ответь «Да» или «Нет».")
        return ASK_AGREE


async def ask_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет имя, спрашивает про источник."""
    user_id = update.effective_user.id
    progress = context.bot_data.setdefault("progress", {}).setdefault(user_id, {})
    progress["name"] = update.message.text

    await update.message.reply_text(
        "Приятно познакомиться! А как ты узнал(а) о нашей группе?\n\n"
        "<i>(Можно ответить текстом или прикрепить фото — на выбор. "
        "Если отправляешь фото, можешь добавить подпись.)</i>",
        parse_mode="HTML",
    )
    return ASK_SOURCE


async def receive_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Источник — текст, фото или фото с подписью."""
    user_id = update.effective_user.id
    msg = update.message
    progress = context.bot_data.setdefault("progress", {}).setdefault(user_id, {})

    if msg.photo:
        progress["source"] = msg.caption or "(фото без подписи)"
        progress["source_photo"] = msg.photo[-1].file_id
    else:
        progress["source"] = msg.text

    await msg.reply_text(
        "Отлично! Теперь расскажи больше о своём мини и о себе — что ещё хочется "
        "добавить? Можешь приложить фото своего мини (по желанию).\n\n"
        "Если не хочешь отвечать, просто напиши «продолжить»."
    )
    return ASK_ABOUT


async def receive_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Последний вопрос: текст, фото, фото+подпись или «продолжить»."""
    user_id = update.effective_user.id
    msg = update.message
    progress = context.bot_data.setdefault("progress", {}).setdefault(user_id, {})

    text = (msg.text or "").strip().lower()

    # «Продолжить» без фото и текста — пропускаем весь вопрос
    if text in SKIP_KEYWORDS:
        progress["about"] = ""
        progress["photo"] = None
        progress["skipped_about"] = True
        return await finish_survey(update, context)

    if msg.photo:
        progress["photo"] = msg.photo[-1].file_id
        if msg.caption:
            # Фото с подписью = полный ответ
            progress["about"] = msg.caption
            return await finish_survey(update, context)
        # Фото без подписи — спрашиваем, будет ли текст
        await msg.reply_text(
            "📷 Фото получил!\n\n"
            "Хочешь добавить текст о себе и о мини? Напиши его следующим сообщением.\n\n"
            "Если хочешь оставить <b>только фото</b> — просто напиши «продолжить».",
            parse_mode="HTML",
        )
        return ASK_ABOUT_TEXT

    # Текст без фото
    progress["photo"] = None
    progress["about"] = msg.text
    return await finish_survey(update, context)


async def receive_about_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ждём текст после фото без подписи."""
    user_id = update.effective_user.id
    msg = update.message
    progress = context.bot_data.setdefault("progress", {}).setdefault(user_id, {})

    text = (msg.text or "").strip().lower()
    if text in SKIP_KEYWORDS:
        # Оставляем только фото, без текста
        progress["about"] = ""
        progress["skipped_about"] = True
    else:
        progress["about"] = msg.text
    return await finish_survey(update, context)


async def finish_survey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_id = user.id

    progress = context.bot_data.get("progress", {}).pop(user_id, {})

    apps = context.bot_data.setdefault("applications", {})
    apps[user_id] = {
        "name": progress.get("name", ""),
        "source": progress.get("source", ""),
        "source_photo": progress.get("source_photo"),
        "about": progress.get("about", ""),
        "photo_id": progress.get("photo"),
        "mention": progress.get("mention", user.mention_html()),
        "skipped_about": progress.get("skipped_about", False),
        "created": datetime.now(),
    }

    await post_application_and_poll(context, user_id, is_revote=False)

    await update.message.reply_text(
        "Спасибо! Твоя анкета отправлена на рассмотрение. Результат придёт в этот чат."
    )
    return ConversationHandler.END


async def post_application_and_poll(context: ContextTypes.DEFAULT_TYPE, user_id: int, is_revote: bool) -> None:
    app = context.bot_data["applications"][user_id]
    name = html.escape(app["name"])
    source = html.escape(app["source"])
    about = html.escape(app.get("about", ""))
    # Приоритет: фото из «о себе», иначе фото из «источника»
    photo_id = app.get("photo_id") or app.get("source_photo")

    app["created"] = datetime.now()

    header = (
        "📋 <b>Повторная заявка (после мотивации)</b>"
        if is_revote
        else "📋 <b>Новая заявка на вступление</b>"
    )

    note = ""
    if app.get("skipped_about"):
        note = "\n<i>⚠️ Кандидат предпочёл не отвечать на последний вопрос.</i>"

    about_display = about if about else "(не ответил)"

    survey_text = (
        f"{header}\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Узнал(а) о нас:</b> {source}\n"
        f"<b>О себе:</b> {about_display}\n"
        f"{note}\n\n"
        f"Пользователь: {app['mention']}\n\n"
        f"{VOTE_NOTE}"
    )

    if photo_id:
        await context.bot.send_photo(
            chat_id=GROUP_CHAT_ID,
            photo=photo_id,
            caption=survey_text,
            parse_mode="HTML",
        )
    else:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=survey_text,
            parse_mode="HTML",
        )

    poll_msg = await context.bot.send_poll(
        chat_id=GROUP_CHAT_ID,
        question=f"Принять {app['name']} в группу?",
        options=["✅ За", "❌ Против"],
        is_anonymous=False,
        allows_multiple_answers=False,
    )

    try:
        members = await context.bot.get_chat_member_count(GROUP_CHAT_ID)
    except Exception as e:
        logger.warning(f"Не удалось получить число участников: {e}")
        members = 4
    required_yes = max(1, ceil(members / 4))

    context.bot_data[poll_msg.poll.id] = {
        "chat_id": GROUP_CHAT_ID,
        "message_id": poll_msg.message_id,
        "votes_yes": 0,
        "votes_no": 0,
        "voters": {},
        "user_id": user_id,
        "user_name": app["name"],
        "decided": False,
        "required_yes": required_yes,
        "members": members,
    }


# --- ГОЛОСОВАНИЕ ---
async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    poll_id = answer.poll_id
    if poll_id not in context.bot_data:
        return

    poll_data = context.bot_data[poll_id]
    if not isinstance(poll_data, dict) or poll_data.get("decided"):
        return

    user_id = answer.user.id
    selected = answer.option_ids[0] if answer.option_ids else None

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

    if poll_data["votes_no"] >= 1:
        poll_data["decided"] = True
        await safe_stop_poll(context, poll_data)
        await reject_application(context, poll_data)
        return

    if poll_data["votes_yes"] >= poll_data["required_yes"]:
        poll_data["decided"] = True
        await safe_stop_poll(context, poll_data)
        await approve_application(context, poll_data)


async def safe_stop_poll(context: ContextTypes.DEFAULT_TYPE, poll_data: dict) -> None:
    try:
        await context.bot.stop_poll(
            chat_id=poll_data["chat_id"],
            message_id=poll_data["message_id"],
        )
    except Exception as e:
        logger.warning(f"Не удалось остановить опрос: {e}")


async def approve_application(context: ContextTypes.DEFAULT_TYPE, poll_data: dict) -> None:
    try:
        approved = context.bot_data.setdefault("approved_users", set())
        approved.add(poll_data["user_id"])

        invite_link = await context.bot.create_chat_invite_link(
            chat_id=GROUP_CHAT_ID,
            member_limit=1,
            name=f"invite_{poll_data['user_id']}",
        )
        await context.bot.send_message(
            chat_id=poll_data["user_id"],
            text=(
                "🎉 Поздравляем! Твоя заявка одобрена.\n\n"
                f"Вот твоя персональная ссылка для входа:\n{invite_link.invite_link}\n\n"
                "После перехода по ссылке бот автоматически примет тебя в группу."
            ),
        )
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=f"✅ Заявка от {poll_data['user_name']} одобрена!",
        )
    except Exception as e:
        logger.error(f"Ошибка при одобрении: {e}")


async def reject_application(context: ContextTypes.DEFAULT_TYPE, poll_data: dict) -> None:
    try:
        await context.bot.send_message(
            chat_id=poll_data["user_id"],
            text=(
                "😔 К сожалению, участники проголосовали против твоей заявки.\n\n"
                "Расскажи, пожалуйста, какая у тебя мотивация быть в группе "
                "и что ты планируешь делать? Мы передадим это участникам "
                "и запустим новое голосование."
            ),
        )
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=f"❌ Заявка от {poll_data['user_name']} отклонена.",
        )
        awaiting = context.bot_data.setdefault("awaiting_motivation", set())
        awaiting.add(poll_data["user_id"])
    except Exception as e:
        logger.error(f"Ошибка при отклонении: {e}")


# --- АВТООДОБРЕНИЕ ---
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    join_request = update.chat_join_request
    user_id = join_request.from_user.id
    approved = context.bot_data.get("approved_users", set())

    if user_id in approved:
        try:
            await join_request.approve()
            logger.info(f"✅ Заявка от {user_id} одобрена автоматически.")
            approved.discard(user_id)
        except Exception as e:
            logger.error(f"Не удалось одобрить заявку {user_id}: {e}")
    else:
        try:
            await join_request.decline()
            logger.info(f"❌ Заявка от {user_id} отклонена (нет в списке).")
        except Exception as e:
            logger.error(f"Не удалось отклонить заявку {user_id}: {e}")


# --- МОТИВАЦИЯ + FALLBACK (объединено) ---
async def motivation_or_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик, который ловит:
    1) текст от пользователя, который ждёт мотивацию;
    2) текст от пользователя, которого бот не знает → просим /start.
    """
    user = update.effective_user
    if not user:
        return
    user_id = user.id

    awaiting = context.bot_data.get("awaiting_motivation", set())

    # Случай 1: это мотивация
    if user_id in awaiting:
        awaiting.discard(user_id)
        motivation = html.escape(update.message.text)
        try:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"💬 <b>Мотивация от участника:</b>\n{motivation}",
                parse_mode="HTML",
            )
            if user_id in context.bot_data.get("applications", {}):
                await post_application_and_poll(context, user_id, is_revote=True)
                await update.message.reply_text(
                    "Спасибо! Твоя мотивация передана в группу, и мы запустили новое голосование. "
                    "Результат придёт сюда."
                )
            else:
                await update.message.reply_text("Спасибо! Мотивация передана в группу.")
        except Exception as e:
            logger.error(f"Ошибка при отправке мотивации: {e}")
        return

    # Случай 2: бот не знает, что с этим делать
    if update.effective_chat.type != "private":
        return
    if user_id in context.bot_data.get("progress", {}):
        return  # в активной анкете — не мешаем

    await update.message.reply_text(
        "⚠️ Извини, произошёл сбой, и я потерял контекст нашего разговора.\n\n"
        "Пожалуйста, напиши /start, чтобы начать заново."
    )


# --- КОМАНДА /status ---
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Команда доступна только администратору.")
        return

    activity = list(context.bot_data.get("activity_log", []))
    lines = []

    if activity:
        recent = list(reversed(activity))[:10]
        lines.append("🕐 <b>Последние события:</b>")
        lines.append("")
        for ev in recent:
            t = ev["time"].strftime("%d.%m %H:%M:%S")
            mention = f'<a href="tg://user?id={ev["user_id"]}">{html.escape(ev["name"])}</a>'
            lines.append(f"<code>{t}</code> — {mention} — <i>{ev['kind']}</i>")

        last = activity[-1]
        delta = datetime.now() - last["time"]
        secs = int(delta.total_seconds())
        if secs < 60:
            ago = f"{secs} сек. назад"
        elif secs < 3600:
            ago = f"{secs // 60} мин. назад"
        elif secs < 86400:
            ago = f"{secs // 3600} ч. назад"
        else:
            ago = f"{secs // 86400} дн. назад"
        lines.append("")
        lines.append(f"⏱ Последняя активность: <b>{ago}</b>")
    else:
        lines.append("🕐 С ботом ещё никто не общался.")

    apps = context.bot_data.get("applications", {})
    if apps:
        sorted_apps = sorted(
            apps.items(),
            key=lambda x: x[1].get("created", datetime.min),
            reverse=True,
        )[:3]

        lines.append("")
        lines.append(f"📋 <b>Последние {len(sorted_apps)} анкет(ы):</b>")
        for uid, app in sorted_apps:
            lines.append("")
            name_safe = html.escape(app.get("name", "?"))
            mention = f'<a href="tg://user?id={uid}">{name_safe}</a>'
            source_safe = html.escape(app.get("source", "(нет)"))
            about_raw = app.get("about", "") or "(не ответил)"
            about_safe = html.escape(about_raw[:250])

            lines.append(f"👤 <b>{mention}</b>")
            lines.append(f"   Узнал(а): {source_safe}")
            lines.append(f"   О себе: {about_safe}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # Фото отдельными сообщениями
    if apps:
        sorted_apps = sorted(
            apps.items(),
            key=lambda x: x[1].get("created", datetime.min),
            reverse=True,
        )[:3]
        for uid, app in sorted_apps:
            photo_id = app.get("photo_id") or app.get("source_photo")
            if photo_id:
                try:
                    await update.message.reply_photo(
                        photo_id,
                        caption=f"📷 {html.escape(app.get('name', '?'))}",
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить фото: {e}")


# --- ЗАПУСК ---
def main() -> None:
    application = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_AGREE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, agree),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, wrong_type_text_only),
            ],
            ASK_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_source),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, wrong_type_text_only),
            ],
            ASK_SOURCE: [
                MessageHandler(
                    filters.PHOTO | (filters.TEXT & ~filters.COMMAND),
                    receive_source,
                ),
                MessageHandler(
                    ~filters.PHOTO & ~filters.TEXT & ~filters.COMMAND,
                    wrong_type_about,
                ),
            ],
            ASK_ABOUT: [
                MessageHandler(
                    filters.PHOTO | (filters.TEXT & ~filters.COMMAND),
                    receive_about,
                ),
                MessageHandler(
                    ~filters.PHOTO & ~filters.TEXT & ~filters.COMMAND,
                    wrong_type_about,
                ),
            ],
            ASK_ABOUT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_about_text),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, wrong_type_about_text),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    # Логирование
    application.add_handler(TypeHandler(Update, log_update), group=-2)

    # ВСЁ В ОДНОЙ ГРУППЕ, ПО ПОРЯДКУ.
    # Срабатывает только первый подходящий.
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(conv_handler)
    application.add_handler(PollAnswerHandler(receive_poll_answer))
    application.add_handler(ChatJoinRequestHandler(handle_join_request))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, motivation_or_fallback)
    )

    print("Bot is running...")
    application.run_polling()


if __name__ == "__main__":
    main()
