from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from constant import (
    DEFAULT_LOGIN_PATH,
    DEFAULT_REFRESH_PATH,
    DEFAULT_SYNC_PATH,
    BATCH_DELAY_SECONDS,
    BATCH_SIZE,
)
from libraries.api_request import ApiError, SyncClient
from utils.utility import (
    get_organization,
    load_encounter_ids,
    save_payload,
    save_response,
    get_uuid,
    get_timestamp_file,
    get_folder_date,
    normalize_filename,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Login dan jalankan sinkronisasi organisasi Serenic."
    )
    result.add_argument(
        "--orgid", required=True, help="ID organisasi pada config/organization.csv"
    )
    result.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Path CSV yang berisi kolom encounter_id",
    )
    result.add_argument(
        "--batch",
        type=int,
        default=BATCH_SIZE,
        help=f"Jumlah encounter per batch (default: {BATCH_SIZE})",
    )
    result.add_argument(
        "--delay",
        type=float,
        default=BATCH_DELAY_SECONDS,
        help=f"Jeda antar-batch dalam detik (default: {BATCH_DELAY_SECONDS})",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        if args.batch <= 0:
            raise ValueError("--batch harus lebih besar dari 0")
        if args.delay < 0:
            raise ValueError("--delay tidak boleh negatif")
        org = get_organization(args.orgid)
        if not org.api_url:
            raise ValueError(f"api_url organisasi {args.orgid!r} kosong")
        encounter_ids = load_encounter_ids(args.file)
        client = SyncClient(
            org,
            login_path=DEFAULT_LOGIN_PATH,
            sync_path=DEFAULT_SYNC_PATH,
            refresh_path=DEFAULT_REFRESH_PATH,
            token_path="data.accessToken",
            refresh_token_path="data.token",
            refresh_payload_key="refreshToken",
        )
        logging.info("Login: %s", org.name)
        tokens = client.login()
        logging.info("Total encounter yang diproses: %s", len(encounter_ids))
        for index, encounter_id in enumerate(encounter_ids, start=1):
            logging.info(
                "%s/%s, encounterId=%s",
                index,
                len(encounter_ids),
                encounter_id,
            )
            request_payload = {"organizationId": org.id, "encounterId": encounter_id}
            request_uuid = get_uuid()
            timestamp = get_timestamp_file()  # datetime.now().strftime("%Y%m%d%H%M%S")
            date_folder = get_folder_date()  # datetime.now().strftime("%Y%m%d")
            filename_encounter_id = normalize_filename(encounter_id)
            filename = (
                Path(org.id)
                / date_folder
                / f"{filename_encounter_id}_{timestamp}_{request_uuid}.json"
            )
            payload_path = save_payload(filename, request_payload)
            response = client.synchronize(tokens, request_payload)
            tokens = client.current_tokens or tokens
            response_path = save_response(filename, response)
            logging.info("Payload tersimpan: %s", payload_path)
            logging.info("Response tersimpan: %s", response_path)
            if index % args.batch == 0 and index < len(encounter_ids):
                logging.info(
                    "Batch %s encounter selesai; menunggu %s detik",
                    args.batch,
                    args.delay,
                )
                time.sleep(args.delay)
        return 0
    except (OSError, ValueError, ApiError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
