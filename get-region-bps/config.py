"""Konfigurasi: memuat .env saat di-import dan menyediakan nilai config."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def load_env(path: Path) -> None:
    """Loader .env sederhana; variabel yang sudah ada di environment tidak ditimpa."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env(ENV_FILE)

DISTRICT_ID = os.environ.get("DISTRICT_ID") or None
