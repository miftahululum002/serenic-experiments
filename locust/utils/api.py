import json
from pathlib import Path

import requests
from utils.utility import (
    get_today,
    get_timestamp,
    get_payload_directory,
    get_response_directory,
    get_filename,
    get_organization,
)

from config import ORGANIZATION_ID

_ORGANIZATION = get_organization(org_id=ORGANIZATION_ID)
if not _ORGANIZATION:
    raise ValueError(
        f"Organisasi {ORGANIZATION_ID} tidak ditemukan di organization.csv. "
        "Periksa nilai ORGANIZATION_ID di .env (ambilkan dari organization.csv)."
    )

ORG_ID = _ORGANIZATION["id"]
API_HOST = _ORGANIZATION["api_url"]
API_KEY = _ORGANIZATION["api_key"]
API_VERSION = str(_ORGANIZATION["version"])

HEADERS = {
    "apiKey": API_KEY,
    "Content-Type": "application/json",
}


def _save_payload(url: str, payload: dict | None) -> Path | None:
    if payload is None:
        return None
    date_dir = get_payload_directory() / ORG_ID / get_today()
    date_dir.mkdir(parents=True, exist_ok=True)
    filepath = date_dir / f"{get_filename(url)}_{get_timestamp()}.json"
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)
    return filepath


def _save_response(url: str, response: requests.Response) -> Path:
    date_dir = get_response_directory() / ORG_ID / get_today()
    date_dir.mkdir(parents=True, exist_ok=True)
    filepath = date_dir / f"{get_filename(url)}_{get_timestamp()}.json"
    try:
        body = response.json()
    except ValueError:
        body = response.text
    with open(filepath, "w") as f:
        json.dump(body, f, indent=2)
    return filepath


def get(url: str, **kwargs) -> requests.Response:
    final_url = f"{API_HOST}/{url}"
    response = requests.get(final_url, headers=HEADERS, timeout=30, **kwargs)
    _save_response(url, response)
    return response


def post(url: str, payload: dict | None = None, **kwargs) -> requests.Response:
    final_url = f"{API_HOST}/{url}"
    _save_payload(url, payload)
    response = requests.post(
        final_url, headers=HEADERS, json=payload, timeout=30, **kwargs
    )
    _save_response(url, response)
    return response
