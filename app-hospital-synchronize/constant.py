import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

DEFAULT_LOGIN_PATH = "/app/v1/api/auth/login"
DEFAULT_SYNC_PATH = "/app/v1/api/synchronize/hospital/single"
DEFAULT_REFRESH_PATH = "/app/v1/api/auth/refresh-token"
PAYLOAD_DIR = Path("payload")
RESPONSE_DIR = Path("response")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
BATCH_DELAY_SECONDS = float(os.getenv("BATCH_DELAY_SECONDS", "5"))
