"""Konfigurasi: memuat .env saat di-import dan menyediakan nilai config."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# Variabel yang sudah ada di environment tidak ditimpa (override=False).
load_dotenv(ENV_FILE)

DISTRICT_ID = os.environ.get("DISTRICT_ID") or None
