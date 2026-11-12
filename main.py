import asyncio
from datetime import datetime
import os
import sqlite3
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
MASTER_CHAT_ID = os.getenv("MASTER_CHAT_ID", "YOUR_MASTER_CHAT_ID")
SECOND_MASTER_CHAT_ID = os.getenv("SECOND_MASTER_CHAT_ID", "6573422629")

bot = Bot(token=BOT_TOKEN)

UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


def init_db():
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            is_booked BOOLEAN DEFAULT 0,
            client_name TEXT,
            client_tg TEXT,
            tattoo_size TEXT,
            tattoo_photo TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_url TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT,
            text TEXT,
            photo_url TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
  cursor.execute(
      "INSERT OR IGNORE INTO settings (key, value) VALUES ('studio_name',"
      " 'Tattoo Studio')"
  )
  cursor.execute(
      "INSERT OR IGNORE INTO settings (key, value) VALUES ('bg_url',"
      " 'https://images.unsplash.com/photo-1544457070-4cd773b4d71e?q=80&w=1000&auto=format&fit=crop')"
  )
  conn.commit()
  conn.close()


init_db()


@app.get("/", response_class=HTMLResponse)
async def index():
  if os.path.exists("index.html"):
    with open("index.html", "r", encoding="utf-8") as f:
      return f.read()
  return "index.html not found", 404


@app.get("/api/data")
async def get_data():
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()

  cursor.execute("""
        SELECT id, date, time, is_booked, client_name, client_tg, tattoo_size, tattoo_photo 
        FROM slots
    """)
  slots = [{
      "id": r[0],
      "date": r[1],
      "time": r[2],
      "is_booked": bool(r[3]),
      "client_name": r[4],
      "client_tg": r[5],
      "tattoo_size": r[6],
      "tattoo_photo": r[7],
  } for r in cursor.fetchall()]

  cursor.execute("SELECT id, image_url FROM works")
  works = [{"id": r[0], "url": r[1]} for r in cursor.fetchall()]

  cursor.execute("SELECT id, author, text, photo_url FROM reviews")
  reviews = [{
      "id": r[0],
      "author": r[1],
      "text": r[2],
      "photo_url": r[3],
  } for r in cursor.fetchall()]

  cursor.execute("SELECT key, value FROM settings")
  settings = {row[0]: row[1] for row in cursor.fetchall()}

  conn.close()
  return JSONResponse({
      "slots": slots,
      "works": works,
      "reviews": reviews,
      "settings": settings,
  })


@app.post("/api/admin/add_slot")
async def add_slot(date: str = Form(...), time: str = Form(...)):
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO slots (date, time, is_booked) VALUES (?, ?, 0)", (date, time)
  )
  conn.commit()
  conn.close()
  return {"status": "success"}


@app.post("/api/admin/delete_slot")
async def delete_slot(slot_id: int = Form(...)):
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
  conn.commit()
  conn.close()
  return {"status": "success"}


@app.post("/api/admin/cancel_booking")
async def cancel_booking(slot_id: int = Form(...)):
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        UPDATE slots 
        SET is_booked = 0, client_name = NULL, client_tg = NULL, tattoo_size = NULL, tattoo_photo = NULL 
        WHERE id = ?
    """,
      (slot_id,),
  )
  conn.commit()
  conn.close()
  return {"status": "success"}


@app.post("/api/admin/add_work")
async def add_work(file: UploadFile = File(...)):
  file_path = os.path.join(UPLOAD_DIR, file.filename)
  with open(file_path, "wb") as buffer:
    content = await file.read()
    buffer.write(content)

  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute("INSERT INTO works (image_url) VALUES (?)", (f"/{file_path}",))
  conn.commit()
  conn.close()
  return {"status": "success"}


@app.post("/api/add_review")
async def add_review(
    author: str = Form(...),
    text: str = Form(...),
    photo: UploadFile = File(None),
):
  photo_url = None
  if photo and photo.filename:
    file_path = os.path.join(UPLOAD_DIR, photo.filename)
    with open(file_path, "wb") as buffer:
      content = await photo.read()
      buffer.write(content)
    photo_url = f"/{file_path}"

  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO reviews (author, text, photo_url) VALUES (?, ?, ?)",
      (author, text, photo_url),
  )
  conn.commit()
  conn.close()
  return {"status": "success"}


@app.post("/api/admin/update_settings")
async def update_settings(studio_name: str = Form(...), bg_url: str = Form(...)):
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO settings (key, value) VALUES ('studio_name', ?)",
      (studio_name,),
  )
  cursor.execute(
      "INSERT OR REPLACE INTO settings (key, value) VALUES ('bg_url', ?)",
      (bg_url,),
  )
  conn.commit()
  conn.close()
  return {"status": "success"}


@app.post("/submit")
async def submit_appointment(
    slot_id: int = Form(None),
    client_name: str = Form(...),
    tattoo_size: str = Form(...),
    telegram_id: str = Form(...),
    tattoo_photo: UploadFile = File(...),
):
  if not slot_id:
    raise HTTPException(
        status_code=400, detail="Пожалуйста, выберите свободный слот времени!"
    )

  file_path = os.path.join(UPLOAD_DIR, tattoo_photo.filename)
  with open(file_path, "wb") as buffer:
    content = await tattoo_photo.read()
    buffer.write(content)

  photo_url = f"/{file_path}"

  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()

  cursor.execute("SELECT date, time, is_booked FROM slots WHERE id = ?", (slot_id,))
  slot = cursor.fetchone()

  if not slot or slot[2] == 1:
    conn.close()
    raise HTTPException(status_code=400, detail="Этот слот уже занят!")

  slot_date, slot_time = slot[0], slot[1]

  cursor.execute(
      """
        UPDATE slots 
        SET is_booked = 1, client_name = ?, client_tg = ?, tattoo_size = ?, tattoo_photo = ? 
        WHERE id = ?
    """,
      (client_name, telegram_id, tattoo_size, photo_url, slot_id),
  )
  conn.commit()
  conn.close()

  msg_text = (
      "🔥 **Новая запись на сеанс!**\n\n"
      f"👤 Клиент: {client_name}\n"
      f"🔗 Telegram: `{telegram_id}`\n"
      f"📅 Дата и время: {slot_date} в {slot_time}\n"
      f"📐 Размер и место: {tattoo_size}"
  )

  if MASTER_CHAT_ID and MASTER_CHAT_ID != "YOUR_MASTER_CHAT_ID":
    try:
      with open(file_path, "rb") as photo_file:
        await bot.send_photo(
            chat_id=MASTER_CHAT_ID,
            photo=photo_file,
            caption=msg_text,
            parse_mode="Markdown",
        )
    except Exception as e:
      print(f"Error sending to master 1: {e}")

  if SECOND_MASTER_CHAT_ID:
    try:
      with open(file_path, "rb") as photo_file:
        await bot.send_photo(
            chat_id=SECOND_MASTER_CHAT_ID,
            photo=photo_file,
            caption=msg_text,
            parse_mode="Markdown",
        )
    except Exception as e:
      print(f"Error sending to master 2: {e}")

  return JSONResponse({"status": "success"})


# Логика телеграм-бота (/start)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user_name = update.effective_user.first_name
  welcome_text = (
      f"Привет, {user_name}! 👋\n\n"
      "Добро пожаловать в нашу тату-студию. Здесь ты можешь выбрать удобное"
      " время, посмотреть портфолио и записаться на сеанс в пару кликов."
  )
  keyboard = [[
      InlineKeyboardButton(
          "🔥 Открыть студию и записаться",
          web_app={"url": "https://tatoooo.onrender.com"},
      )
  ]]
  reply_markup = InlineKeyboardMarkup(keyboard)
  await update.message.reply_text(
      welcome_text, reply_markup=reply_markup, parse_mode="Markdown"
  )


async def run_telegram_bot():
  if BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN":
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    await application.initialize()
    await application.start()
    await application.updater.start_polling()


@app.on_event("startup")
async def startup_event():
  asyncio.create_task(run_telegram_bot())