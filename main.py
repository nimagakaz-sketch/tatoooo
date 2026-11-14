import asyncio
import os
from typing import List
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from supabase import Client, create_client
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
MASTER_CHAT_ID = os.getenv("MASTER_CHAT_ID", "YOUR_MASTER_CHAT_ID")
SECOND_MASTER_CHAT_ID = os.getenv("SECOND_MASTER_CHAT_ID", "6573422629")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

supabase: Client = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY
    else None
)
bot = Bot(token=BOT_TOKEN)

app.mount("/static", StaticFiles(directory="static"), name="static")


async def upload_to_supabase(file: UploadFile) -> str:
  if not supabase or not file or not file.filename:
    return None
  file_content = await file.read()
  file_path = f"{int(asyncio.get_event_loop().time())}_{file.filename}"

  supabase.storage.from_("uploads").upload(
      path=file_path, file=file_content, file_options={"content-type": file.content_type}
  )
  public_url = f"{SUPABASE_URL}/storage/v1/object/public/uploads/{file_path}"
  return public_url


@app.get("/", response_class=HTMLResponse)
async def index():
  if os.path.exists("index.html"):
    with open("index.html", "r", encoding="utf-8") as f:
      return f.read()
  return "index.html not found", 404


@app.get("/api/data")
async def get_data():
  if not supabase:
    return JSONResponse({"slots": [], "works": [], "reviews": [], "settings": {}})

  slots_res = supabase.table("slots").select("*").execute()
  works_res = supabase.table("works").select("*").execute()
  reviews_res = supabase.table("reviews").select("*").execute()
  settings_res = supabase.table("settings").select("*").execute()

  slots = [{
      "id": r["id"],
      "date": r["date"],
      "time": r["time"],
      "is_booked": bool(r["is_booked"]),
      "client_name": r.get("client_name"),
      "client_tg": r.get("client_tg"),
      "tattoo_size": r.get("tattoo_size"),
      "tattoo_photo": r.get("tattoo_photo"),
  } for r in slots_res.data]

  works = [{"id": r["id"], "url": r["image_url"]} for r in works_res.data]
  reviews = [{
      "id": r["id"],
      "author": r["author"],
      "text": r["text"],
      "photo_url": r.get("photo_url"),
  } for r in reviews_res.data]
  settings = {r["key"]: r["value"] for r in settings_res.data}

  return JSONResponse({
      "slots": slots,
      "works": works,
      "reviews": reviews,
      "settings": settings,
  })


@app.post("/api/admin/add_slot")
async def add_slot(date: str = Form(...), time: str = Form(...)):
  if supabase:
    supabase.table("slots").insert(
        {"date": date, "time": time, "is_booked": False}
    ).execute()
  return {"status": "success"}


@app.post("/api/admin/delete_slot")
async def delete_slot(slot_id: int = Form(...)):
  if supabase:
    supabase.table("slots").delete().eq("id", slot_id).execute()
  return {"status": "success"}


@app.post("/api/admin/cancel_booking")
async def cancel_booking(slot_id: int = Form(...)):
  if supabase:
    supabase.table("slots").update({
        "is_booked": False,
        "client_name": None,
        "client_tg": None,
        "tattoo_size": None,
        "tattoo_photo": None,
    }).eq("id", slot_id).execute()
  return {"status": "success"}


@app.post("/api/admin/add_work")
async def add_work(files: List[UploadFile] = File(...)):
  if not supabase:
    return {"status": "error"}
  for file in files:
    if file.filename:
      public_url = await upload_to_supabase(file)
      if public_url:
        supabase.table("works").insert({"image_url": public_url}).execute()
  return {"status": "success"}


@app.post("/api/admin/delete_work")
async def delete_work(work_id: int = Form(...)):
  if supabase:
    supabase.table("works").delete().eq("id", work_id).execute()
  return {"status": "success"}


@app.post("/api/add_review")
async def add_review(
    author: str = Form(...),
    text: str = Form(...),
    photo: UploadFile = File(None),
):
  photo_url = None
  if photo and photo.filename and supabase:
    photo_url = await upload_to_supabase(photo)

  if supabase:
    supabase.table("reviews").insert({
        "author": author,
        "text": text,
        "photo_url": photo_url,
    }).execute()
  return {"status": "success"}


@app.post("/api/admin/update_settings")
async def update_settings(studio_name: str = Form(...), bg_url: str = Form(...)):
  if supabase:
    supabase.table("settings").upsert({
        "key": "studio_name",
        "value": studio_name,
    }).execute()
    supabase.table("settings").upsert(
        {"key": "bg_url", "value": bg_url}
    ).execute()
  return {"status": "success"}


@app.post("/api/admin/toggle_snow")
async def toggle_snow(enabled: str = Form(...)):
  if supabase:
    supabase.table("settings").upsert(
        {"key": "snow_enabled", "value": enabled}
    ).execute()
  return {"status": "success"}


@app.post("/submit")
async def submit_appointment(
    slot_id: int = Form(None),
    client_name: str = Form(...),
    tattoo_size: str = Form(...),
    telegram_id: str = Form(...),
    tattoo_photo: UploadFile = File(...),
):
  if not slot_id or not supabase:
    raise HTTPException(status_code=400, detail="Ошибка бронирования")

  photo_url = await upload_to_supabase(tattoo_photo)
  if not photo_url:
    raise HTTPException(status_code=400, detail="Ошибка загрузки фото эскиза")

  slot_res = supabase.table("slots").select("*").eq("id", slot_id).execute()
  if not slot_res.data or slot_res.data[0]["is_booked"]:
    raise HTTPException(status_code=400, detail="Этот слот уже занят!")

  slot = slot_res.data[0]
  slot_date, slot_time = slot["date"], slot["time"]

  supabase.table("slots").update({
      "is_booked": True,
      "client_name": client_name,
      "client_tg": telegram_id,
      "tattoo_size": tattoo_size,
      "tattoo_photo": photo_url,
  }).eq("id", slot_id).execute()

  msg_text = (
      "🔥 **Новая запись на сеанс!**\n\n"
      f"👤 Клиент: {client_name}\n"
      f"🔗 Telegram: `{telegram_id}`\n"
      f"📅 Дата и время: {slot_date} в {slot_time}\n"
      f"📐 Размер и место: {tattoo_size}"
  )

  if MASTER_CHAT_ID and MASTER_CHAT_ID != "YOUR_MASTER_CHAT_ID":
    try:
      await bot.send_photo(
          chat_id=MASTER_CHAT_ID,
          photo=photo_url,
          caption=msg_text,
          parse_mode="Markdown",
      )
    except Exception as e:
      print(f"Error sending to master 1: {e}")

  if SECOND_MASTER_CHAT_ID:
    try:
      await bot.send_photo(
          chat_id=SECOND_MASTER_CHAT_ID,
          photo=photo_url,
          caption=msg_text,
          parse_mode="Markdown",
      )
    except Exception as e:
      print(f"Error sending to master 2: {e}")

  return JSONResponse({"status": "success"})


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