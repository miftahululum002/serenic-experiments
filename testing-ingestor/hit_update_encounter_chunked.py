import argparse
import json
import time
from pathlib import Path

from utils.logger import get_logger
from utils.api_interface import api_update_encounter


log = get_logger("hit_update_encounter_chunked")

BATCH_SIZE = 5
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def load_updates_from_chunk(filepath: Path):
    with open(filepath) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("request_data", {}).get("updates", [])


def send_batch(updates: list):
    response = api_update_encounter(updates)
    return response


def process_chunk(filepath: Path, delay: float = 0):
    updates = load_updates_from_chunk(filepath)
    log.info("Loaded %d updates from %s", len(updates), filepath.name)

    batches = [updates[i : i + BATCH_SIZE] for i in range(0, len(updates), BATCH_SIZE)]

    for idx, batch in enumerate(batches, 1):
        log.info("Sending batch %d/%d", idx, len(batches))
        send_batch(batch)
        if delay > 0 and idx < len(batches):
            time.sleep(delay)


def process_all_chunks(delay: float = 0):
    files = sorted(OUTPUT_DIR.glob("update_encounters_chunk_*.json"))
    log.info("Found %d chunk files", len(files))

    for filepath in files:
        process_chunk(filepath, delay)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=str, help="Specific chunk file to process")
    parser.add_argument("--all", action="store_true", help="Process all chunk files")
    parser.add_argument(
        "--delay", type=float, default=0, help="Delay between batches (seconds)"
    )
    args = parser.parse_args()

    if args.file:
        process_chunk(Path(args.file), args.delay)
    elif args.all:
        process_all_chunks(args.delay)
    else:
        parser.print_help()
