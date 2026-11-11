import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

DEFAULT_LOGIN_PATH = "/app/v1/api/auth/login"
DEFAULT_SYNC_PATH = "/app/v1/api/synchronize/hospital/single"
DEFAULT_REFRESH_PATH = "/app/v1/api/auth/refresh-token"
PAYLOAD_DIR = Path("payload")
RESPONSE_DIR = Path("response")
RUNTIME_DIR = Path("runtime")
SAVE_PAYLOAD = os.getenv("SAVE_PAYLOAD", "false").strip().lower() in {"1", "true", "yes", "on"}
SAVE_RESPONSE = os.getenv("SAVE_RESPONSE", "false").strip().lower() in {"1", "true", "yes", "on"}
BATCH_SIZE =int(os.getenv("BATCH_SIZE", "20"))
BATCH_DELAY_SECONDS = float(os.getenv("BATCH_DELAY_SECONDS", "5"))
