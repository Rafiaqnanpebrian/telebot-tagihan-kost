import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client


def main() -> None:
    load_dotenv(Path(__file__).resolve().with_name(".env"))

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_role_key = os.getenv("SUPABASE_KEY", "").strip()
    if not supabase_url or not service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be configured."
        )

    supabase = create_client(supabase_url, service_role_key)
    response = supabase.rpc("generate_monthly_bills").execute()

    if not isinstance(response.data, int):
        raise RuntimeError(
            f"Unexpected response from generate_monthly_bills: {response.data!r}"
        )

    logging.info("Monthly bill job completed; inserted %d bill(s).", response.data)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    main()
