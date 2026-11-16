import os
import hmac
import json
import time
import uuid
import hashlib
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from supabase import Client, create_client
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tattoo")

# ---------- Настройки (переменные окружения на Render) ----------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")          # ВАЖНО: service_role ключ (только на сервере!)
BOT_TOKEN = os.getenv("BOT_TOKEN")
MASTER_CHAT_ID = os.getenv("MASTER_CHAT_ID")
SECOND_MASTER_CHAT_ID = os.getenv("SECOND_MASTER_CHAT_ID")
WEBAPP_URL = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://tatoooo.onrender.com").rstrip("/")
ADMIN_USERNAMES = {u.strip().lstrip("@").lower() for u in os.getenv("ADMIN_USERNAMES", "youngyannniii,liizzytatt").split(",") if u.strip()}
ADMIN_IDS = {str(i).strip() for i in (MASTER_CHAT_ID, SECOND_MASTER_CHAT_ID) if i}
BUCKET = "uploads"
MAX_FILE = 10 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
WEBHOOK_SECRET = hashlib.sha256(("wh:" + (BOT_TOKEN or "")).encode()).hexdigest()[:32]

_db: Optional[Client] = None


def sb() -> Client:
    global _db
    if _db is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise HTTPException(503, "База данных не настроена (SUPABASE_URL / SUPABASE_KEY)")
        _db = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _db


# ---------- Telegram-бот ----------
application = ApplicationBuilder().token(BOT_TOKEN).build() if BOT_TOKEN else None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Записаться ✨", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await update.message.reply_text(
        "Привет! Добро пожаловать в нашу тату-студию 🖤\nНажми кнопку ниже, чтобы выбрать окошко для записи:",
        reply_markup=kb,
    )


if application:
    application.add_handler(CommandHandler("start", start_command))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if application:
        await application.initialize()
        await application.start()
        try:
            await application.bot.set_webhook(url=f"{WEBAPP_URL}/webhook", secret_token=WEBHOOK_SECRET)
            logger.info("Webhook set: %s/webhook", WEBAPP_URL)
        except Exception as e:  # сайт должен подняться, даже если Telegram недоступен
            logger.error("Не удалось поставить webhook: %s", e)
    yield
    if application:
        await application.stop()
        await application.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Exception)
async def on_error(_: Request, exc: Exception):
    logger.exception("Необработанная ошибка: %s", exc)
    return JSONResponse({"detail": "Ошибка сервера. Попробуйте ещё раз."}, status_code=500)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not application:
        return {"status": "bot not configured"}
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(403, "forbidden")
    update = Update.de_json(await request.json(), application.bot)
    await application.process_update(update)
    return {"status": "ok"}


# ---------- Проверка, что запрос от мастера (подпись Telegram initData) ----------
def get_admin(init_data: str) -> Optional[dict]:
    if not BOT_TOKEN or not init_data:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        got = data.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
            return None
        if time.time() - int(data.get("auth_date", "0")) > 86400:
            return None
        user = json.loads(data["user"])
    except Exception:
        return None
    if (user.get("username") or "").lower() in ADMIN_USERNAMES or str(user.get("id")) in ADMIN_IDS:
        return user
    return None


def require_admin(x_init_data: str = Header(default="")) -> dict:
    user = get_admin(x_init_data)
    if not user:
        raise HTTPException(403, "Нет доступа. Откройте приложение из Telegram под аккаунтом мастера.")
    return user


# ---------- Файлы ----------
def save_image(prefix: str, data: bytes, content_type: Optional[str]) -> str:
    ext = IMAGE_TYPES.get((content_type or "").lower())
    if not ext:
        raise HTTPException(400, "Можно загружать только фото (jpg, png, webp, gif)")
    if len(data) > MAX_FILE:
        raise HTTPException(413, "Файл больше 10 МБ")
    path = f"{prefix}/{uuid.uuid4().hex}.{ext}"
    sb().storage.from_(BUCKET).upload(path, data, {"content-type": content_type})
    return str(sb().storage.from_(BUCKET).get_public_url(path)).rstrip("?")


def remove_image(url: Optional[str]) -> None:
    if not url or f"/{BUCKET}/" not in url:
        return
    try:
        sb().storage.from_(BUCKET).remove([url.split(f"/{BUCKET}/", 1)[1].split("?")[0]])
    except Exception as e:
        logger.warning("Не удалось удалить файл %s: %s", url, e)


async def read_image(prefix: str, f: Optional[UploadFile]) -> str:
    if not f or not f.filename:
        return ""
    data = await f.read()
    return await asyncio.to_thread(save_image, prefix, data, f.content_type) if data else ""


# ---------- Страница и данные ----------
@app.get("/", response_class=HTMLResponse)
def index():
    page = Path(__file__).with_name("index.html")
    return page.read_text(encoding="utf-8") if page.exists() else "Tattoo Studio API is running!"


@app.get("/healthz")
def healthz():
    try:
        sb().table("settings").select("key").limit(1).execute()
        return {"ok": True, "db": True}
    except Exception as e:
        return JSONResponse({"ok": False, "db": False, "error": str(e)[:200]}, status_code=503)


@app.get("/api/data")
def get_data(x_init_data: str = Header(default="")):
    db = sb()
    is_admin = get_admin(x_init_data) is not None
    settings = {r["key"]: r["value"] for r in db.table("settings").select("*").execute().data}
    rows = db.table("slots").select("*").order("date").order("time").execute().data
    slots = []
    for s in rows:
        item = {"id": s["id"], "date": str(s["date"]), "time": str(s["time"])[:5], "is_booked": bool(s.get("is_booked"))}
        if is_admin:  # данные клиентов видит только мастер
            for k in ("client_name", "client_tg", "tattoo_size", "tattoo_photo"):
                item[k] = s.get(k) or ""
        slots.append(item)
    works = [{"id": w["id"], "url": w.get("image_url") or w.get("url") or ""}
             for w in db.table("works").select("*").order("id", desc=True).execute().data]
    reviews = [{"id": r["id"], "author": r.get("author") or "", "text": r.get("text") or "", "photo_url": r.get("photo_url") or ""}
               for r in db.table("reviews").select("*").order("id", desc=True).execute().data]
    return {"settings": settings, "slots": slots, "works": works, "reviews": reviews}


# ---------- Запись клиента ----------
@app.post("/submit")
async def submit(
    slot_id: int = Form(...),
    client_name: str = Form(...),
    tattoo_size: str = Form(...),
    telegram_id: str = Form(""),
    tattoo_photo: Optional[UploadFile] = File(None),
):
    name, size, tg = client_name.strip()[:80], tattoo_size.strip()[:200], telegram_id.strip()[:80]
    if not name or not size:
        raise HTTPException(400, "Заполните имя и размер татуировки")
    photo_url = await read_image("sketch", tattoo_photo)

    def book():  # атомарно: займётся только свободный слот — двойной записи не будет
        return sb().table("slots").update({
            "is_booked": True, "client_name": name, "client_tg": tg, "tattoo_size": size, "tattoo_photo": photo_url,
        }).eq("id", slot_id).eq("is_booked", False).execute()

    res = await asyncio.to_thread(book)
    if not res.data:
        await asyncio.to_thread(remove_image, photo_url)
        raise HTTPException(409, "Это время уже занято. Выберите другое.")
    slot = res.data[0]
    await notify_masters(name, tg, slot, size, photo_url)
    return {"status": "ok"}


async def notify_masters(name, tg, slot, size, photo_url):
    if not BOT_TOKEN:
        return
    contact = tg if tg.startswith("@") or " " in tg or not tg else "@" + tg
    text = (f"🔥 Новая запись на сеанс!\n\n👤 Клиент: {name}\n🔗 Telegram: {contact or 'не указан'}\n"
            f"📅 Дата и время: {slot.get('date')} в {str(slot.get('time'))[:5]}\n📐 Размер и место: {size}")
    bot = application.bot if application else Bot(BOT_TOKEN)
    for cid in {c for c in (MASTER_CHAT_ID, SECOND_MASTER_CHAT_ID) if c}:
        try:
            if photo_url:
                await bot.send_photo(chat_id=cid, photo=photo_url, caption=text)
            else:
                await bot.send_message(chat_id=cid, text=text)
        except Exception as e:
            logger.error("Уведомление %s не отправлено: %s", cid, e)
            try:
                await bot.send_message(chat_id=cid, text=text + (f"\n🖼 {photo_url}" if photo_url else ""))
            except Exception:
                pass


# ---------- Отзывы ----------
@app.post("/api/add_review")
async def add_review(author: str = Form(...), text: str = Form(...), photo: Optional[UploadFile] = File(None)):
    author, text = author.strip()[:60], text.strip()[:1000]
    if not author or not text:
        raise HTTPException(400, "Заполните имя и текст отзыва")
    photo_url = await read_image("review", photo)
    await asyncio.to_thread(lambda: sb().table("reviews").insert({"author": author, "text": text, "photo_url": photo_url}).execute())
    return {"status": "ok"}


# ---------- Панель мастера ----------
def upsert_settings(pairs: dict):
    sb().table("settings").upsert([{"key": k, "value": v} for k, v in pairs.items()], on_conflict="key").execute()


@app.post("/api/admin/toggle_snow")
def toggle_snow(enabled: str = Form(...), _: dict = Depends(require_admin)):
    upsert_settings({"snow_enabled": "true" if enabled.strip().lower() == "true" else "false"})
    return {"status": "ok"}


@app.post("/api/admin/update_settings")
def update_settings(studio_name: str = Form(""), bg_url: str = Form(""), _: dict = Depends(require_admin)):
    bg = bg_url.strip()
    if bg and not bg.lower().startswith(("https://", "http://")):
        raise HTTPException(400, "Ссылка на фон должна начинаться с https://")
    upsert_settings({"studio_name": studio_name.strip()[:60] or "Tattoo Studio", "bg_url": bg})
    return {"status": "ok"}


@app.post("/api/admin/add_slot")
def add_slot(date: str = Form(...), time: str = Form(...), _: dict = Depends(require_admin)):
    try:
        datetime.strptime(date, "%Y-%m-%d")
        datetime.strptime(time[:5], "%H:%M")
    except ValueError:
        raise HTTPException(400, "Неверная дата или время")
    time = time[:5]
    if sb().table("slots").select("id").eq("date", date).eq("time", time).execute().data:
        raise HTTPException(409, "Такое окошко уже есть")
    sb().table("slots").insert({"date": date, "time": time, "is_booked": False}).execute()
    return {"status": "ok"}


@app.post("/api/admin/delete_slot")
def delete_slot(slot_id: int = Form(...), _: dict = Depends(require_admin)):
    rows = sb().table("slots").select("tattoo_photo").eq("id", slot_id).execute().data
    sb().table("slots").delete().eq("id", slot_id).execute()
    if rows:
        remove_image(rows[0].get("tattoo_photo"))
    return {"status": "ok"}


@app.post("/api/admin/cancel_booking")
def cancel_booking(slot_id: int = Form(...), _: dict = Depends(require_admin)):
    rows = sb().table("slots").select("tattoo_photo").eq("id", slot_id).execute().data
    sb().table("slots").update({"is_booked": False, "client_name": None, "client_tg": None,
                                "tattoo_size": None, "tattoo_photo": None}).eq("id", slot_id).execute()
    if rows:
        remove_image(rows[0].get("tattoo_photo"))
    return {"status": "ok"}


@app.post("/api/admin/add_work")
async def add_work(files: List[UploadFile] = File(...), _: dict = Depends(require_admin)):
    urls = [u for u in [await read_image("work", f) for f in files] if u]
    if not urls:
        raise HTTPException(400, "Файлы не выбраны")
    await asyncio.to_thread(lambda: sb().table("works").insert([{"image_url": u} for u in urls]).execute())
    return {"status": "ok", "count": len(urls)}


@app.post("/api/admin/delete_work")
def delete_work(work_id: int = Form(...), _: dict = Depends(require_admin)):
    rows = sb().table("works").select("image_url").eq("id", work_id).execute().data
    sb().table("works").delete().eq("id", work_id).execute()
    if rows:
        remove_image(rows[0].get("image_url"))
    return {"status": "ok"}
