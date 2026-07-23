import argparse
import json
from pathlib import Path

from config import API_HOST
from utils.api_request import post
from utils.helper import get_timestamp
from utils.logger import get_logger

log = get_logger("hit_prerequisites")

BASE_DIR = Path(__file__).parent
DEFAULT_PAYLOAD = BASE_DIR / "data" / "generated-prerequisites.json"


def hit_prerequisites(filepath: str | None = None):
    fp = Path(filepath) if filepath else DEFAULT_PAYLOAD
    log.info("Loading payload from %s", fp)
    with open(fp) as f:
        payload = json.load(f)

    url = f"{API_HOST}/integrations/v2/prerequisites"
    payload["timestamp"] = get_timestamp()

    log.info("Posting prerequisites to %s", url)
    response = post(url, payload=payload)

    if response.status_code == 200:
        log.info("Prerequisites posted successfully")
    else:
        log.warning("Prerequisites failed with status %s", response.status_code)

    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Path to custom prerequisites JSON file")
    args = parser.parse_args()
    hit_prerequisites(args.file)
