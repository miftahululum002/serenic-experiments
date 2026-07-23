import json
from datetime import datetime
from pathlib import Path

import requests

from config import API_KEY, ORGANIZATION_ID, ENDPOINT_FILENAMES
from utils.logger import get_logger

log = get_logger("api_request")

BASE_DIR = Path(__file__).parent.parent
PAYLOAD_DIR = BASE_DIR / "payload"
RESPONSE_DIR = BASE_DIR / "response"

HEADERS = {
    "apiKey": API_KEY,
    "Content-Type": "application/json",
}


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _resolve_filename(url: str) -> str:
    for path_key, filename in ENDPOINT_FILENAMES.items():
        if path_key in url:
            return filename
    return url.rstrip("/").rsplit("/", 1)[-1]


def _save_payload(url: str, payload: dict | None) -> Path | None:
    if payload is None:
        return None
    date_dir = PAYLOAD_DIR / ORGANIZATION_ID / _today()
    date_dir.mkdir(parents=True, exist_ok=True)
    filepath = date_dir / f"{_resolve_filename(url)}_{_timestamp()}.json"
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)
    log.info("Payload saved: %s", filepath)
    return filepath


def _save_response(url: str, response: requests.Response) -> Path:
    date_dir = RESPONSE_DIR / ORGANIZATION_ID / _today()
    date_dir.mkdir(parents=True, exist_ok=True)
    filepath = date_dir / f"{_resolve_filename(url)}_{_timestamp()}.json"
    try:
        body = response.json()
    except ValueError:
        body = response.text
    with open(filepath, "w") as f:
        json.dump(body, f, indent=2)
    log.info("Response saved: %s", filepath)
    return filepath


def get(url: str, **kwargs) -> requests.Response:
    log.info("GET %s", url)
    response = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
    log.info("Status: %s", response.status_code)
    _save_response(url, response)
    return response


def post(url: str, payload: dict | None = None, **kwargs) -> requests.Response:
    log.info("POST %s", url)
    _save_payload(url, payload)
    response = requests.post(url, headers=HEADERS, json=payload, timeout=30, **kwargs)
    log.info("Status: %s", response.status_code)
    _save_response(url, response)
    return response
