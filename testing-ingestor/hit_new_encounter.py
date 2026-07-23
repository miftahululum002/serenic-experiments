import argparse
import json
from pathlib import Path

from config import API_HOST
from utils.api_request import post
from utils.helper import get_timestamp
from utils.logger import get_logger

log = get_logger("hit_new_encounter")


def hit_new_encounter(filepath: str):
    fp = Path(filepath)
    log.info("Loading payload from %s", fp)
    with open(fp) as f:
        data = json.load(f)

    if isinstance(data, list):
        new_encounters = data
    else:
        new_encounters = data.get("request_data", {}).get("newEncounters", [])

    log.info("Found %d new encounters", len(new_encounters))

    url = f"{API_HOST}/integrations/v2/encounters/new"
    payload = {
        "timestamp": get_timestamp(),
        "newEncounters": new_encounters,
    }

    log.info("Posting new encounter to %s", url)
    response = post(url, payload=payload)

    if response.status_code == 200:
        log.info("New encounter posted successfully")
    else:
        log.warning("New encounter failed with status %s", response.status_code)

    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, required=True, help="Path to new encounters JSON file")
    args = parser.parse_args()
    hit_new_encounter(args.file)
