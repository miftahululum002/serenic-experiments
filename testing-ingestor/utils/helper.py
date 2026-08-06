from datetime import datetime, timezone
from config import API_VERSION, API_HOST, API_KEY


def get_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_api_version():
    return f"v{API_VERSION}"


def get_api_host():
    return API_HOST


def get_api_key():
    return API_KEY
