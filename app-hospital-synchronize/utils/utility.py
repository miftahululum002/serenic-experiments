from __future__ import annotations

import csv
import json
from pathlib import Path
import uuid
from datetime import datetime

from constant import PAYLOAD_DIR, RESPONSE_DIR
from libraries.api_request import Organization


REQUIRED_ORGANIZATION_COLUMNS = {
    "id",
    "name",
    "email",
    "password",
    "api_url",
    "sync_endpoint",
    "rule",
}
ORGANIZATIONS_FILE = Path("config/organization.csv")


def load_organizations(path: Path) -> list[Organization]:
    """Read organization credentials and API hosts from a CSV file."""
    result: list[Organization] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        for line_number, row in enumerate(rows, start=2):
            missing = REQUIRED_ORGANIZATION_COLUMNS - set(row)
            if missing:
                raise ValueError(
                    f"Kolom CSV kurang pada baris {line_number}: {', '.join(sorted(missing))}"
                )
            if not any(row.values()):
                continue
            sync_endpoint = row["sync_endpoint"].strip()
            rule = row["rule"].strip().lower()
            if not sync_endpoint:
                raise ValueError(f"sync_endpoint organisasi kosong pada baris {line_number}")
            if rule not in {"new", "legacy"}:
                raise ValueError(
                    f"rule organisasi pada baris {line_number} harus 'new' atau 'legacy'"
                )
            result.append(
                Organization(
                    id=row["id"].strip(),
                    name=row["name"].strip(),
                    email=row["email"].strip(),
                    password=row["password"].strip(),
                    api_url=row["api_url"].strip(),
                    sync_endpoint=sync_endpoint,
                    rule=rule,
                )
            )
    return result


def get_organization(organization_id: str) -> Organization:
    """Return the organization identified by --orgid."""
    for organization in load_organizations(ORGANIZATIONS_FILE):
        if organization.id == organization_id:
            return organization
    raise ValueError(f"Organisasi dengan id {organization_id!r} tidak ditemukan")


def load_encounter_ids(path: Path) -> list[str]:
    """Read encounter IDs from a CSV file."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        column = (
            "encounter_id"
            if rows.fieldnames and "encounter_id" in rows.fieldnames
            else "encounterId"
        )
        if not rows.fieldnames or column not in rows.fieldnames:
            raise ValueError(f"{path} harus memiliki kolom encounter_id")
        encounter_ids = [
            row[column].strip() for row in rows if row.get(column, "").strip()
        ]
    if not encounter_ids:
        raise ValueError(f"Tidak ada encounterId pada {path}")
    return encounter_ids


def normalize_filename(value: str) -> str:
    """Remove path separators before using a value as part of a filename."""
    return value.replace("/", "")


def save_json(path: Path, value: object) -> None:
    """Create parent directories and save a JSON value to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def save_payload(
    filename: str | Path, payload: object
) -> Path:
    """Save a synchronization payload and return its path."""
    path = PAYLOAD_DIR / filename
    save_json(path, payload)
    return path


def save_response(
    filename: str | Path, response: object
) -> Path:
    """Save a synchronization response and return its path."""
    path = RESPONSE_DIR / filename
    save_json(path, response)
    return path


def get_uuid():
    return uuid.uuid4()


def get_timestamp_file():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_folder_date():
    return datetime.now().strftime("%Y%m%d")
