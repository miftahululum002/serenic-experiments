import argparse
import json
from pathlib import Path

from utils.query import delete_encounters_by_noregistrasi
from utils.logger import get_logger
import sys

log = get_logger("delete_encounters")


def delete_encounters(filepath: str):
    fp = Path(filepath)
    log.info("Loading payload from %s", fp)
    with open(fp) as f:
        data = json.load(f)

    if isinstance(data, list):
        items = data
    else:
        items = data.get("request_data", {}).get("newEncounters", [])

    noregistrasi_list = [
        item.get("noregistrasi") for item in items if item.get("noregistrasi")
    ]
    log.info("Found %d noregistrasi", len(noregistrasi_list))
    deleted = delete_encounters_by_noregistrasi(noregistrasi_list)
    log.info("Deleted %d encounters", len(deleted))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file", type=str, required=True, help="Path to encounters JSON file"
    )
    args = parser.parse_args()
    delete_encounters(args.file)
