import argparse
import json
from pathlib import Path

from config import API_HOST
from utils.api_request import post
from utils.helper import get_timestamp
from utils.logger import get_logger

log = get_logger("hit_update_encounter")


def hit_update_encounter(filepath: str):
    fp = Path(filepath)
    log.info("Loading payload from %s", fp)
    with open(fp) as f:
        data = json.load(f)

    if isinstance(data, list):
        updates = data
    else:
        updates = data.get("request_data", {}).get("updates", [])

    log.info("Found %d updates", len(updates))

    url = f"{API_HOST}/integrations/v2/encounters/update"
    now = get_timestamp()
    payload = {
        "start_timestamp": now,
        "end_timestamp": now,
        "updates": updates,
    }

    log.info("Posting update encounter to %s", url)
    response = post(url, payload=payload)

    if response.status_code == 200:
        log.info("Update encounter posted successfully")
    else:
        log.warning("Update encounter failed with status %s", response.status_code)

    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, required=True, help="Path to update encounters JSON file")
    args = parser.parse_args()
    hit_update_encounter(args.file)
