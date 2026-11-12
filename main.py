from datetime import datetime
import os
import sqlite3
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Bot, FSInputFile

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
MASTER_CHAT_ID = os.getenv("MASTER_CHAT_ID", "YOUR_MASTER_CHAT_ID")
bot = Bot(token=BOT_TOKEN)

UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Инициализация базы данных SQLite
def init_db():
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  # Таблица свободных/занятых слотов
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            is_booked BOOLEAN DEFAULT 0,
            client_name TEXT,
            client_tg TEXT
        )
    """)
  # Таблица портфолио
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_url TEXT
        )
    """)
  # Таблица отзывов
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT,
            text TEXT
        )
    """)
  conn.commit()
  conn.close()


init_db()


@app.get("/", response_class=HTMLResponse)
async def index():
  if os.path.exists("index.html"):
    with open("index.html", "r", encoding="utf-8") as f:
      return f.read()
  return "index.html not found", 404


# API: Получить список свободных слотов, работ и отзывов
@app.get("/api/data")
async def get_data():
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()

  cursor.execute("SELECT id, date, time, is_booked FROM slots")
  slots = [{
      "id": r[0],
      "date": r[1],
      "time": r[2],
      "is_booked": bool(r[3]),
  } for r in cursor.fetchall()]

  cursor.execute("SELECT id, image_url FROM works")
  works = [{"id": r[0], "url": r[1]} for r in cursor.fetchall()]

  cursor.execute("SELECT id, author, text FROM reviews")
  reviews = [{"id": r[0], "author": r[1], "text": r[2]} for r in cursor.fetchall()]

  conn.close()
  return JSONResponse({"slots": slots, "works": works, "reviews": reviews})


# API для мастера: Добавить свободный слот
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


# API для мастера: Загрузить работу
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


# API для мастера: Добавить отзыв
@app.post("/api/admin/add_review")
async def add_review(author: str = Form(...), text: str = Form(...)):
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO reviews (author, text) VALUES (?, ?)", (author, text)
  )
  conn.commit()
  conn.close()
  return {"status": "success"}


# Обработка записи клиента на выбранный слот
@app.post("/submit")
async def submit_appointment(
    slot_id: int = Form(...),
    client_name: str = Form(...),
    tattoo_size: str = Form(...),
    telegram_id: str = Form(...),
    tattoo_photo: UploadFile = File(...),
):
  conn = sqlite3.connect("database.db")
  cursor = conn.cursor()

  cursor.execute("SELECT date, time, is_booked FROM slots WHERE id = ?", (slot_id,))
  slot = cursor.fetchone()

  if not slot or slot[2] == 1:
    conn.close()
    raise HTTPException(status_code=400, detail="Этот слот уже занят!")

  slot_date, slot_time = slot[0], slot[1]

  # Бронируем слот в БД
  cursor.execute(
      "UPDATE slots SET is_booked = 1, client_name = ?, client_tg = ? WHERE id"
      " = ?",
      (client_name, telegram_id, slot_id),
  )
  conn.commit()
  conn.close()

  # Сохраняем фото эскиза
  file_path = os.path.join(UPLOAD_DIR, tattoo_photo.filename)
  with open(file_path, "wb") as buffer:
    content = await tattoo_photo.read()
    buffer.write(content)

  # Отправляем уведомление мастеру в Telegram
  msg_text = (
      "🔥 **Новая запись на сеанс!**\n\n"
      f"👤 Клиент: {client_name}\n"
      f"🔗 Telegram: `{telegram_id}`\n"
      f"📅 Дата и время: {slot_date} в {slot_time}\n"
      f"📐 Размер и место: {tattoo_size}"
  )

  photo_file = FSInputFile(file_path)
  await bot.send_photo(
      chat_id=MASTER_CHAT_ID, photo=photo_file, caption=msg_text, parse_mode="Markdown"
  )

  return HTMLResponse(
      """
        <script>
            alert("Запись успешно оформлена! Мастер скоро свяжется с вами.");
            window.location.href = "/";
        </script>
    """
  )