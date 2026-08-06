import argparse
import json
from pathlib import Path

from utils.logger import get_logger
from utils.api_interface import api_new_encounter


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
    response = api_new_encounter(new_encounters)
    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file", type=str, required=True, help="Path to new encounters JSON file"
    )
    args = parser.parse_args()
    hit_new_encounter(args.file)
