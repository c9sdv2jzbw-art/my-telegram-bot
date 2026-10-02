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
    ApplicationHandlerStop,
)

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "-1004344602549"))
ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID", "357312670"))

T_REMIND_1 = 30 * 60
T_REMIND_2 = 2 * 60 * 60
T_REMIND_3 = 14 * 60 * 60
T_AUTOPUB  = 15 * 60 * 60

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


# --- ПРОГРЕСС ---
def get_progress(context, user_id):
    return context.bot_data.setdefault("progress", {}).setdefault(user_id, {})


def pop_progress(context, user_id):
    return context.bot_data.get("progress", {}).pop(user_id, None)


# --- НАПОМИНАНИЯ ---
def _reminder_names(user_id):
    return [f"r1_{user_id}", f"r2_{user_id}", f"r3_{user_id}", f"autopub_{user_id}"]


def cancel_all_reminders(context, user_id):
    for name in _reminder_names(user_id):
        for j in context.job_queue.get_jobs_by_name(name):
            j.schedule_removal()


def schedule_reminders(context, user_id):
    cancel_all_reminders(context, user_id)
    context.job_queue.run_once(
        send_reminder_1, when=T_REMIND_1, data={"user_id": user_id}, name=f"r1_{user_id}"
    )
    context.job_queue.run_once(
        send_reminder_2, when=T_REMIND_2, data={"user_id": user_id}, name=f"r2_{user_id}"
    )
    context.job_queue.run_once(
        send_reminder_3, when=T_REMIND_3, data={"user_id": user_id}, name=f"r3_{user_id}"
    )
    context.job_queue.run_once(
        auto_publish_application, when=T_AUTOPUB, data={"user_id": user_id}, name=f"autopub_{user_id}"
    )


async def _safe_send(context, user_id, text):
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
    except Exception as e:
        logger.warning(f"Не удалось отправить сообщение {user_id}: {e}")


async def send_reminder_1(context):
    user_id = context.job.data["user_id"]
    await _safe_send(
        context, user_id,
        "👋 Привет! Ты начал(а) заполнять анкету, но пока не завершил(а). "
        "Продолжим? Просто ответь на текущий вопрос — я жду 🙂"
    )


async def send_reminder_2(context):
    user_id = context.job.data["user_id"]
    await _safe_send(
        context, user_id,
        "⏰ Напоминаем о себе! Мы всё ещё ждём продолжения твоей анкеты. "
        "Ответь, пожалуйста, на текущий вопрос, чтобы двинуться дальше."
    )


async def send_reminder_3(context):
    user_id = context.job.data["user_id"]
    await _safe_send(
        context, user_id,
        "🙏 Мы очень тебя ждём! Заверши анкету — осталось совсем немного. "
        "Просто ответь на текущий вопрос, и всё получится."
    )


async def auto_publish_application(context):
    user_id = context.job.data["user_id"]
    progress = context.bot_data.get("progress", {}).get(user_id)
    if not progress:
        return

    if not (progress.get("name") and progress.get("source")):
        return

    cancel_all_reminders(context, user_id)

    apps = context.bot_data.setdefault("applications", {})
    apps[user_id] = {
        "name": progress.get("name", ""),
        "source": progress.get("source", ""),
        "source_photo": progress.get("source_photo"),
        "about": progress.get("about", ""),
        "photo_id": progress.get("photo"),
        "mention": progress.get("mention", ""),
        "skipped_about": False,
        "incomplete": True,
        "created": datetime.now(),
    }
    pop_progress(context, user_id)

    try:
        await post_application_and_poll(context, user_id, is_revote=False)
        await _safe_send(
            context, user_id,
            "⏰ Мы так и не дождались ответа на последний вопрос, но всё равно "
            "отправили твою анкету на рассмотрение — с пометкой, что часть анкеты "
            "осталась незаполненной.\n\nРезультат придёт в этот чат."
        )
    except Exception as e:
        logger.error(f"Auto-publish error: {e}")


# --- ЛОГИРОВАНИЕ ---
async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id == ADMIN_ID:
        return

    if update.message and update.message.text:
        text = update.message.text
        if text.startswith("/") and not text.startswith("/start"):
            return

    kind = "?"
    text_content = ""
    photo_id = None

    if update.message:
        if update.message.text:
            kind = "start" if update.message.text.startswith("/start") else "text"
            text_content = update.message.text[:300]
        elif update.message.photo:
            kind = "photo"
            photo_id = update.message.photo[-1].file_id
            text_content = (update.message.caption or "")[:300]
        else:
            kind = "message"
    elif update.callback_query:
        kind = "button"
        text_content = update.callback_query.data or ""
    elif update.poll_answer:
        kind = "vote"
        # Кто и как проголосовал
        opts = update.poll_answer.option_ids
        text_content = f"vote: {opts}"
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
        "text": text_content,
        "photo_id": photo_id,
    })


# --- МОТИВАЦИЯ + FALLBACK (высокий приоритет) ---
async def motivation_or_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    user_id = user.id

    awaiting = context.bot_data.get("awaiting_motivation", set())

    # --- 1. Мотивация ---
    if user_id in awaiting:
        awaiting.discard(user_id)
        motivation = html.escape(update.message.text)

        # Помечаем, что мотивация уже была использована — второй раз не даём
        motivation_used = context.bot_data.setdefault("motivation_used", set())
        motivation_used.add(user_id)

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
                    "Результат придёт сюда.\n\n"
                    "<i>Это последняя попытка — если участники снова отклонят, заявка будет закрыта.</i>",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text("Спасибо! Мотивация передана в группу.")
        except Exception as e:
            logger.error(f"Ошибка при отправке мотивации: {e}")
        raise ApplicationHandlerStop

    # --- 2. Пользователь в анкете — не мешаем ---
    if user_id in context.bot_data.get("progress", {}):
        return

    # --- 3. Fallback ---
    if update.effective_chat.type != "private":
        return
    if not (update.message and update.message.text):
        return

    await update.message.reply_text(
        "⚠️ Извини, произошёл сбой, и я потерял контекст нашего разговора.\n\n"
        "Пожалуйста, напиши /start, чтобы начать заново."
    )
    raise ApplicationHandlerStop


# --- АНКЕТА ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_id = user.id

    awaiting = context.bot_data.get("awaiting_motivation", set())
    awaiting.discard(user_id)

    # Пользователь начал заново — сбрасываем флаг "мотивация использована"
    motivation_used = context.bot_data.get("motivation_used", set())
    motivation_used.discard(user_id)

    pop_progress(context, user_id)

    progress = get_progress(context, user_id)
    progress["started"] = datetime.now()
    progress["mention"] = user.mention_html()

    await update.message.reply_text(RULES_TEXT, parse_mode="HTML")
    schedule_reminders(context, user_id)
    return ASK_AGREE


async def wrong_type_text_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    schedule_reminders(context, user_id)
    await update.message.reply_text(
        "Пожалуйста, ответь текстом 🙂 На этом шаге фото не подойдёт."
    )


async def wrong_type_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    schedule_reminders(context, user_id)
    await update.message.reply_text(
        "Пожалуйста, отправь текст или фото с подписью 🙂 "
        "Другие типы сообщений на этом шаге не подойдут."
    )


async def wrong_type_about_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    schedule_reminders(context, user_id)
    await update.message.reply_text(
        "Пожалуйста, напиши текстом пару слов о себе (или напиши «продолжить») 🙂"
    )


async def agree(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    answer = update.message.text.strip().lower()

    if answer in OK_KEYWORDS:
        schedule_reminders(context, user_id)
        await update.message.reply_text("Отлично! Как тебя зовут?")
        return ASK_NAME
    elif answer in NO_KEYWORDS:
        cancel_all_reminders(context, user_id)
        pop_progress(context, user_id)
        await update.message.reply_text(
            "Жаль. Без согласия с правилами вступить нельзя. Если передумаешь — напиши /start."
        )
        return ConversationHandler.END
    else:
        schedule_reminders(context, user_id)
        await update.message.reply_text("Пожалуйста, ответь «Да» или «Нет».")
        return ASK_AGREE


async def ask_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    progress = get_progress(context, user_id)
    progress["name"] = update.message.text

    schedule_reminders(context, user_id)
    await update.message.reply_text(
        "Приятно познакомиться! А как ты узнал(а) о нашей группе?\n\n"
        "<i>(Можно ответить текстом или прикрепить фото — на выбор. "
        "Если отправляешь фото, можешь добавить подпись.)</i>",
        parse_mode="HTML",
    )
    return ASK_SOURCE


async def receive_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    msg = update.message
    progress = get_progress(context, user_id)

    if msg.photo:
        progress["source"] = msg.caption or "(фото без подписи)"
        progress["source_photo"] = msg.photo[-1].file_id
    else:
        progress["source"] = msg.text

    schedule_reminders(context, user_id)
    await msg.reply_text(
        "Отлично! Теперь расскажи о своём мини/о себе (по желанию)?\n"
        "Можешь просто отправить фото своего мини.\n\n"
        "Если не хочешь отвечать, просто напиши «продолжить»."
    )
    return ASK_ABOUT


async def receive_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    msg = update.message
    progress = get_progress(context, user_id)

    text = (msg.text or "").strip().lower()

    if text in SKIP_KEYWORDS:
        progress["about"] = ""
        progress["photo"] = None
        progress["skipped_about"] = True
        return await finish_survey(update, context)

    if msg.photo:
        progress["photo"] = msg.photo[-1].file_id
        if msg.caption:
            progress["about"] = msg.caption
            return await finish_survey(update, context)
        schedule_reminders(context, user_id)
        await msg.reply_text(
            "📷 Фото получил!\n\n"
            "Хочешь добавить текст о себе и о мини? Напиши его следующим сообщением.\n\n"
            "Если хочешь оставить <b>только фото</b> — просто напиши «продолжить».",
            parse_mode="HTML",
        )
        return ASK_ABOUT_TEXT

    progress["photo"] = None
    progress["about"] = msg.text
    return await finish_survey(update, context)


async def receive_about_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    msg = update.message
    progress = get_progress(context, user_id)

    text = (msg.text or "").strip().lower()
    if text in SKIP_KEYWORDS:
        progress["about"] = ""
        progress["skipped_about"] = True
    else:
        progress["about"] = msg.text
    return await finish_survey(update, context)


async def finish_survey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_id = user.id

    cancel_all_reminders(context, user_id)
    progress = pop_progress(context, user_id) or {}

    apps = context.bot_data.setdefault("applications", {})
    apps[user_id] = {
        "name": progress.get("name", ""),
        "source": progress.get("source", ""),
        "source_photo": progress.get("source_photo"),
        "about": progress.get("about", ""),
        "photo_id": progress.get("photo"),
        "mention": progress.get("mention", user.mention_html()),
        "skipped_about": progress.get("skipped_about", False),
        "incomplete": False,
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
    elif app.get("incomplete"):
        note = "\n<i>⚠️ Кандидат не завершил анкету — не ответил на последний вопрос.</i>"

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
    user_id = poll_data["user_id"]
    motivation_used = context.bot_data.get("motivation_used", set())

    try:
        # Если мотивация уже была использована — финальный отказ без нового цикла
        if user_id in motivation_used:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "😔 К сожалению, участники снова проголосовали против.\n\n"
                    "На этом процесс рассмотрения завершён. Спасибо за интерес к группе!"
                ),
            )
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"❌ Заявка от {poll_data['user_name']} окончательно отклонена.",
            )
            motivation_used.discard(user_id)
            return

        # Первый отказ — просим мотивацию
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "😔 К сожалению, участники проголосовали против твоей заявки.\n\n"
                "Расскажи, пожалуйста, какая у тебя мотивация быть в группе "
                "и что ты планируешь делать? Мы передадим это участникам "
                "и запустим новое голосование.\n\n"
                "<i>Это последняя попытка — если снова отклонят, заявка будет закрыта.</i>"
            ),
            parse_mode="HTML",
        )
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=f"❌ Заявка от {poll_data['user_name']} отклонена.",
        )
        awaiting = context.bot_data.setdefault("awaiting_motivation", set())
        awaiting.add(user_id)
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


# --- КОМАНДА /status ---
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Команда доступна только администратору.")
        return

    activity = list(context.bot_data.get("activity_log", []))

    if not activity:
        await update.message.reply_text(
            "🕐 С ботом ещё никто не общался (с момента последнего запуска)."
        )
        return

    recent = list(reversed(activity))[:10]

    lines = ["🕐 <b>Последние 10 сообщений:</b>", ""]
    for ev in recent:
        t = ev["time"].strftime("%d.%m %H:%M:%S")
        mention = f'<a href="tg://user?id={ev["user_id"]}">{html.escape(ev["name"])}</a>'
        kind = ev.get("kind", "?")

        row = f"<code>{t}</code> — {mention} — <i>{kind}</i>"
        text_content = ev.get("text") or ""
        if text_content:
            row += f"\n   ↳ {html.escape(text_content)}"
        lines.append(row)

    # Информация о последней активности
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

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # Отправляем фото, если они были в последних сообщениях
    for ev in recent:
        photo_id = ev.get("photo_id")
        if photo_id:
            try:
                await update.message.reply_photo(
                    photo_id,
                    caption=f'📷 <a href="tg://user?id={ev["user_id"]}">{html.escape(ev["name"])}</a>',
                    parse_mode="HTML",
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

    application.add_handler(TypeHandler(Update, log_update), group=-2)

    # Мотивация + fallback — выше conv_handler
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, motivation_or_fallback),
        group=-1,
    )

    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(conv_handler)
    application.add_handler(PollAnswerHandler(receive_poll_answer))
    application.add_handler(ChatJoinRequestHandler(handle_join_request))

    print("Bot is running...")
    application.run_polling()


if __name__ == "__main__":
    main()
