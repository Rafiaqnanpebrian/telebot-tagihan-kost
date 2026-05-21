import os
import asyncio
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update,InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)

from payment import payment_handler,cancel_payment_handler
from bills import cek_tagihan,cek_history_tagihan

# === Load environment variables from .env (optional but recommended) ===
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN").strip()

# === Init Supabase client ===
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Helper functions for DB operations ===

def get_user_by_chat_id(chat_id: int) -> Optional[dict]:
    """Return user record dict if chat_id exists, else None."""
    try:
        res = supabase.table("users").select("*").eq("chat_id", chat_id).execute()
        # supabase-py returns an object with .data or dict with 'data'
        data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else None)
        if data and len(data) > 0:
            return data[0]
    except Exception as e:
        print("Error get_user_by_chat_id: %s", e)
    return None

def get_user_by_access_code(access_code: str) -> Optional[dict]:
    """Return user record dict if access_code exists, else None."""
    try:
        res = supabase.table("users").select("*").eq("access_code", access_code).execute()
        data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else None)
        if data and len(data) > 0:
            return data[0]
    except Exception as e:
        print("Error get_user_by_access_code: %s", e)
    return None

def update_user_chat_id(access_code: str, chat_id: int) -> bool:
    """Update chat_id for the user with given access_code. Return True if success."""
    try:
        res = supabase.table("users").update({"chat_id": chat_id}).eq("access_code", access_code).execute()
        # check affected rows
        data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else None)
        return bool(data)
    except Exception as e:
        print("Error update_user_chat_id: %s", e)
        return False

# === Telegram handlers ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    chat_id = update.effective_chat.id
    print("Received /start from chat_id=%s", chat_id)

    keyboard = [
        [
            InlineKeyboardButton("💰 TAGIHAN", callback_data="tagihan"),
            InlineKeyboardButton("📜 HISTORY TAGIHAN", callback_data="history_tagihan")
        ]
    ]
    start_markup = InlineKeyboardMarkup(keyboard)

    user = get_user_by_chat_id(chat_id)
    if user:
        # Already registered
        full_name = user.get("full_name") or update.effective_user.full_name
        await context.bot.send_photo(
                chat_id=chat_id,
                photo=open("/app/tagihin-kost/assets/logo.jpg", "rb"),
                caption=(
                    f"Selamat datang kembali di *Tagih.in*, {full_name} 🙌\n\n"
                    f"kamu sudah terdaftar di sistem dan siap menikmati kemudahan pembayaran kost\n"
                    f"Gunakan perintah berikut untuk mulai:"
                    ),
                reply_markup=start_markup,
                parse_mode="Markdown"
            )
        return

    # Not registered yet -> ask for access code
    await context.bot.send_photo(
                chat_id=chat_id,
                photo=open("/app/tagihin-kost/assets/logo.jpg", "rb"),
                caption=(
                    f"👋 Selamat datang di **Tagih.in!**\n\n"
                    f"📌 Tagih.in adalah bot yang membantu penghuni melakukan pembayaran secara praktis. Tidak perlu repot lagi, cukup gunakan bot ini untuk cek tagihan dan bayar langsung via QR Code.\n\n"
                    f"📝 Untuk registrasi, silakan masukkan *access code* yang diberikan oleh admin.Ketik kode tersebut di chat ini untuk mulai menggunakan layanan.\n"
                    ),
                parse_mode="Markdown"
            )

async def handle_text_as_access_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Generic text handler:
    - If user already registered (chat_id exists) -> ignore or show help.
    - Else treat incoming text as access_code and attempt registration.
    """
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    print("Text from chat_id=%s: %s", chat_id, text)

    # If user already registered, provide a short help message or treat as normal command
    existing = get_user_by_chat_id(chat_id)
    if existing:
        await update.message.reply_text(
            "Kamu sudah terdaftar. Silahkan ketik /start untuk memulai."
        )
        return

    # Treat text as access_code
    access_code = text
    user_record = get_user_by_access_code(access_code)
    if not user_record:
        await update.message.reply_text(
            "Access code tidak valid. Pastikan kamu memasukkan kode yang benar atau hubungi admin."
        )
        return

    # Update chat_id in DB
    success = update_user_chat_id(access_code, chat_id)
    if not success:
        await update.message.reply_text(
            "Terjadi kesalahan saat menyimpan data. Silakan coba lagi nanti atau hubungi admin."
        )
        return

    full_name = user_record.get("full_name") or "Penghuni"
    await update.message.reply_text(
        f"🎉 Registrasi Berhasil!\n"
        f"Selamat datang di Tagih.in, {full_name}.\n"
        f"Silahkan ketik /start untuk memulai"
    )

# async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     await update.message.reply_text(
#         "/start - Mulai/registrasi\n"
#         "/tagihan - Lihat tagihan kamu\n"
#         "/help - Bantuan"
#     )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # wajib untuk menghentikan "loading" di UI Telegram

    if query.data == "tagihan":
        # Panggil handler cek_tagihan
        await cek_tagihan(update, context)
    elif query.data == "history_tagihan":
        # Panggil handler cek_history_tagihan
        await cek_history_tagihan(update, context)
    elif query.data == "bayar":
        # Panggil handler payment
        await payment_handler(update, context)

def main():
    if BOT_TOKEN in (None, "", "your-telegram-bot-token"):
        print("BOT_TOKEN belum di-set. Set environment variable BOT_TOKEN atau isi di .env")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tagihan", cek_tagihan))
    app.add_handler(CommandHandler("history_tagihan", cek_history_tagihan))
    app.add_handler(CommandHandler("bayar", payment_handler))
    app.add_handler(CallbackQueryHandler(cancel_payment_handler, pattern="^cancel_payment:"))
    # app.add_handler(CommandHandler("help", help_command)) 
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_as_access_code))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot berjalan. Menjalankan polling...")
    # Run polling (blocking) — ini mengelola lifecycle internal Application dengan benar
    app.run_polling()

if __name__ == "__main__":
    main()
