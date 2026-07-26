import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
CHUNK_SIZE = 20


def split_updates():
    input_file = OUTPUT_DIR / "update_encounters_matched.json"
    with open(input_file) as f:
        updates = json.load(f)

    chunks = [updates[i:i + CHUNK_SIZE] for i in range(0, len(updates), CHUNK_SIZE)]

    for idx, chunk in enumerate(chunks, 1):
        output_file = OUTPUT_DIR / f"update_encounters_chunk_{idx:03d}.json"
        with open(output_file, "w") as f:
            json.dump(chunk, f, indent=2)
        print(f"Chunk {idx}: {len(chunk)} items -> {output_file.name}")

    print(f"\nTotal chunks: {len(chunks)}")


if __name__ == "__main__":
    split_updates()
