import copy
import json
import os
import uuid
from pathlib import Path

from locust import HttpUser, task, between

from utils.api import API_HOST, API_KEY, API_VERSION
from utils.utility import get_datetime_now

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# Update encounter memakai norec/noregistrasi sebagai id. Default acak supaya
# setiap request dianggap data baru dan selalu menciptakan job parsing baru
# (hindari dedup server yang membuat job tidak masuk queue).
UNIQUE_NOREC = os.getenv("UNIQUE_NOREC", "1") == "1"

HEADERS = {
    "apiKey": API_KEY,
    "Content-Type": "application/json",
}

_BASE_URL = f"/integrations/v{API_VERSION}"
_ENDPOINT = f"{_BASE_URL}/encounters/update"

with open(DATA_DIR / "update_encounters.json") as f:
    UPDATE_ENCOUNTERS = json.load(f)


def _fresh(items: list) -> list:
    data = copy.deepcopy(items)
    if UNIQUE_NOREC:
        for item in data:
            item["norec"] = uuid.uuid4().hex
            if "noregistrasi" in item:
                item["noregistrasi"] = f"LT{uuid.uuid4().hex[:8].upper()}"
    return data


class UpdateEncounterUser(HttpUser):
    host = API_HOST or ""
    wait_time = between(0.1, 0.5)

    @task
    def update_encounters(self):
        payload = {
            "start_timestamp": get_datetime_now(),
            "end_timestamp": get_datetime_now(),
            "updates": _fresh(UPDATE_ENCOUNTERS),
        }
        with self.client.post(_ENDPOINT, json=payload, headers=HEADERS) as resp:
            pass
