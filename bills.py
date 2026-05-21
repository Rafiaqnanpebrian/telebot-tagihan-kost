import os
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update,InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from payment import payment_handler

# === Load environment variables from .env (optional but recommended) ===
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

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

def get_bills_by_user_id(user_id: int) -> dict | None:
    """Return top 1 unpaid bill for a user_id, ordered by due_date desc."""
    try:
        res = (
            supabase.table("bills")
            .select("*")
            .eq("user_id", user_id)
            .eq("bill_status", "unpaid")
            .order("due_date", desc=True)   # gunakan keyword desc
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else None) or []
        return data[0] if data else None
    except Exception as e:
        print("Error get_bills_by_user_id:", e)
        return None
    
def get_history_bills_by_user_id(user_id: int) -> list:
    """Return list of bills for a user_id, sorted by due_date ascending (Python-side)."""
    try:
        res = supabase.table("bills").select("*").eq("user_id", user_id).execute()
        data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else None) or []
        # Normalisasi dan sort by due_date (as string). Jika pakai ISO date, sort string sudah benar.
        sorted_data = sorted(data, key=lambda b: b.get("due_date") or "")
        return sorted_data
    except Exception as e:
        print("Error get_history_bills_by_user_id: %s", e)
        return []

# === Telegram handlers ===

async def cek_tagihan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = get_user_by_chat_id(chat_id)

    keyboard = [
        [
            InlineKeyboardButton("💵 BAYAR TAGIHAN", callback_data="bayar"),
        ]
    ]
    start_markup = InlineKeyboardMarkup(keyboard)

    if not user:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Kamu belum terdaftar. Silakan registrasi dulu."
            )
        return

    bill = get_bills_by_user_id(user["user_id"])
    if bill:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Tagihan terbaru:\nTanggal: {bill['bill_date']}\nJumlah: Rp{bill['amount']:,}\nStatus: {bill['bill_status']}\nJatuh tempo: {bill['due_date']}",
            reply_markup=start_markup,
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Tidak ada tagihan unpaid."
            )


async def cek_history_tagihan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /tagihan command — menampilkan daftar tagihan user."""
    chat_id = update.effective_chat.id
    print("Cek tagihan request from chat_id=%s", chat_id)

    user = get_user_by_chat_id(chat_id)
    if not user:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Kamu belum terdaftar. Silakan registrasi dulu dengan access code (kirim /start untuk memulai)."
        )
        return

    user_id = user.get("user_id")
    bills = get_history_bills_by_user_id(user_id)
    if not bills:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Tidak ada tagihan aktif untukmu saat ini. 🎉"
            )
        return

    # Build message
    lines = ["📑 *Daftar Tagihan Kamu:*"]
    for b in bills:
        bill_date = b.get("bill_date") or b.get("created_at") or "—"
        amount = b.get("amount") or 0
        status = b.get("bill_status") or "unpaid"
        due = b.get("due_date") or "—"
        lines.append(f"- Tanggal: {bill_date} | Jumlah: Rp{amount:,} | Status: {status} | Jatuh tempo: {due}")

    msg = "\n".join(lines)
    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode="Markdown")