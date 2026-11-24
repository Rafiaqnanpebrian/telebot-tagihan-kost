import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from supabase import create_client

TIMEZONE = ZoneInfo("Asia/Jakarta")
MAX_SEND_ATTEMPTS = 3
REMINDER_TYPES = {
    3: "h_minus_3",
    0: "due_date",
    -1: "h_plus_1",
}

logger = logging.getLogger(__name__)


def reminder_type_for_due_date(due_date: date, today: date) -> Optional[str]:
    days_until_due = (due_date - today).days
    return REMINDER_TYPES.get(days_until_due)


def parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def format_rupiah(amount: object) -> str:
    try:
        return f"Rp{int(float(amount)):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return f"Rp{amount}"


def build_reminder_message(bill: dict, user: dict, reminder_type: str) -> str:
    due_date = parse_date(bill["due_date"])
    messages = {
        "h_minus_3": "Jatuh tempo tagihan Anda 3 hari lagi.",
        "due_date": "Tagihan Anda jatuh tempo hari ini.",
        "h_plus_1": "Tagihan Anda sudah melewati jatuh tempo 1 hari.",
    }
    full_name = user.get("full_name") or "Penghuni"
    return (
        f"🔔 Pengingat Tagihan Kost\n\n"
        f"Halo {full_name}, {messages[reminder_type]}\n"
        f"📅 Jatuh tempo: {due_date.isoformat()}\n"
        f"💰 Jumlah: {format_rupiah(bill.get('amount'))}\n\n"
        f"Silakan cek dan bayar tagihan melalui bot dengan perintah /tagihan."
    )


def send_telegram_message(
    bot_token: str,
    chat_id: int,
    message: str,
) -> Tuple[bool, Optional[str], bool, Optional[float]]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message},
            timeout=30,
        )
    except requests.RequestException as error:
        return False, f"network error ({type(error).__name__})", True, None

    try:
        result = response.json()
    except ValueError:
        return (
            False,
            f"Telegram returned a non-JSON response (HTTP {response.status_code})",
            response.status_code >= 500,
            None,
        )

    if response.ok and isinstance(result, dict) and result.get("ok"):
        return True, None, False, None

    description = (
        result.get("description", "Telegram returned ok=false")
        if isinstance(result, dict)
        else "Unexpected Telegram response"
    )
    retryable = response.status_code == 429 or response.status_code >= 500
    retry_after = None
    if response.status_code == 429 and isinstance(result, dict):
        parameters = result.get("parameters") or {}
        retry_after_value = parameters.get("retry_after")
        if retry_after_value is not None:
            try:
                retry_after = min(float(retry_after_value), 60.0)
            except (TypeError, ValueError):
                retry_after = None

    return (
        False,
        f"HTTP {response.status_code}: {description}",
        retryable,
        retry_after,
    )


def update_reminder_log(
    supabase,
    bill_id: str,
    reminder_type: str,
    updates: dict,
) -> None:
    response = (
        supabase.table("bill_reminder_logs")
        .update(updates)
        .eq("bill_id", bill_id)
        .eq("reminder_type", reminder_type)
        .execute()
    )
    if not response.data:
        raise RuntimeError(
            f"Could not update reminder log for bill_id={bill_id}, "
            f"reminder_type={reminder_type}."
        )


def run_reminders(supabase, bot_token: str, today: date, dry_run: bool) -> None:
    due_dates = [
        (today + timedelta(days=days)).isoformat()
        for days in REMINDER_TYPES
    ]
    bills_response = (
        supabase.table("bills")
        .select("bill_id,user_id,due_date,amount")
        .eq("bill_status", "unpaid")
        .in_("due_date", due_dates)
        .execute()
    )
    bills = bills_response.data or []

    candidates = []
    for bill in bills:
        reminder_type = reminder_type_for_due_date(parse_date(bill["due_date"]), today)
        if reminder_type:
            candidates.append((bill, reminder_type))

    if not candidates:
        logger.info("No unpaid bills need reminders today.")
        return

    user_ids = list({bill["user_id"] for bill, _ in candidates if bill.get("user_id") is not None})
    users_response = (
        supabase.table("users")
        .select("user_id,full_name,chat_id")
        .in_("user_id", user_ids)
        .execute()
    ) if user_ids else None
    users = {
        user["user_id"]: user
        for user in (users_response.data if users_response else [])
    }

    if dry_run:
        logger.info(
            "Dry run: found %d reminder candidate(s); no messages sent and no logs written.",
            len(candidates),
        )
        return

    bill_ids = list({str(bill["bill_id"]) for bill, _ in candidates})
    existing_response = (
        supabase.table("bill_reminder_logs")
        .select("bill_id,reminder_type,status,attempt_count")
        .in_("bill_id", bill_ids)
        .execute()
    )
    existing_logs = {
        (row["bill_id"], row["reminder_type"]): row
        for row in (existing_response.data or [])
    }

    new_logs = [
        {
            "bill_id": str(bill["bill_id"]),
            "reminder_type": reminder_type,
            "status": "pending",
        }
        for bill, reminder_type in candidates
        if (str(bill["bill_id"]), reminder_type) not in existing_logs
    ]
    if new_logs:
        supabase.table("bill_reminder_logs").insert(new_logs).execute()
        for row in new_logs:
            existing_logs[(row["bill_id"], row["reminder_type"])] = {
                **row,
                "attempt_count": 0,
            }

    sent_count = 0
    skipped_count = 0
    failed_count = 0

    for bill, reminder_type in candidates:
        bill_id = str(bill["bill_id"])
        log_entry = existing_logs[(bill_id, reminder_type)]
        if log_entry["status"] == "sent":
            skipped_count += 1
            continue

        attempt_count = int(log_entry.get("attempt_count") or 0)
        if attempt_count >= MAX_SEND_ATTEMPTS:
            logger.warning(
                "Reminder attempts exhausted: bill_id=%s reminder_type=%s",
                bill_id,
                reminder_type,
            )
            skipped_count += 1
            continue

        user = users.get(bill.get("user_id"))
        chat_id = user.get("chat_id") if user else None
        if not chat_id:
            update_reminder_log(
                supabase,
                bill_id,
                reminder_type,
                {
                    "status": "failed",
                    "last_error": "Tenant has no Telegram chat_id.",
                },
            )
            logger.warning(
                "Reminder skipped because tenant has no chat_id: bill_id=%s",
                bill_id,
            )
            skipped_count += 1
            continue

        message = build_reminder_message(bill, user, reminder_type)
        sent = False
        last_error = None

        while attempt_count < MAX_SEND_ATTEMPTS:
            attempt_count += 1
            update_reminder_log(
                supabase,
                bill_id,
                reminder_type,
                {
                    "status": "sending",
                    "attempt_count": attempt_count,
                    "last_attempt_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": None,
                },
            )

            sent, last_error, retryable, retry_after = send_telegram_message(
                bot_token,
                chat_id,
                message,
            )
            if sent:
                update_reminder_log(
                    supabase,
                    bill_id,
                    reminder_type,
                    {
                        "status": "sent",
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                        "last_error": None,
                    },
                )
                logger.info(
                    "Bill reminder sent: bill_id=%s reminder_type=%s chat_id=%s",
                    bill_id,
                    reminder_type,
                    chat_id,
                )
                sent_count += 1
                break

            update_reminder_log(
                supabase,
                bill_id,
                reminder_type,
                {"status": "failed", "last_error": (last_error or "Unknown error")[:500]},
            )
            logger.error(
                "Bill reminder send failed: bill_id=%s reminder_type=%s "
                "attempt=%d error=%s",
                bill_id,
                reminder_type,
                attempt_count,
                last_error,
            )
            if not retryable or attempt_count >= MAX_SEND_ATTEMPTS:
                break
            time.sleep(retry_after or min(2 ** (attempt_count - 1), 8))

        if not sent:
            failed_count += 1

    logger.info(
        "Bill reminder job completed: candidates=%d sent=%d failed=%d skipped=%d",
        len(candidates),
        sent_count,
        failed_count,
        skipped_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Send scheduled tenant bill reminders.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List whether reminders are due without sending messages or writing logs.",
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().with_name(".env"))
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_KEY", "").strip()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not supabase_url or not service_role_key or not bot_token:
        raise RuntimeError(
            "SUPABASE_URL, SUPABASE_KEY, and BOT_TOKEN "
            "must be configured."
        )

    supabase = create_client(supabase_url, service_role_key)
    today = datetime.now(TIMEZONE).date()
    logger.info("Starting bill reminder job for %s (Asia/Jakarta).", today)
    run_reminders(supabase, bot_token, today, args.dry_run)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    main()
