import os
import asyncio
import logging
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MASTER_CHAT_ID = os.getenv("MASTER_CHAT_ID")
SECOND_MASTER_CHAT_ID = os.getenv("SECOND_MASTER_CHAT_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.warning("Supabase credentials not fully set!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

application = None
if BOT_TOKEN:
    application = ApplicationBuilder().token(BOT_TOKEN).build()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Записаться ✨", web_app=WebAppInfo(url="https://tatoooo.onrender.com"))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! Добро пожаловать в нашу тату-студию 🖤\nНажми кнопку ниже, чтобы выбрать окошко для записи:",
        reply_markup=reply_markup
    )

if application:
    application.add_handler(CommandHandler("start", start_command))

@app.on_event("startup")
async def startup_event():
    if application:
        await application.initialize()
        await application.start()
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if render_url:
            webhook_url = f"{render_url}/webhook"
            await application.bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook successfully set to {webhook_url}")

@app.on_event("shutdown")
async def shutdown_event():
    if application:
        await application.stop()
        await application.shutdown()

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not application:
        return {"status": "bot not configured"}
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "Tattoo Studio API is running!"

@app.get("/api/data")
async def get_all_data():
    if not supabase:
        return {"slots": [], "works": [], "reviews": [], "settings": {}, "master_ids": []}
    
    try:
        slots_res = supabase.table("slots").select("*").order("id").execute().data or []
        works_res = supabase.table("works").select("*").order("id", desc=True).execute().data or []
        reviews_res = supabase.table("reviews").select("*").order("id", desc=True).execute().data or []
        settings_res = supabase.table("settings").select("*").execute().data or []

        normalized_works = []
        for w in works_res:
            img = w.get("image_url") or w.get("url") or w.get("image") or ""
            normalized_works.append({
                "id": w.get("id"),
                "image_url": img,
                "url": img,
                "title": w.get("title") or "Work"
            })

        normalized_reviews = []
        for r in reviews_res:
            photo = r.get("photo_url") or r.get("url") or r.get("photo") or ""
            normalized_reviews.append({
                "id": r.get("id"),
                "author": r.get("author") or "Клиент",
                "text": r.get("text") or "",
                "photo_url": photo,
                "url": photo
            })

        settings = {item["key"]: item["value"] for item in settings_res if "key" in item and "value" in item}
        
        raw_masters = [MASTER_CHAT_ID, SECOND_MASTER_CHAT_ID]
        master_ids = []
        for m in raw_masters:
            if m:
                try:
                    master_ids.append(int(m))
                except ValueError:
                    pass

        return {
            "slots": slots_res,
            "works": normalized_works,
            "reviews": normalized_reviews,
            "settings": settings,
            "master_ids": master_ids
        }
    except Exception as e:
        logger.error(f"Error fetching normalized data: {e}")
        return {"slots": [], "works": [], "reviews": [], "settings": {}, "master_ids": []}

@app.get("/api/settings")
async def get_settings():
    if not supabase:
        return {}
    response = supabase.table("settings").select("*").execute()
    return {item["key"]: item["value"] for item in response.data}

@app.get("/api/slots")
async def get_slots():
    if not supabase:
        return []
    response = supabase.table("slots").select("*").order("id").execute()
    return response.data

@app.post("/api/slots")
async def add_slot(request: Request):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    try:
        body = await request.json()
        date = body.get("date")
        time = body.get("time")
    except Exception:
        form = await request.form()
        date = form.get("date")
        time = form.get("time")

    if not date or not time:
        raise HTTPException(status_code=400, detail="Date and time are required")

    supabase.table("slots").insert({"date": date, "time": time, "is_booked": False}).execute()
    return {"status": "success"}

@app.delete("/api/slots/{slot_id}")
async def delete_slot(slot_id: int):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    supabase.table("slots").delete().eq("id", slot_id).execute()
    return {"status": "success"}

@app.post("/api/book")
async def book_slot(
    slot_id: int = Form(...),
    client_name: str = Form(...),
    client_tg: str = Form(...),
    tattoo_size: str = Form(...),
    tattoo_photo: UploadFile = File(None)
):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    photo_url = ""
    if tattoo_photo:
        file_bytes = await tattoo_photo.read()
        file_name = f"booking_{int(asyncio.get_event_loop().time())}_{tattoo_photo.filename}"
        supabase.storage.from_("uploads").upload(file_name, file_bytes)
        photo_url = supabase.storage.from_("uploads").get_public_url(file_name)

    # Надежное обновление статуса слота в базе
    update_res = supabase.table("slots").update({
        "is_booked": True,
        "client_name": client_name,
        "client_tg": client_tg,
        "tattoo_size": tattoo_size,
        "tattoo_photo": photo_url
    }).eq("id", slot_id).execute()

    logger.info(f"Slot {slot_id} booked successfully: {update_res}")

    slot_res = supabase.table("slots").select("*").eq("id", slot_id).execute()
    slot_data = slot_res.data[0] if slot_res.data else {}

    if BOT_TOKEN:
        bot = Bot(token=BOT_TOKEN)
        msg_text = (
            f"🔥 Новая запись на сеанс!\n\n"
            f"👤 Клиент: {client_name}\n"
            f"🔗 Telegram: @{client_tg.lstrip('@')}\n"
            f"📅 Дата и время: {slot_data.get('date')} в {slot_data.get('time')}\n"
            f"📐 Размер и место: {tattoo_size}"
        )
        chat_ids = [MASTER_CHAT_ID, SECOND_MASTER_CHAT_ID]
        for cid in chat_ids:
            if cid:
                try:
                    if photo_url:
                        await bot.send_photo(chat_id=cid, photo=photo_url, caption=msg_text)
                    else:
                        await bot.send_message(chat_id=cid, text=msg_text)
                except Exception as e:
                    logger.error(f"Error sending notification to {cid}: {e}")

    return {"status": "success"}

@app.get("/api/works")
async def get_works():
    if not supabase:
        return []
    response = supabase.table("works").select("*").order("id", desc=True).execute()
    return response.data

# Поддержка МНОЖЕСТВЕННОЙ загрузки работ за один раз
@app.post("/api/works")
async def add_works(files: list[UploadFile] = File(...)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    try:
        uploaded_urls = []
        for file in files:
            file_bytes = await file.read()
            file_name = f"work_{int(asyncio.get_event_loop().time())}_{file.filename}"
            supabase.storage.from_("uploads").upload(file_name, file_bytes)
            public_url = supabase.storage.from_("uploads").get_public_url(file_name)
            
            supabase.table("works").insert({"image_url": public_url, "url": public_url}).execute()
            uploaded_urls.append(public_url)
            
        return {"status": "success", "urls": uploaded_urls}
    except Exception as e:
        logger.error(f"Error uploading works: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/works/{work_id}")
async def delete_work(work_id: int):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    supabase.table("works").delete().eq("id", work_id).execute()
    return {"status": "success"}

@app.get("/api/reviews")
async def get_reviews():
    if not supabase:
        return []
    response = supabase.table("reviews").select("*").order("id", desc=True).execute()
    return response.data

@app.post("/api/reviews")
async def add_review(author: str = Form(...), text: str = Form(...), photo: UploadFile = File(None)):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    photo_url = ""
    if photo:
        file_bytes = await photo.read()
        file_name = f"review_{int(asyncio.get_event_loop().time())}_{photo.filename}"
        supabase.storage.from_("uploads").upload(file_name, file_bytes)
        photo_url = supabase.storage.from_("uploads").get_public_url(file_name)

    supabase.table("reviews").insert({"author": author, "text": text, "photo_url": photo_url}).execute()
    return {"status": "success"}

@app.delete("/api/reviews/{review_id}")
async def delete_review(review_id: int):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    supabase.table("reviews").delete().eq("id", review_id).execute()
    return {"status": "success"}