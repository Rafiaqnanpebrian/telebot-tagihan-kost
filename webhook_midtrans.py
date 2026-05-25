import os
import hashlib
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY", "").strip()


supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()


def verify_midtrans_signature(order_id, status_code, gross_amount, signature_key):
    raw_signature = f"{order_id}{status_code}{gross_amount}{MIDTRANS_SERVER_KEY}"
    expected_signature = hashlib.sha512(raw_signature.encode()).hexdigest()
    return expected_signature == signature_key


def send_telegram_message(chat_id, message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        return response.json()
    except Exception as e:
        print(f"Gagal kirim pesan Telegram: {e}")
        return None

def delete_telegram_message(chat_id, message_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"

    payload = {
        "chat_id": chat_id,
        "message_id": message_id
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        result = response.json()
        print("Delete Telegram message result:", result)
        return result
    except Exception as e:
        print(f"Gagal menghapus pesan Telegram: {e}")
        return None


def format_rupiah(amount):
    try:
        return f"Rp{int(float(amount)):,.0f}".replace(",", ".")
    except Exception:
        return f"Rp{amount}"


@app.post("/midtrans/notification")
async def midtrans_notification(request: Request):
    payload = await request.json()

    print("Midtrans notification received:", payload)

    order_id = payload.get("order_id")
    transaction_status = payload.get("transaction_status")
    fraud_status = payload.get("fraud_status")
    status_code = payload.get("status_code")
    gross_amount = payload.get("gross_amount")
    signature_key = payload.get("signature_key")
    payment_type = payload.get("payment_type")
    settlement_time = payload.get("settlement_time")
    transaction_time = payload.get("transaction_time")

    if not order_id:
        raise HTTPException(status_code=400, detail="order_id tidak ditemukan")

    if not signature_key:
        raise HTTPException(status_code=400, detail="signature_key tidak ditemukan")

    is_valid = verify_midtrans_signature(
        order_id=order_id,
        status_code=status_code,
        gross_amount=gross_amount,
        signature_key=signature_key
    )

    if not is_valid:
        raise HTTPException(status_code=403, detail="Signature Midtrans tidak valid")

    # Ambil transaksi
    trx_response = (
        supabase.table("transactions")
        .select("*")
        .eq("order_id", order_id)
        .limit(1)
        .execute()
    )

    if not trx_response.data:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    transaction = trx_response.data[0]
    bill_id = transaction.get("bill_id")

    current_payment_status = transaction.get("payment_status")
    terminal_statuses = ["settlement", "expire", "cancel", "deny"]

    # Jika transaksi sudah final, abaikan notifikasi duplikat atau pending yang datang belakangan
    if current_payment_status in terminal_statuses:
        print(
            f"Duplicate/late notification ignored. "
            f"order_id={order_id}, current_status={current_payment_status}, incoming_status={transaction_status}"
        )

        return {
            "status": "ignored",
            "message": "Transaction already finalized",
            "order_id": order_id,
            "current_payment_status": current_payment_status,
            "incoming_transaction_status": transaction_status
        }   

    # Ambil tagihan
    bill_response = (
        supabase.table("bills")
        .select("*")
        .eq("bill_id", bill_id)
        .limit(1)
        .execute()
    )

    if not bill_response.data:
        raise HTTPException(status_code=404, detail="Tagihan tidak ditemukan")

    bill = bill_response.data[0]
    user_id = bill.get("user_id")

    # Ambil user
    user_response = (
        supabase.table("users")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    if not user_response.data:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    user = user_response.data[0]

    chat_id = user.get("chat_id")
    full_name = user.get("full_name", "Penghuni")

    # Mapping status Midtrans ke status sistem
    new_payment_status = transaction_status
    new_bill_status = bill.get("bill_status")

    if transaction_status == "settlement":
        new_payment_status = "settlement"
        new_bill_status = "paid"

    elif transaction_status == "capture":
        if fraud_status == "accept":
            new_payment_status = "settlement"
            new_bill_status = "paid"
        else:
            new_payment_status = "challenge"
            new_bill_status = "pending"

    elif transaction_status == "pending":
        new_payment_status = "pending"
        new_bill_status = "pending"

    elif transaction_status == "expire":
        new_payment_status = "expire"
        new_bill_status = "unpaid"

    elif transaction_status == "cancel":
        new_payment_status = "cancel"
        new_bill_status = "unpaid"

    elif transaction_status == "deny":
        new_payment_status = "deny"
        new_bill_status = "unpaid"

    # Update transactions sesuai schema Anda
    trx_update_data = {
        "payment_status": new_payment_status,
        "payment_method": payment_type,
        "gross_amount": gross_amount
    }

    if transaction_time:
        trx_update_data["transaction_time"] = transaction_time

    if settlement_time:
        trx_update_data["settlement_time"] = settlement_time

    supabase.table("transactions").update(trx_update_data).eq("order_id", order_id).execute()

    # Update bills sesuai schema Anda
    supabase.table("bills").update({
        "bill_status": new_bill_status
    }).eq("bill_id", bill_id).execute()

    # Kirim notifikasi Telegram
    bill_date = bill.get("bill_date", "-")
    amount = bill.get("amount", gross_amount)

    # Hapus pesan QRIS jika transaksi berhasil atau expired
    telegram_chat_id = transaction.get("telegram_chat_id")
    telegram_message_id = transaction.get("telegram_message_id")

    if new_payment_status in ["settlement", "expire"] and telegram_chat_id and telegram_message_id:
        delete_telegram_message(telegram_chat_id, telegram_message_id)

    if chat_id:
        if new_bill_status == "paid":
            message = (
                f"✅ *Pembayaran Berhasil*\n\n"
                f"Halo {full_name}, pembayaran tagihan kost Anda berhasil dikonfirmasi.\n\n"
                f"📅 Tanggal Tagihan: {bill_date}\n"
                f"💰 Jumlah: {format_rupiah(amount)}\n"
                f"🔢 Order ID: `{order_id}`\n"
                f"💳 Metode: {payment_type}\n\n"
                f"Status tagihan Anda telah diperbarui menjadi *Lunas*."
            )
            send_telegram_message(chat_id, message)

        elif new_payment_status == "expire":
            message = (
                f"⌛ *Pembayaran Kedaluwarsa*\n\n"
                f"Halo {full_name}, transaksi pembayaran tagihan kost Anda telah kedaluwarsa.\n\n"
                f"📅 Tanggal Tagihan: {bill_date}\n"
                f"💰 Jumlah: {format_rupiah(amount)}\n"
                f"🔢 Order ID: `{order_id}`\n\n"
                f"Status tagihan dikembalikan menjadi *Belum Dibayar*.\n"
                f"Gunakan /bayar untuk membuat QRIS baru."
            )
            send_telegram_message(chat_id, message)

        elif new_payment_status in ["cancel", "deny"]:
            message = (
                f"❌ *Pembayaran Tidak Berhasil*\n\n"
                f"Halo {full_name}, transaksi pembayaran Anda tidak berhasil.\n\n"
                f"🔢 Order ID: `{order_id}`\n"
                f"Status transaksi: *{new_payment_status}*\n\n"
                f"Status tagihan dikembalikan menjadi *Belum Dibayar*.\n"
                f"Gunakan /bayar untuk membuat QRIS baru."
            )
            send_telegram_message(chat_id, message)

    return {
        "status": "success",
        "message": "Notification processed",
        "order_id": order_id,
        "payment_status": new_payment_status,
        "bill_status": new_bill_status
    }