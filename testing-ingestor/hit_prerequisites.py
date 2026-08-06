import argparse
import json
from pathlib import Path

from utils.logger import get_logger
from utils.api_interface import api_prerequisites


log = get_logger("hit_prerequisites")

BASE_DIR = Path(__file__).parent
DEFAULT_PAYLOAD = BASE_DIR / "data" / "generated-prerequisites.json"


def hit_prerequisites(filepath: str | None = None):
    fp = Path(filepath) if filepath else DEFAULT_PAYLOAD
    log.info("Loading payload from %s", fp)
    with open(fp) as f:
        payload = json.load(f)

    response = api_prerequisites(payload)
    return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Path to custom prerequisites JSON file")
    args = parser.parse_args()
    hit_prerequisites(args.file)
