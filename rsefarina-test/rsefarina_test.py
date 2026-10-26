#!/usr/bin/env python3
"""Testing API RSEFARINA (POST /api/serenic) per resource & rentang tanggal.

Contoh:
    python3 rsefarina_test.py                       # pakai registrasi.csv
    python3 rsefarina_test.py data/lain.csv
    python3 rsefarina_test.py --mode variants
    python3 rsefarina_test.py --mode variants --limit 10 --offset 20
    python3 rsefarina_test.py --resources asesmen_awal resume_medis
    python3 rsefarina_test.py --start 2026-10-17 --end 2026-10-27

File registrasi: CSV dengan kolom noregistrasi (opsional admission_type,
period_start, period_end).
Output satu folder per run, output/<YYYYMMDD>/<HHMMSS>/: raw JSON, summary.json,
key_consistency.txt, dan laporan.md (konsistensi & temuan, report.py).
Hanya memakai standard library (tanpa pip install).
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from report import Result, build_report, is_grouped, key_consistency, type_name

ENDPOINT = "/api/serenic"

RESOURCES = [
    "triase_igd",
    "diagnosis_aktif",
    "diagnosis_awal",
    "cppt",
    "asesmen_awal",
    "resume_medis",
    "penunjang_lab",
    "laporan_operasi",
    "penunjang_radiologi",
    "obat",
    "billing",
]

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 520, 522, 524}

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_env(path):
    """Parser .env sederhana: KEY=VALUE, mengabaikan komentar & tanda kutip."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


REGISTRATION_COLUMNS = ("noregistrasi", "no_registrasi", "registration_no")


def read_registrations(path):
    """Return (list no registrasi, {no: metadata baris CSV}).

    CSV dengan kolom `noregistrasi`; kolom lain (mis. admission_type,
    period_start, period_end) disimpan sebagai metadata. Urutan dipertahankan,
    duplikat dibuang.
    """
    path = Path(path)
    metadata = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = {c.strip().lower(): c for c in reader.fieldnames or []}
        column = next((columns[c] for c in REGISTRATION_COLUMNS if c in columns), None)
        if column is None:
            sys.exit(
                f"Kolom no registrasi tidak ditemukan di {path} "
                f"(salah satu dari: {', '.join(REGISTRATION_COLUMNS)})"
            )
        for row in reader:
            no_reg = (row.get(column) or "").strip()
            if no_reg and not no_reg.startswith("#"):
                metadata.setdefault(
                    no_reg,
                    {k.strip(): (v or "").strip() for k, v in row.items() if k and k != column},
                )
    return list(metadata), metadata


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status, body, message):
        super().__init__(message)
        self.status = status
        self.body = body


def post_source(base_url, api_key, resource, no_reg, start, end, timeout, retries):
    body = json.dumps(
        {
            "resource": resource,
            "registration_no": no_reg,
            "date_start": start or "",
            "date_end": end or "",
        }
    ).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "AccessKey": api_key},
    )

    last_error = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode() or "null")
                return payload, response.status, time.monotonic() - started
        except urllib.error.HTTPError as error:
            text = error.read().decode(errors="replace")
            last_error = ApiError(error.code, text, f"HTTP {error.code}: {text[:200]}")
            if error.code not in RETRYABLE_STATUS:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = ApiError(None, None, f"{type(error).__name__}: {error}")
        except json.JSONDecodeError as error:
            last_error = ApiError(None, None, f"Response bukan JSON: {error}")
            break
        if attempt < retries:
            time.sleep(min(2 ** attempt, 10))
    raise last_error


# ---------------------------------------------------------------------------
# Rentang tanggal
# ---------------------------------------------------------------------------


def registration_date(no_reg):
    """REG/IP/261017-0014 -> 2026-10-17 (YYMMDD setelah '/' terakhir)."""
    match = re.search(r"/(\d{2})(\d{2})(\d{2})-\d+$", no_reg)
    if not match:
        return None
    yy, mm, dd = (int(part) for part in match.groups())
    try:
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None


def parse_date(text):
    """'2026-10-15 02:03:00.000' / '2026-10-15' -> date, atau None."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def encounter_period(no_reg, meta):
    """(period_start, period_end) sebagai date. period_start dari CSV, atau dari
    no registrasi; period_end dari CSV, None kalau belum pulang/tidak ada."""
    meta = meta or {}
    start = parse_date(meta.get("period_start")) or registration_date(no_reg)
    end = parse_date(meta.get("period_end"))
    return start, end


def build_ranges(args, no_reg, meta=None):
    """Daftar (label, date_start, date_end) per registrasi.

    single  : --start/--end kalau diisi; selain itu period_start..period_end
              (period_end kosong -> besok), tanpa rentang kalau periode tidak ada.
    variants: variasi di sekitar period_start..period_end, plus rentang yang
              dipakai cron (penuh & incremental) sebagai pembanding.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)
    period_start, period_end = encounter_period(no_reg, meta)
    end_or_tomorrow = period_end or tomorrow
    iso = lambda d: d.isoformat() if d else ""

    if args.mode == "single":
        if args.start or args.end:
            return [("custom", args.start, args.end)]
        if period_start:
            return [("periode", iso(period_start), iso(end_or_tomorrow))]
        return [("tanpa_rentang", "", "")]

    ranges = [("tanpa_rentang", "", "")]
    if period_start:
        one = timedelta(days=1)
        ranges += [
            ("periode", iso(period_start), iso(end_or_tomorrow)),
            ("start..", iso(period_start), ""),
            ("start..end+1", iso(period_start), iso(end_or_tomorrow + one)),
            ("start-1..end+1", iso(period_start - one), iso(end_or_tomorrow + one)),
            # rentang penuh cron: period_start - 1 .. besok
            ("cron_penuh", iso(period_start - one), iso(tomorrow)),
        ]
    # rentang incremental cron (checkpoint ~ hari ini): hari ini - 2 .. besok
    ranges.append(("cron_incremental", iso(today - timedelta(days=2)), iso(tomorrow)))
    return ranges


# ---------------------------------------------------------------------------
# Analisis response
# ---------------------------------------------------------------------------


def summarize(payload, resource):
    """Ringkasan isi: '5 item' / '1e soap:4 obat:18' / '[]' / '(kosong)'."""
    if not isinstance(payload, dict):
        return f"response bukan object ({type_name(payload)})"
    if resource not in payload:
        return f"key '{resource}' tidak ada (keys: {','.join(payload)})"
    entries = payload[resource]
    if not isinstance(entries, list):
        return f"'{resource}' bukan array ({type_name(entries)})"
    if not entries:
        return "[]"
    if not is_grouped(entries):
        return f"{len(entries)} item"
    totals = {}
    for entry in entries:
        for key, value in entry.items():
            if isinstance(value, list) and value:
                totals[key] = totals.get(key, 0) + len(value)
    sections = " ".join(f"{k}:{v}" for k, v in totals.items()) or "(kosong)"
    return f"{len(entries)}e {sections}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def save_response(run_dir, task, payload, status, error):
    """<registrasi>/<label>/<resource>.json = response API apa adanya; kalau
    gagal, <resource>.error.json berisi request, status & body error."""
    no_reg, resource, label, start, end = task
    target = run_dir / safe_name(no_reg) / safe_name(label)
    target.mkdir(parents=True, exist_ok=True)
    if error is None:
        (target / f"{resource}.json").write_text(
            json.dumps(payload, indent=1, ensure_ascii=False)
        )
        return
    body = error.body
    try:
        body = json.loads(body) if body else body
    except json.JSONDecodeError:
        pass
    (target / f"{resource}.error.json").write_text(
        json.dumps(
            {
                "request": {
                    "endpoint": ENDPOINT,
                    "resource": resource,
                    "registration_no": no_reg,
                    "date_start": start or "",
                    "date_end": end or "",
                },
                "status": status,
                "error": str(error),
                "response": body,
            },
            indent=1,
            ensure_ascii=False,
        )
    )


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "kosong"


def main():
    parser = argparse.ArgumentParser(
        description="Testing API RSEFARINA per resource & rentang tanggal."
    )
    parser.add_argument(
        "file", nargs="?", default=str(BASE_DIR / "registrasi.csv"),
        help="CSV dengan kolom noregistrasi (default: registrasi.csv)",
    )
    parser.add_argument(
        "--resources", nargs="+", choices=RESOURCES, default=RESOURCES,
        help="resource yang dites (default: semua)",
    )
    parser.add_argument(
        "--mode", choices=["single", "variants"], default="single",
        help="single: period_start..period_end dari CSV (atau --start/--end); "
             "variants: tanpa rentang, periode, start.., start..end+1, "
             "start-1..end+1, cron_penuh, cron_incremental",
    )
    parser.add_argument("--start", default="", help="date_start (YYYY-MM-DD)")
    parser.add_argument("--end", default="", help="date_end (YYYY-MM-DD)")
    parser.add_argument("--env", default=str(BASE_DIR / ".env"), help="path .env")
    parser.add_argument(
        "--out", default=str(BASE_DIR / "output"),
        help="folder output (raw JSON, summary & laporan)",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--no-raw", action="store_true",
        help="jangan simpan response API (default: disimpan per registrasi/rentang/resource)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="jumlah registrasi yang dites (default: semua)",
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="lewati N registrasi pertama di CSV (default: 0)",
    )
    args = parser.parse_args()

    load_env(Path(args.env))
    base_url = os.environ.get("RSEFARINA_API_BASE_URL", "")
    api_key = os.environ.get("RSEFARINA_API_KEY", "")
    if not base_url or not api_key:
        sys.exit(
            f"RSEFARINA_API_BASE_URL / RSEFARINA_API_KEY belum diisi ({args.env})"
        )

    registrations, metadata = read_registrations(args.file)
    total_in_file = len(registrations)
    end = None if args.limit is None else args.offset + max(0, args.limit)
    registrations = registrations[max(0, args.offset):end]
    if not registrations:
        sys.exit(
            f"Tidak ada no registrasi di {args.file} "
            f"(total {total_in_file}, offset {args.offset}, limit {args.limit})"
        )

    # Satu folder per run: output/<YYYYMMDD>/<HHMMSS>/ berisi raw JSON & laporan.
    now = datetime.now()
    run_dir = Path(args.out) / now.strftime("%Y%m%d") / now.strftime("%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        (no_reg, resource, label, start, end)
        for no_reg in registrations
        for (label, start, end) in build_ranges(args, no_reg, metadata.get(no_reg))
        for resource in args.resources
    ]
    print(
        f"{len(registrations)} dari {total_in_file} registrasi "
        f"(baris {args.offset + 1}-{args.offset + len(registrations)}), "
        f"{len(args.resources)} resource, "
        f"{len(tasks)} request -> {run_dir}\n"
    )

    def run(task):
        no_reg, resource, label, start, end = task
        try:
            payload, status, elapsed = post_source(
                base_url, api_key, resource, no_reg, start, end,
                args.timeout, args.retries,
            )
            return task, payload, status, elapsed, None
        except ApiError as error:
            return task, None, error.status, None, error

    rows = []
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for task, payload, status, elapsed, error in pool.map(run, tasks):
            no_reg, resource, label, start, end = task
            range_text = f"{start or '-'}..{end or '-'}"
            results.append(Result(
                registrasi=no_reg, resource=resource, label=label,
                rentang=range_text, payload=payload,
                error=str(error) if error else None, status=status,
            ))
            result = f"ERROR {error}" if error else summarize(payload, resource)
            if not args.no_raw:
                save_response(run_dir, task, payload, status, error)
            rows.append(
                {
                    "registrasi": no_reg,
                    "resource": resource,
                    "label": label,
                    "rentang": range_text,
                    "status": status,
                    "detik": round(elapsed, 2) if elapsed is not None else None,
                    "hasil": result,
                }
            )
            print(
                f"{no_reg} | {resource:<19} | {label:<15} | {range_text:<23} | "
                f"{result}"
            )

    report_text, findings = build_report(
        results, registrations, args.resources, args.mode,
        now.strftime("%Y-%m-%d %H:%M:%S"), metadata,
    )
    shape_lines = [
        f"{status:<2} {name} ({samples}){': ' + note if note else ''}"
        for status, name, samples, note in key_consistency(results)
    ]

    errors = [row for row in rows if row["hasil"].startswith("ERROR")]
    (run_dir / "summary.json").write_text(
        json.dumps(rows, indent=1, ensure_ascii=False)
    )
    (run_dir / "key_consistency.txt").write_text("\n".join(shape_lines) + "\n")
    report_path = run_dir / "laporan.md"
    report_path.write_text(report_text)

    level_counts = {
        level: sum(1 for f in findings if f.level == level)
        for level in ("error", "warning", "info")
    }
    print(
        f"\nSelesai: {len(rows)} request, {len(errors)} request error. "
        f"Temuan: {level_counts['error']} error, {level_counts['warning']} warning, "
        f"{level_counts['info']} info.\nLaporan: {report_path}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
