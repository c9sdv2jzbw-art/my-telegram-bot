from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

TOKEN = "8709698676:AAGquH1s1nQ2mLN6nJbdWCAFDy0a_GcZ4tA"

async def start(update, context):
    await update.message.reply_text("Привет! Напиши что-нибудь.")

async def echo(update, context):
    await update.message.reply_text(update.message.text)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, echo))
app.run_polling()