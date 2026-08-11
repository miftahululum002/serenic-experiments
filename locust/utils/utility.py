import csv
from datetime import datetime
from pathlib import Path
from config import ENDPOINT_FILENAMES
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
ORGANIZATION_CSV = BASE_DIR / "organization.csv"


def load_organizations() -> list[dict]:
    with open(ORGANIZATION_CSV) as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["version"] = int(row["version"])
        row["is_active"] = int(row["is_active"])
    return rows


def get_active_organizations() -> list[dict]:
    return [org for org in load_organizations() if org["is_active"]]


def get_organization(
    org_id: str | None = None,
    name: str | None = None,
) -> dict | None:
    organizations = load_organizations()
    for org in organizations:
        if org_id and org["id"] == org_id:
            return org
        if name and org["name"].lower() == name.lower():
            return org
    return None


def get_today() -> str:
    return datetime.now().strftime("%Y%m%d")


def get_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_payload_directory() -> str:
    return BASE_DIR / "payload"


def get_response_directory() -> str:
    return BASE_DIR / "response"


def get_filename(url: str) -> str:
    for path_key, filename in ENDPOINT_FILENAMES.items():
        if path_key in url:
            return filename
    return url.rstrip("/").rsplit("/", 1)[-1]


def get_datetime_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
