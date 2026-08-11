import copy
import csv
import itertools
import json
import random
from pathlib import Path

from locust import HttpUser, task, between

from utils.api import API_HOST, API_KEY, API_VERSION
from utils.utility import get_datetime_now

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# Update encounter memakai norec/noregistrasi sebagai id yang harus sudah ada
# di database. Daftar norec yang dipakai diambil dari data/norec_pool.csv
# (sumber kebenaran); payload-nya diambil dari update_encounters_chunks/ (map
# norec -> payload update). Pool diputar bergantian supaya tiap request memakai
# data beda & selalu membuat job baru (hindari dedup server).

_PAYLOAD_MAP = {}
_CHUNKS_DIR = DATA_DIR / "update_encounters_chunks"
if _CHUNKS_DIR.exists():
    for fp in sorted(_CHUNKS_DIR.glob("part_*.json")):
        with open(fp) as f:
            _PAYLOAD_MAP.update(json.load(f))
elif (DATA_DIR / "update_encounters.json").exists():
    with open(DATA_DIR / "update_encounters.json") as f:
        _PAYLOAD_MAP = json.load(f)

# Filter hanya norec yang ada di pool CSV (sumber kebenaran)
_POOL_FILE = DATA_DIR / "norec_pool.csv"
if _POOL_FILE.exists():
    pool_keys = {
        row["norec"].strip()
        for row in csv.DictReader(open(_POOL_FILE))
        if row.get("norec") and row["norec"].strip()
    }
    _PAYLOAD_MAP = {k: v for k, v in _PAYLOAD_MAP.items() if k in pool_keys}

_POOL_CYCLE = None
if _PAYLOAD_MAP:
    keys = list(_PAYLOAD_MAP.keys())
    random.shuffle(keys)
    _POOL_CYCLE = itertools.cycle(keys)

HEADERS = {
    "apiKey": API_KEY,
    "Content-Type": "application/json",
}

_BASE_URL = f"/integrations/v{API_VERSION}"
_ENDPOINT = f"{_BASE_URL}/encounters/update"


def _fresh() -> list:
    if _POOL_CYCLE is None:
        raise RuntimeError("update_encounters_chunks kosong / tidak ditemukan")
    norec = next(_POOL_CYCLE)
    item = copy.deepcopy(_PAYLOAD_MAP[norec])
    item["norec"] = norec
    item["noregistrasi"] = item.get("noregistrasi") or norec
    return [item]


class UpdateEncounterUser(HttpUser):
    host = API_HOST or ""
    wait_time = between(0.1, 0.5)

    @task
    def update_encounters(self):
        payload = {
            "start_timestamp": get_datetime_now(),
            "end_timestamp": get_datetime_now(),
            "force_ingest_completed": "true",
            "updates": _fresh(),
        }
        self.client.post(_ENDPOINT, json=payload, headers=HEADERS)
