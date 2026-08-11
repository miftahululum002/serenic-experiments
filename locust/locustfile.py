import json
import os
import copy
import uuid
from pathlib import Path

from locust import HttpUser, task, between

from utils.api import API_KEY, API_HOST, API_VERSION
from utils.utility import get_datetime_now

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

UNIQUE_NOREC = os.getenv("UNIQUE_NOREC", "0") == "1"

HEADERS = {
    "apiKey": API_KEY,
    "Content-Type": "application/json",
}

_BASE_URL = f"/integrations/v{API_VERSION}"


def _load(name: str):
    with open(DATA_DIR / name) as f:
        return json.load(f)


NEW_ENCOUNTERS = _load("new_encounters.json")
UPDATE_ENCOUNTERS = _load("update_encounters.json")
PREREQUISITES = _load("prerequisites.json")
COMPLETED = _load("completed.json")


def _fresh(items: list) -> list:
    data = copy.deepcopy(items)
    if UNIQUE_NOREC:
        for item in data:
            item["norec"] = uuid.uuid4().hex
            if "noregistrasi" in item:
                item["noregistrasi"] = f"LT{uuid.uuid4().hex[:8].upper()}"
    return data


class IntegrationUser(HttpUser):
    host = API_HOST or ""
    wait_time = between(0.1, 0.5)

    @task(5)
    def health_check(self):
        with self.client.get(f"{_BASE_URL}/health_check", headers=HEADERS) as resp:
            pass

    @task(15)
    def prerequisites(self):
        payload = copy.deepcopy(PREREQUISITES)
        payload["timestamp"] = get_datetime_now()
        with self.client.post(
            f"{_BASE_URL}/prerequisites", json=payload, headers=HEADERS
        ) as resp:
            pass

    @task(35)
    def new_encounters(self):
        payload = {
            "timestamp": get_datetime_now(),
            "newEncounters": _fresh(NEW_ENCOUNTERS),
        }
        with self.client.post(
            f"{_BASE_URL}/encounters/new", json=payload, headers=HEADERS
        ) as resp:
            pass

    @task(35)
    def update_encounters(self):
        payload = {
            "start_timestamp": get_datetime_now(),
            "end_timestamp": get_datetime_now(),
            "updates": _fresh(UPDATE_ENCOUNTERS),
        }
        with self.client.post(
            f"{_BASE_URL}/encounters/update", json=payload, headers=HEADERS
        ) as resp:
            pass

    @task(10)
    def completed_encounters(self):
        payload = {
            "timestamp": get_datetime_now(),
            "dischargedEncounters": _fresh(COMPLETED),
        }
        with self.client.post(
            f"{_BASE_URL}/encounters/completed", json=payload, headers=HEADERS
        ) as resp:
            pass
