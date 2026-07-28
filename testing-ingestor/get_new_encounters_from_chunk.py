import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def get_noregistrasi_from_chunk(chunk_file: Path):
    with open(chunk_file) as f:
        updates = json.load(f)
    return {u["noregistrasi"] for u in updates if u.get("noregistrasi")}


def filter_new_encounters(noregistrasi_set: set):
    new_file = OUTPUT_DIR / "new_encounters_dedup.json"
    with open(new_file) as f:
        encounters = json.load(f)
    return [enc for enc in encounters if enc.get("noregistrasi") in noregistrasi_set]


def save_to_file(encounters: list, chunk_file: Path):
    output_file = OUTPUT_DIR / f"new_encounters_{chunk_file.stem}.json"
    with open(output_file, "w") as f:
        json.dump(encounters, f, indent=2)
    print(f"Saved {len(encounters)} new encounters to {output_file.name}")


if __name__ == "__main__":
    chunk_file = OUTPUT_DIR / "update_encounters_chunk_001.json"
    noregistrasi = get_noregistrasi_from_chunk(chunk_file)
    print(f"Found {len(noregistrasi)} noregistrasi from chunk")

    encounters = filter_new_encounters(noregistrasi)
    print(f"Matched {len(encounters)} new encounters")

    save_to_file(encounters, chunk_file)
