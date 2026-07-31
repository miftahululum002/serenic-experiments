import argparse
import csv
import os
from datetime import datetime
from constant import TARGET_ORG


def get_org_id() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgid", default=TARGET_ORG)
    args, _ = parser.parse_known_args()
    return args.orgid


def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_now():
    return datetime.now()


def get_timestamp_file() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_csv(path: str, data: list[dict], headers: list[str]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)
    return path


def save_to_csv_data_parsing(jobs: list[dict], org_id: str):
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    date = now.strftime("%Y%m%d")
    dirpath = f"results/{org_id}/{date}"
    filename = f"{dirpath}/integration_queue_{ts}.csv"
    return write_csv(filename, jobs, ["id", "start_timestamp", "created_at"])
