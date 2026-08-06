import argparse
import json
from pathlib import Path

from utils.logger import get_logger
from utils.api_interface import api_update_encounter

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
    response = api_update_encounter(updates)
    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file", type=str, required=True, help="Path to update encounters JSON file"
    )
    args = parser.parse_args()
    hit_update_encounter(args.file)
