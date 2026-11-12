import os
import asyncio
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart
import uvicorn

TOKEN = "9194082999:AAEmD6ZXT-Ji4jrS_RLcB7Uo_ZE6VBDquvQ"
MASTER_CHAT_ID = 374188835

app = FastAPI()
bot = Bot(token=TOKEN)
dp = Dispatcher()

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = os.path.join(BASE_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()
@app.post("/submit")
async def submit_appointment(
    request: Request,
    client_name: str = Form(...),
    session_date: str = Form(...),
    session_time: str = Form(...),
    tattoo_size: str = Form(...),
    telegram_id: str = Form(default="неизвестно"),
    tattoo_photo: UploadFile = File(...)
):
    file_path = f"static/uploads/{tattoo_photo.filename}"
    with open(file_path, "wb") as buffer:
        content = await tattoo_photo.read()
        buffer.write(content)

    msg_text = (
        "🔥 **Новая запись на сеанс!**\n\n"
        f"👤 Клиент: {client_name} (Telegram: `{telegram_id}`)\n"
        f"📅 Дата: {session_date}\n"
        f"⏰ Время: {session_time}\n"
        f"📏 Размер и место: {tattoo_size}"
    )

    photo_file = FSInputFile(file_path)
    await bot.send_photo(
        chat_id=MASTER_CHAT_ID,
        photo=photo_file,
        caption=msg_text,
        parse_mode="Markdown"
    )

    return HTMLResponse("<html><body style='background:#3a060e;color:#ffdf73;text-align:center;padding-top:50px;font-family:sans-serif;'><h2>Заявка успешно отправлена мастеру!</h2><p>Можешь закрыть это окно.</p></body></html>")

@dp.message(CommandStart())
async def cmd_start(message: Message):
    web_app_url = "https://9c7e0385a64bcf.lhr.life"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть студию (Записаться)", web_app=WebAppInfo(url=web_app_url))]
        ]
    )
    await message.answer("Добро пожаловать в тату-студию в стиле СССР iOS Glass!", reply_markup=keyboard)

async def main():
    # Получаем порт из окружения хостинга (для Render) или ставим 8000 по умолчанию для локального теста
    port = int(os.environ.get("PORT", 8000))
    
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    print("🚀 Сервер и бот успешно запущены!")
    await asyncio.gather(
        server.serve(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
