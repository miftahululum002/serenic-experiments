"""Helper untuk fetch wilayah dari BPS serta login dan POST desa ke endpoint villages."""

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
PAYLOAD_DIR = BASE_DIR / "payload"
RESPONSE_DIR = BASE_DIR / "response"
REQUIRED_ENV = ("ACCOUNT_USERNAME", "ACCOUNT_PASSWORD", "API_BASE_URL", "API_AUTH", "API_VILLAGE")
BPS_URL = "https://sig.bps.go.id/rest-bridging/getwilayah"
WILAYAH_URL = "https://wilayah.cahyadsn.com/api/"
DETAIL_DIR = BASE_DIR / "village_detail"


def log(message: str = "") -> None:
    """Print dengan timestamp di depan; langsung di-flush supaya tampil real-time."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def build_url(level: str, parent: str, periode_merge: str) -> str:
    query = urlencode({"level": level, "parent": parent, "periode_merge": periode_merge})
    return f"{BPS_URL}?{query}"


def build_filename(level: str, parent: str, periode_merge: str) -> str:
    name = f"{level}_{parent}_{periode_merge}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{name}_{timestamp}.json".replace("/", "-")


def fetch(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


def save(filename: str, body: bytes) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    try:
        data = json.loads(body)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        path = path.with_suffix(".txt")
        path.write_bytes(body)
    return path


def fetch_villages(parent: str, level: str, periode_merge: str) -> list[dict]:
    url = build_url(level, parent, periode_merge)
    log(f"GET {url}")
    try:
        status, body = fetch(url)
    except HTTPError as e:
        log(f"  HTTP error {e.code}: {e.reason}")
        return []
    except URLError as e:
        log(f"  Gagal koneksi: {e.reason}")
        return []

    path = save(build_filename(level, parent, periode_merge), body)
    log(f"  status {status}, tersimpan di {path}")

    try:
        villages = json.loads(body)
    except json.JSONDecodeError:
        log("  Response bukan JSON, dilewati")
        return []
    if not isinstance(villages, list) or not villages:
        log("  Response kosong, dilewati")
        return []
    log(f"  {len(villages)} desa ditemukan")
    return villages


def request_json(method: str, url: str, payload: dict | None = None, token: str | None = None) -> tuple[int, object, dict]:
    """Kirim request JSON. Mengembalikan (status, body, headers)."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, body, resp_headers = resp.status, resp.read(), dict(resp.headers)
    except HTTPError as e:
        status, body, resp_headers = e.code, e.read(), dict(e.headers or {})
    try:
        return status, json.loads(body), resp_headers
    except json.JSONDecodeError:
        return status, body.decode(errors="replace"), resp_headers


def retry_after_seconds(headers: dict) -> float | None:
    """Baca header Retry-After (dalam detik) kalau ada."""
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def request_with_retry(
    method: str, url: str, payload: dict | None, token: str, max_retries: int = 5, retry_delay: float = 10
) -> tuple[int | None, object]:
    """Kirim request dan coba ulang saat kena rate limit (429).

    Waktu tunggu mengikuti header Retry-After kalau ada; kalau tidak, backoff
    retry_delay x 2^n (10, 20, 40, ... detik).
    """
    for attempt in range(max_retries + 1):
        try:
            status, body, headers = request_json(method, url, payload, token)
        except URLError as e:
            return None, f"gagal koneksi: {e.reason}"
        if status != 429 or attempt == max_retries:
            return status, body
        wait = retry_after_seconds(headers) or retry_delay * 2**attempt
        log(
            f"    429 rate limit untuk {method} {url}, tunggu {wait:g} detik lalu coba lagi "
            f"(percobaan {attempt + 2}/{max_retries + 1})..."
        )
        time.sleep(wait)
    return status, body


def extract_token(body: object) -> str | None:
    """Ambil token dari response login: {"code", "message", "data": {"access_token", ...}}."""
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        return None
    return body["data"].get("access_token") or None


def login(base_url: str) -> str:
    url = base_url + os.environ["API_AUTH"]
    payload = {
        "username": os.environ["ACCOUNT_USERNAME"],
        "password": os.environ["ACCOUNT_PASSWORD"],
    }
    status, body, _ = request_json("POST", url, payload)
    token = extract_token(body)
    if status >= 400 or not token:
        raise SystemExit(f"Login gagal (status {status}): {body}")
    log(f"Login berhasil sebagai {payload['username']}")
    return token


def build_payload(village: dict, district_id: str | None) -> dict:
    code = village["kode_bps"]
    return {
        "name": village["nama_bps"].title(),
        "district_id": district_id or code[:7],
        "code": code,
        "code_dagri": village.get("kode_dagri"),
        "latitude": None,
        "longitude": None,
    }


def write_json(path: Path, data: object) -> Path:
    """Simpan data sebagai JSON; kalau response bukan JSON, simpan apa adanya sebagai .txt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path = path.with_suffix(".txt")
        path.write_text(data)
    else:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def post_villages(
    payloads: list[dict], max_requests: int = 0, delay: float = 0, max_retries: int = 5, retry_delay: float = 10
) -> int:
    """Login lalu POST setiap payload satu per satu.

    Dalam satu district_id, setiap sudah `max_requests` request diberi jeda `delay` detik
    sebelum lanjut. Saat pindah district_id hitungan direset dan langsung lanjut tanpa jeda.
    `max_requests` 0 berarti tanpa batas (tanpa jeda).

    Request yang kena rate limit (429) dicoba ulang maksimal `max_retries` kali
    (lihat request_with_retry).

    Response 409 (data sudah ada) dicatat sebagai SKIP, tidak dihitung gagal.
    Mengembalikan jumlah yang gagal.
    """
    missing = [key for key in REQUIRED_ENV if not os.environ.get(key)]
    if missing:
        raise SystemExit(f"Env belum diisi: {', '.join(missing)}")

    base_url = os.environ["API_BASE_URL"].rstrip("/")
    token = login(base_url)
    village_url = base_url + os.environ["API_VILLAGE"]

    created = existing = failed = 0
    sent_in_district = 0
    for i, payload in enumerate(payloads):
        if i > 0 and payloads[i - 1]["district_id"] != payload["district_id"]:
            sent_in_district = 0
        if max_requests and delay and sent_in_district and sent_in_district % max_requests == 0:
            log(
                f"    sudah {sent_in_district} request di district {payload['district_id']}, "
                f"jeda {delay:g} detik sebelum mengirim {payload['code']} {payload['name']} ({i + 1}/{len(payloads)})..."
            )
            time.sleep(delay)
        sent_in_district += 1
        now = datetime.now()
        filename = Path(now.strftime("%Y%m%d")) / f"{payload['code']}_{now.strftime('%H%M%S')}.json"
        write_json(PAYLOAD_DIR / filename, payload)
        status, body = request_with_retry("POST", village_url, payload, token, max_retries, retry_delay)
        write_json(RESPONSE_DIR / filename, body)
        label = f"{payload['code']} {payload['name']} -> {status}"
        if status is not None and status < 400:
            created += 1
            log(f"[OK] {label}")
        elif status == 409:
            # Data desa sudah ada di API, bukan masalah
            existing += 1
            log(f"[SKIP] {label} (sudah ada)")
        else:
            failed += 1
            log(f"[FAIL] {label}")
            log(f"    {body}")

    print()
    log(f"Selesai: {created} berhasil, {existing} sudah ada, {failed} gagal")
    return failed


def to_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def build_detail_url(kode: str) -> str:
    return f"{WILAYAH_URL}?{urlencode({'kode': kode, 'detail': 1})}"


def fetch_village_detail(kode: str) -> dict | None:
    """GET detail satu desa dari wilayah.cahyadsn.com (pakai kode_dagri) dan simpan response-nya.

    Mengembalikan data desa ({kode, nama, kodepos, lat, lng, ...}) atau None.
    """
    url = build_detail_url(kode)
    log(f"GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except HTTPError as e:
        log(f"  HTTP error {e.code}: {e.reason}")
        return None
    except URLError as e:
        log(f"  Gagal koneksi: {e.reason}")
        return None

    path = DETAIL_DIR / datetime.now().strftime("%Y%m%d") / f"{kode}.json"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        write_json(path, body.decode(errors="replace"))
        log("  Response bukan JSON, dilewati")
        return None

    path = write_json(path, data)
    items = data.get("data") if isinstance(data, dict) else None
    if not items:
        log(f"  Kode {kode} tidak ditemukan, tersimpan di {path}")
        return None

    village = items[0]
    log(f"  {village.get('nama')} | kodepos {village.get('kodepos')} | lat {village.get('lat')}, lng {village.get('lng')}")
    log(f"  tersimpan di {path}")
    return village


def build_update_payload(detail: dict) -> dict:
    """Payload PATCH village: hanya postal_code, latitude, longitude dari wilayah.cahyadsn.com."""
    return {
        "postal_code": detail.get("kodepos") or None,
        "latitude": to_float(detail.get("lat")),
        "longitude": to_float(detail.get("lng")),
    }


def get_village(village_url: str, token: str, village_id: str, max_retries: int = 5, retry_delay: float = 10) -> dict | None:
    """GET API_VILLAGE/{village_id}. Mengembalikan data village atau None."""
    status, body = request_with_retry("GET", f"{village_url}/{village_id}", None, token, max_retries, retry_delay)
    data = body.get("data") if isinstance(body, dict) else None
    if status is None or status >= 400 or not isinstance(data, dict):
        log(f"[FAIL] GET village {village_id} -> {status}")
        log(f"    {body}")
        return None
    return data


def update_villages(
    village_ids: list[str],
    delay: float = 0,
    detail_delay: float = 1,
    max_retries: int = 5,
    retry_delay: float = 10,
    dry_run: bool = False,
) -> int:
    """Update postal_code, latitude, longitude setiap village berdasarkan village_id.

    Per village: GET API_VILLAGE/{id} (ambil code_dagri) -> detail wilayah.cahyadsn.com
    -> PATCH API_VILLAGE/{id}. Payload dan response PATCH disimpan ke payload/ dan
    response/ dengan prefix update_. Dengan dry_run, PATCH tidak dikirim.
    Mengembalikan jumlah yang gagal.
    """
    missing = [key for key in REQUIRED_ENV if not os.environ.get(key)]
    if missing:
        raise SystemExit(f"Env belum diisi: {', '.join(missing)}")

    base_url = os.environ["API_BASE_URL"].rstrip("/")
    token = login(base_url)
    village_url = base_url + os.environ["API_VILLAGE"]

    updated = failed = 0
    for i, village_id in enumerate(village_ids):
        if i and delay:
            log(f"    jeda {delay:g} detik sebelum village {village_id} ({i + 1}/{len(village_ids)})...")
            time.sleep(delay)
        print()
        log(f"== [{i + 1}/{len(village_ids)}] village_id={village_id}")

        village = get_village(village_url, token, village_id, max_retries, retry_delay)
        if not village:
            failed += 1
            continue
        code_dagri = village.get("code_dagri")
        if not code_dagri:
            log(f"[FAIL] {village_id} {village.get('name')} tidak punya code_dagri")
            failed += 1
            continue

        if i and detail_delay:
            time.sleep(detail_delay)
        detail = fetch_village_detail(code_dagri)
        if not detail:
            log(f"[FAIL] {village_id} {village.get('name')}: detail {code_dagri} tidak ditemukan")
            failed += 1
            continue

        payload = build_update_payload(detail)
        label = f"{village_id} {village.get('name')}"
        if dry_run:
            log(f"[DRY-RUN] PATCH {label}: {json.dumps(payload)}")
            updated += 1
            continue

        now = datetime.now()
        filename = Path(now.strftime("%Y%m%d")) / f"update_{village_id}_{now.strftime('%H%M%S')}.json"
        write_json(PAYLOAD_DIR / filename, payload)
        status, body = request_with_retry("PATCH", f"{village_url}/{village_id}", payload, token, max_retries, retry_delay)
        write_json(RESPONSE_DIR / filename, body)
        if status is not None and status < 400:
            updated += 1
            log(f"[OK] PATCH {label} -> {status} (kodepos {payload['postal_code']}, lat {payload['latitude']}, lng {payload['longitude']})")
        else:
            failed += 1
            log(f"[FAIL] PATCH {label} -> {status}")
            log(f"    {body}")

    print()
    log(f"Selesai update: {updated} berhasil{' (dry-run)' if dry_run else ''}, {failed} gagal")
    return failed
