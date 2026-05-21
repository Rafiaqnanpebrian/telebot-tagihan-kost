import os
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update,InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from midtrans_qris import MidtransQRIS

midtrans = MidtransQRIS()

# === Load environment variables from .env (optional but recommended) ===
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# === Init Supabase client ===
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # 1. Ambil data penghuni dari Supabase
    user = supabase.table("users").select("*").eq("chat_id", chat_id).execute()
    if not user.data:
        await context.bot.send_message(chat_id=chat_id, text="Data penghuni tidak ditemukan. Registrasi dulu dengan access code.")
        return
    user_data = user.data[0]

    # 2. Ambil tagihan unpaid terbaru
    bill = (
        supabase.table("bills")
        .select("*")
        .eq("user_id", user_data["user_id"])
        .eq("bill_status", "unpaid")
        .order("due_date", desc=True)
        .limit(1)
        .execute()
    )
    if not bill.data:
        await context.bot.send_message(chat_id=chat_id, text="Tidak ada tagihan unpaid.")
        return
    bill_data = bill.data[0]

    # 3. Buat transaksi Midtrans
    order_id = midtrans.generate_order_id(chat_id)
    qris_result = midtrans.create_qris_payment(
        order_id=order_id,
        amount=bill_data["amount"],
        customer_name=user_data["full_name"]
    )

    keyboard = [
        [
            InlineKeyboardButton("❌ Batalkan Pembayaran",
            callback_data=f"cancel_payment:{order_id}")
        ]
    ]
    start_markup = InlineKeyboardMarkup(keyboard)

    if qris_result.get("success"):
        # Simpan transaksi ke Supabase
        supabase.table("transactions").insert({
            "bill_id": bill_data["bill_id"],
            "order_id": order_id,
            "transaction_id": qris_result["transaction_id"],
            "payment_url": qris_result["payment_url"],
            "qr_string": qris_result["qr_string"],
            "payment_status": qris_result["status"],
            "gross_amount": bill_data["amount"],
            "payment_method": qris_result.get("raw_response", {}).get("payment_type"),
            "transaction_time": qris_result.get("transaction_time") or qris_result.get("raw_response", {}).get("transaction_time")
        }).execute()

        # 4. Kirim QR code ke user
        qr_image_result = midtrans.generate_qr_image(
            qr_string=qris_result["qr_string"],
            customer_name=user_data["full_name"],
            amount=bill_data["amount"],
            order_id=order_id
        )

        if qr_image_result["success"]:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=qr_image_result["image_bytes"],
                caption=(
                    f"🏠 **TAGIHAN KOST**\n\n"
                    f"Halo {user_data['full_name']}, ini tagihan kost Anda:\n\n"
                    f"📅 Tanggal: {bill_data['bill_date']}\n"
                    f"💰 Jumlah: Rp{bill_data['amount']:,}\n"
                    f"🔢 Order ID: {order_id}\n\n"
                    f"💳 **PEMBAYARAN QRIS**\n"
                    f"Scan QR Code di atas atau klik link:\n"
                    f"🔗 {qris_result['payment_url']}\n\n"
                ),
                reply_markup=start_markup,
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(chat_id=chat_id, text="QRIS berhasil dibuat, tapi gagal generate QR image.")
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Gagal membuat pembayaran QRIS: {qris_result.get('message')}")

async def cancel_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    if not data.startswith("cancel_payment:"):
        return

    order_id = data.split("cancel_payment:")[1]

    # Ambil data transaksi berdasarkan order_id
    trx_response = (
        supabase.table("transactions")
        .select("*")
        .eq("order_id", order_id)
        .limit(1)
        .execute()
    )

    if not trx_response.data:
        await query.edit_message_caption(
            caption="❌ Transaksi tidak ditemukan atau sudah tidak valid."
        )
        return

    transaction = trx_response.data[0]

    # Cegah cancel transaksi yang sudah paid/settlement
    current_status = transaction.get("transaction_status")

    if current_status in ["settlement", "capture", "paid", "success"]:
        await query.edit_message_caption(
            caption="✅ Pembayaran ini sudah berhasil, sehingga tidak dapat dibatalkan."
        )
        return

    if current_status in ["cancel", "cancelled", "expire", "expired"]:
        await query.edit_message_caption(
            caption="⚠️ Transaksi ini sudah dibatalkan atau sudah kedaluwarsa."
        )
        return

    # Panggil Midtrans cancel_payment
    midtrans = MidtransQRIS()
    cancel_result = midtrans.cancel_payment(order_id)

    # Sesuaikan pengecekan ini dengan return cancel_payment di midtrans_qris.py Anda
    if not cancel_result.get("success"):
        await query.edit_message_caption(
            caption=(
                "❌ Gagal membatalkan pembayaran.\n\n"
                f"Order ID: {order_id}\n"
                f"Pesan: {cancel_result.get('message', 'Tidak diketahui')}"
            )
        )
        return

    # Update status transaksi
    supabase.table("transactions").update({
        "payment_status": "cancelled"
    }).eq("order_id", order_id).execute()

    # Update status tagihan kembali menjadi unpaid
    bill_id = transaction.get("bill_id")

    if bill_id:
        supabase.table("bills").update({
            "bill_status": "unpaid"
        }).eq("bill_id", bill_id).execute()

    await query.message.delete()

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"❌ Pembayaran berhasil dibatalkan.\n\n"
            f"Order ID: {order_id}\n"
            f"Status tagihan dikembalikan menjadi belum dibayar.\n"
        )
    )
