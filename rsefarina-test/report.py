"""Laporan konsistensi & temuan dari satu run rsefarina_test.py.

Input: hasil run berupa list Result (registrasi, resource, label rentang,
payload/error). Output: teks Markdown.

Cek dibagi dua:
- Konsistensi key: keyset & tipe per section, digabung dari semua registrasi.
- Temuan: aturan yang mendeteksi pola yang pernah bikin error / tidak sesuai
  format Serenic (lihat notes/rsefarina-*.md di cron-api-scheduler).
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

BASE_LABELS = ("tanpa_rentang", "periode", "custom")

# Pola performer yang dipakai mayoritas source (mis. MD-00227).
PERFORMER_PATTERN = re.compile(r"^[A-Z]{2,}-\d+$")
PERFORMER_KEYS = ("performer_id", "requester_id", "result_interpreter_id")

VALUE_TYPE_PYTHON = {
    "string": (str,),
    "integer": (int,),
    "decimal": (int, float),
    "quantity": (int, float),
    "boolean": (bool,),
}

# Resource yang datanya sekali per encounter: kehilangan data karena rentang = error.
SINGLE_PER_ENCOUNTER = (
    "asesmen_awal",
    "resume_medis",
    "triase_igd",
    "laporan_operasi",
    "billing",
)


# Aturan yang sudah ditangani mapper cron-api-scheduler (rsefarina/mapper).
MAPPER_HANDLED = {
    "concept-array": "`mapPemeriksaanFisikConcept` (resume_medis)",
    "category-uppercase": "`mapCpptForRegistration` (hanya SOAP)",
    "performer-format": "`mapDispensePerformerId`",
    "obat-reference": "`dropDanglingObatReferences`",
    "duplicate-id": "`dropDanglingObatReferences` (hanya `obat.obat`)",
    "service-request-order": "`dropRadiologiServiceRequestId`",
    "assessment-text-missing": "`ensureAssessmentText`",
    "empty-entry": "`mapSectionsForRegistration` (entry kosong dibuang)",
}


@dataclass
class Result:
    registrasi: str
    resource: str
    label: str
    rentang: str
    payload: object = None
    error: str = None
    status: int = None


@dataclass
class Finding:
    rule: str
    level: str  # "error" | "warning" | "info"
    registrasi: str
    location: str
    detail: str
    samples: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def entries_of(payload, resource):
    if not isinstance(payload, dict):
        return []
    entries = payload.get(resource)
    return entries if isinstance(entries, list) else []


def is_grouped(entries):
    return any(
        isinstance(entry, dict) and any(isinstance(v, list) for v in entry.values())
        for entry in entries
    )


def iter_sections(payload, resource):
    """Yield (nama_section, index_entry, items). Resource flat → section = resource."""
    entries = entries_of(payload, resource)
    if not is_grouped(entries):
        yield resource, None, [e for e in entries if isinstance(e, dict)]
        return
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if isinstance(value, list):
                yield f"{resource}.{key}", index, [v for v in value if isinstance(v, dict)]


def section_counts(payload, resource):
    counts = defaultdict(int)
    for name, _, items in iter_sections(payload, resource):
        counts[name] += len(items)
    return counts


def ids(items, limit=5):
    values = [str(item.get("id", "?")) for item in items]
    more = f" (+{len(values) - limit})" if len(values) > limit else ""
    return values[:limit], more


# ---------------------------------------------------------------------------
# Konsistensi key
# ---------------------------------------------------------------------------


def key_consistency(results):
    """Return list baris (status, section, sampel, catatan)."""
    sections = {}
    wrappers = defaultdict(set)

    for result in results:
        if result.error or not isinstance(result.payload, dict):
            continue
        wrappers[result.resource].add(tuple(sorted(result.payload)))
        entries = entries_of(result.payload, result.resource)
        if is_grouped(entries):
            entry_section = sections.setdefault(
                f"{result.resource}.<entry>",
                {"keysets": set(), "types": defaultdict(set), "samples": 0, "regs": set()},
            )
            for entry in entries:
                if isinstance(entry, dict):
                    entry_section["samples"] += 1
                    entry_section["keysets"].add(tuple(sorted(entry)))
                    entry_section["regs"].add(result.registrasi)
        for name, _, items in iter_sections(result.payload, result.resource):
            section = sections.setdefault(
                name,
                {"keysets": set(), "types": defaultdict(set), "samples": 0, "regs": set()},
            )
            for item in items:
                section["samples"] += 1
                section["regs"].add(result.registrasi)
                section["keysets"].add(tuple(sorted(item)))
                for key, value in item.items():
                    section["types"][key].add(type_name(value))

    rows = []
    for resource, keysets in sorted(wrappers.items()):
        if len(keysets) > 1:
            rows.append(("!", resource, "-", f"wrapper berbeda: {sorted(keysets)}"))

    for name, section in sorted(sections.items()):
        if section["samples"] == 0:
            rows.append(("-", name, "0", "selalu kosong, key belum bisa dicek"))
            continue
        notes = []
        if len(section["keysets"]) > 1:
            all_keys = set().union(*section["keysets"])
            partial = sorted(
                k for k in all_keys if not all(k in ks for ks in section["keysets"])
            )
            notes.append(f"key tidak selalu ada: `{'`, `'.join(partial)}`")
        has_value_type = "value_type" in section["types"]
        mixed = {
            k: sorted(t)
            for k, t in section["types"].items()
            # value boleh beda tipe kalau mengikuti value_type (dicek di temuan)
            if len(t - {"null"}) > 1 and not (k == "value" and has_value_type)
        }
        if mixed:
            notes.append(
                "tipe campur: "
                + ", ".join(f"`{k}`={'|'.join(t)}" for k, t in mixed.items())
            )
        always_null = sorted(k for k, t in section["types"].items() if t == {"null"})
        if always_null:
            notes.append(f"selalu null: `{'`, `'.join(always_null)}`")
        status = "!" if (len(section["keysets"]) > 1 or mixed) else "ok"
        rows.append(
            (
                status,
                name,
                f"{section['samples']} item / {len(section['regs'])} reg",
                "; ".join(notes),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Aturan temuan (per registrasi, dari payload rentang dasar)
# ---------------------------------------------------------------------------


# Section yang concept array-nya sudah dinormalisasi mapper cron
# (mapPemeriksaanFisikConcept): konsisten selalu array, jadi cukup warning.
CONCEPT_ARRAY_HANDLED = {"resume_medis.pemeriksaan_fisik"}


def rule_concept_array(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            bad = [i for i in items if isinstance(i.get("concept"), list)]
            if bad:
                sample, more = ids(bad)
                level = "warning" if name in CONCEPT_ARRAY_HANDLED else "error"
                findings.append(Finding(
                    "concept-array", level, reg, name,
                    f"{len(bad)} item `concept` berupa array, Serenic mengharapkan object",
                    sample + ([more] if more else []),
                ))


def rule_category_uppercase(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            cats = sorted({
                i["category"] for i in items
                if isinstance(i.get("category"), str)
                and i["category"] != i["category"].lower()
            })
            if cats:
                findings.append(Finding(
                    "category-uppercase", "warning", reg, name,
                    f"`category` huruf besar: {', '.join(cats)}",
                ))


def rule_performer_format(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            for key in PERFORMER_KEYS:
                bad = sorted({
                    i[key] for i in items
                    if isinstance(i.get(key), str) and i[key]
                    and not PERFORMER_PATTERN.match(i[key])
                })
                if bad:
                    findings.append(Finding(
                        "performer-format", "warning", reg, f"{name}.{key}",
                        f"{len(bad)} nilai tidak berpola `MD-xxxxx`",
                        bad[:5],
                    ))


def rule_value_type(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            bad = []
            for item in items:
                vt, value = item.get("value_type"), item.get("value")
                expected = VALUE_TYPE_PYTHON.get(vt)
                if not expected or value is None:
                    continue
                if isinstance(value, bool) and bool not in expected:
                    bad.append(item)
                elif not isinstance(value, expected):
                    bad.append(item)
            if bad:
                sample, more = ids(bad)
                findings.append(Finding(
                    "value-type-mismatch", "error", reg, name,
                    f"{len(bad)} item `value` tidak sesuai `value_type`",
                    sample + ([more] if more else []),
                ))


def rule_blank_strings(reg, payloads, findings):
    """Field string kosong '' (bukan null) — sering lolos tapi tidak bermakna."""
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            counts = defaultdict(int)
            for item in items:
                for key, value in item.items():
                    if value == "":
                        counts[key] += 1
            if counts:
                findings.append(Finding(
                    "blank-string", "info", reg, name,
                    "string kosong `\"\"`: "
                    + ", ".join(f"`{k}` {v}/{len(items)}" for k, v in sorted(counts.items())),
                ))


def rule_diagnosis_code(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            if not (name.endswith("diagnosis") or "diagnosis_" in name.split(".")[-1]
                    or name in ("diagnosis_aktif", "diagnosis_awal")):
                continue
            blank = [i for i in items if i.get("code") == ""]
            nulls = [i for i in items if "code" in i and i["code"] is None]
            if blank:
                findings.append(Finding(
                    "diagnosis-code", "warning", reg, name,
                    f"{len(blank)}/{len(items)} `code` = \"\" (source lain memakai null)",
                    ids(blank)[0],
                ))
            if nulls and len(nulls) == len(items):
                findings.append(Finding(
                    "diagnosis-code", "info", reg, name,
                    f"semua `code` null ({len(items)} item), diagnosis tanpa ICD",
                ))


def rule_prosedur_null(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            if not name.split(".")[-1].startswith("prosedur"):
                continue
            for key in ("status", "category"):
                nulls = [i for i in items if key in i and i[key] is None]
                if nulls:
                    findings.append(Finding(
                        "prosedur-null", "warning", reg, f"{name}.{key}",
                        f"{len(nulls)}/{len(items)} item `{key}` null",
                        ids(nulls)[0],
                    ))


def rule_duplicate_ids(reg, payloads, findings):
    for resource, payload in payloads.items():
        for name, _, items in iter_sections(payload, resource):
            seen, dups = set(), []
            for item in items:
                item_id = item.get("id")
                if item_id in (None, ""):
                    continue
                if item_id in seen:
                    dups.append(item_id)
                seen.add(item_id)
            if dups:
                findings.append(Finding(
                    "duplicate-id", "error", reg, name,
                    f"{len(dups)} id duplikat dalam section",
                    sorted(set(dups))[:5],
                ))


def rule_obat_references(reg, payloads, findings):
    payload = payloads.get("obat")
    for index, entry in enumerate(entries_of(payload, "obat")):
        if not isinstance(entry, dict):
            continue
        med_ids = {o.get("id") for o in entry.get("obat") or [] if isinstance(o, dict)}
        req_ids = {r.get("id") for r in entry.get("resep_obat") or [] if isinstance(r, dict)}
        loc = f"obat[{index}]"
        missing_med = sorted({
            r.get("medication_id") for r in entry.get("resep_obat") or []
            if isinstance(r, dict) and r.get("medication_id") and r["medication_id"] not in med_ids
        } | {
            p.get("medication_id") for p in entry.get("pengeluaran_obat") or []
            if isinstance(p, dict) and p.get("medication_id") and p["medication_id"] not in med_ids
        })
        if missing_med:
            findings.append(Finding(
                "obat-reference", "error", reg, loc,
                f"{len(missing_med)} medication_id dirujuk resep/pengeluaran tapi tidak ada di `obat`",
                missing_med[:5],
            ))
        missing_req = sorted({
            p.get("medication_request_id") for p in entry.get("pengeluaran_obat") or []
            if isinstance(p, dict) and p.get("medication_request_id")
            and p["medication_request_id"] not in req_ids
        })
        if missing_req:
            findings.append(Finding(
                "obat-reference", "error", reg, loc,
                f"{len(missing_req)} medication_request_id di pengeluaran tidak ada di `resep_obat`",
                missing_req[:5],
            ))


def rule_service_request_reference(reg, payloads, findings):
    """service_request_id di laporan_radiologi/prosedur_operasi vs pemesanan sumbernya."""
    radiologi_orders = {
        o.get("id")
        for _, _, items in iter_sections(payloads.get("penunjang_radiologi"), "penunjang_radiologi")
        for o in items
    } if "penunjang_radiologi" in payloads else None

    for resource in ("asesmen_awal", "resume_medis"):
        for name, _, items in iter_sections(payloads.get(resource), resource):
            if not name.endswith("laporan_radiologi"):
                continue
            refs = [i for i in items if i.get("service_request_id")]
            if not refs:
                continue
            detail = (
                f"{len(refs)} item punya `service_request_id` (merujuk "
                "`penunjang_radiologi` yang di-ingest belakangan)"
            )
            if radiologi_orders is not None:
                missing = sorted({
                    i["service_request_id"] for i in refs
                    if i["service_request_id"] not in radiologi_orders
                })
                if missing:
                    detail += f"; {len(missing)} tidak ada di penunjang_radiologi"
            findings.append(Finding(
                "service-request-order", "warning", reg, name, detail,
                sorted({i["service_request_id"] for i in refs})[:5],
            ))

    # Di dalam source yang sama: laporan_* harus merujuk pemesanan yang ada.
    for resource, report_section in (
        ("penunjang_radiologi", "laporan_radiologi"),
        ("penunjang_lab", "laporan_lab"),
        ("penunjang_lab", "hasil_lab"),
        ("laporan_operasi", "prosedur_operasi"),
    ):
        for index, entry in enumerate(entries_of(payloads.get(resource), resource)):
            if not isinstance(entry, dict):
                continue
            orders = {o.get("id") for o in entry.get("pemesanan") or [] if isinstance(o, dict)}
            missing = sorted({
                i.get("service_request_id") for i in entry.get(report_section) or []
                if isinstance(i, dict) and i.get("service_request_id")
                and i["service_request_id"] not in orders
            })
            if missing:
                findings.append(Finding(
                    "service-request-reference", "error", reg,
                    f"{resource}[{index}].{report_section}",
                    f"{len(missing)} service_request_id tidak ada di `pemesanan`",
                    missing[:5],
                ))


def rule_assessment_text(reg, payloads, findings):
    for index, entry in enumerate(entries_of(payloads.get("asesmen_awal"), "asesmen_awal")):
        if not isinstance(entry, dict):
            continue
        others = any(
            isinstance(v, list) and v for k, v in entry.items() if k != "assessment_text"
        )
        if others and not entry.get("assessment_text"):
            findings.append(Finding(
                "assessment-text-missing", "warning", reg, f"asesmen_awal[{index}]",
                "`assessment_text` (wajib) kosong padahal section lain ada isinya "
                "(mapper cron mengisi placeholder \"-\")",
            ))


def rule_empty_entries(reg, payloads, findings):
    for resource, payload in payloads.items():
        entries = entries_of(payload, resource)
        if not is_grouped(entries):
            continue
        empty = [
            i for i, e in enumerate(entries)
            if isinstance(e, dict) and not any(isinstance(v, list) and v for v in e.values())
        ]
        if empty and len(empty) == len(entries):
            findings.append(Finding(
                "empty-entry", "info", reg, resource,
                f"{len(entries)} entry dikirim tapi semua section kosong",
            ))


def rule_billing_null_period(reg, payloads, findings):
    items = entries_of(payloads.get("billing"), "billing")
    nulls = [i for i in items if isinstance(i, dict) and i.get("billable_period_start") is None]
    if nulls:
        sample, more = ids(nulls)
        findings.append(Finding(
            "billing-null-period", "warning", reg, "billing",
            f"{len(nulls)}/{len(items)} item `billable_period_start` null "
            "(hilang begitu request memakai rentang tanggal)",
            sample + ([more] if more else []),
        ))


def rule_placeholder_medication(reg, payloads, findings):
    for resource in ("asesmen_awal", "resume_medis"):
        for name, _, items in iter_sections(payloads.get(resource), resource):
            if not name.endswith("riwayat_penggunaan_obat"):
                continue
            bad = [i for i in items if not i.get("medication_name")]
            if bad:
                findings.append(Finding(
                    "placeholder-medication", "warning", reg, name,
                    f"{len(bad)} item tanpa `medication_name` (kemungkinan placeholder)",
                    ids(bad)[0],
                ))


RULES = [
    rule_concept_array,
    rule_value_type,
    rule_duplicate_ids,
    rule_obat_references,
    rule_service_request_reference,
    rule_category_uppercase,
    rule_performer_format,
    rule_diagnosis_code,
    rule_prosedur_null,
    rule_assessment_text,
    rule_billing_null_period,
    rule_placeholder_medication,
    rule_empty_entries,
    rule_blank_strings,
]


# ---------------------------------------------------------------------------
# Perbandingan rentang (mode variants)
# ---------------------------------------------------------------------------


def range_differences(reg, results_by_label, findings):
    base = results_by_label.get("tanpa_rentang")
    if not base:
        return
    for label, by_resource in results_by_label.items():
        if label in ("tanpa_rentang", "cron_incremental"):
            continue
        for resource, result in by_resource.items():
            base_result = base.get(resource)
            if not base_result or base_result.error:
                continue
            if result.error:
                findings.append(Finding(
                    "range-error", "error", reg, resource,
                    f"rentang `{result.rentang}` ({label}) error, tanpa rentang OK: {result.error[:120]}",
                ))
                continue
            before = section_counts(base_result.payload, resource)
            after = section_counts(result.payload, resource)
            lost = [
                f"{name.split('.')[-1]} {before[name]}→{after.get(name, 0)}"
                for name in before
                if after.get(name, 0) < before[name]
            ]
            if lost:
                level = "error" if resource in SINGLE_PER_ENCOUNTER else "warning"
                findings.append(Finding(
                    "range-data-loss", level, reg, resource,
                    f"rentang `{result.rentang}` ({label}) kehilangan data: {', '.join(lost)}",
                ))


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

LEVEL_ICON = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


def build_report(results, registrations, resources, mode, generated_at, metadata=None):
    metadata = metadata or {}
    by_reg = defaultdict(lambda: defaultdict(dict))  # reg -> label -> resource -> Result
    for result in results:
        by_reg[result.registrasi][result.label][result.resource] = result

    findings = []
    for reg in registrations:
        labels = by_reg.get(reg, {})
        base_label = next((l for l in BASE_LABELS if l in labels), None)
        if base_label is None and labels:
            base_label = next(iter(labels))
        base = labels.get(base_label, {})
        payloads = {
            resource: r.payload for resource, r in base.items() if not r.error
        }
        for resource, r in base.items():
            if r.error:
                findings.append(Finding(
                    "request-error", "error", reg, resource,
                    f"request `{r.rentang}` gagal: {r.error[:200]}",
                ))
        for rule in RULES:
            rule(reg, payloads, findings)
        if mode == "variants":
            range_differences(reg, labels, findings)

    total = len(results)
    errors = sum(1 for r in results if r.error)
    lines = [
        "# Laporan testing API RSEFARINA",
        "",
        f"- Waktu: {generated_at}",
        f"- Mode: `{mode}`",
        f"- Registrasi: {len(registrations)} · Resource: {len(resources)} · "
        f"Request: {total} · Error: {errors}",
        "",
    ]

    # Ringkasan
    counts = defaultdict(int)
    for f in findings:
        counts[f.level] += 1
    lines += [
        "## Ringkasan",
        "",
        f"- {LEVEL_ICON['error']} error: {counts['error']} · "
        f"{LEVEL_ICON['warning']} warning: {counts['warning']} · "
        f"{LEVEL_ICON['info']} info: {counts['info']}",
        "",
    ]

    by_rule = defaultdict(list)
    for f in findings:
        by_rule[(f.level, f.rule)].append(f)
    if by_rule:
        lines += [
            "| Level | Aturan | Registrasi terdampak | Kemunculan | Ditangani mapper cron |",
            "|---|---|---|---|---|",
        ]
        for (level, rule), items in sorted(
            by_rule.items(), key=lambda kv: (LEVEL_ORDER[kv[0][0]], kv[0][1])
        ):
            regs = len({f.registrasi for f in items})
            handled = MAPPER_HANDLED.get(rule, "belum")
            lines.append(
                f"| {LEVEL_ICON[level]} | `{rule}` | {regs}/{len(registrations)} "
                f"| {len(items)} | {handled} |"
            )
        lines.append("")

    # Isi per registrasi
    lines += ["## Isi per registrasi", ""]
    header_resources = list(resources)
    meta_columns = [c for c in ("admission_type", "period_start", "period_end")
                    if any(c in m for m in metadata.values())]
    lines.append(
        "| Registrasi | " + "".join(f"{c} | " for c in meta_columns)
        + " | ".join(f"`{r}`" for r in header_resources) + " |"
    )
    lines.append("|---" * (len(header_resources) + len(meta_columns) + 1) + "|")
    for reg in registrations:
        labels = by_reg.get(reg, {})
        base_label = next((l for l in BASE_LABELS if l in labels), None) or next(iter(labels), None)
        base = labels.get(base_label, {})
        cells = []
        for resource in header_resources:
            r = base.get(resource)
            if r is None:
                cells.append("-")
            elif r.error:
                cells.append("ERROR")
            else:
                total_items = sum(section_counts(r.payload, resource).values())
                cells.append("kosong" if total_items == 0 else str(total_items))
        meta_cells = [metadata.get(reg, {}).get(c, "") for c in meta_columns]
        lines.append(f"| `{reg}` | " + "".join(f"{c} | " for c in meta_cells) + " | ".join(cells) + " |")
    lines += ["", "Angka = total item semua section (rentang dasar).", ""]

    # Konsistensi key
    lines += [
        "## Konsistensi key",
        "",
        "Digabung dari semua registrasi & rentang. `!` = key/tipe tidak konsisten, "
        "`-` = selalu kosong.",
        "",
        "| | Section | Sampel | Catatan |",
        "|---|---|---|---|",
    ]
    for status, name, samples, note in key_consistency(results):
        lines.append(f"| {status} | `{name}` | {samples} | {note} |")
    lines.append("")

    # Detail temuan
    lines += ["## Detail temuan", ""]
    if not findings:
        lines += ["Tidak ada temuan.", ""]
    for (level, rule), items in sorted(
        by_rule.items(), key=lambda kv: (LEVEL_ORDER[kv[0][0]], kv[0][1])
    ):
        lines += [f"### {LEVEL_ICON[level]} `{rule}`", ""]
        if rule in MAPPER_HANDLED:
            lines += [f"Ditangani mapper cron: {MAPPER_HANDLED[rule]}.", ""]
        for f in items:
            sample = f" — contoh: `{'`, `'.join(map(str, f.samples))}`" if f.samples else ""
            lines.append(f"- `{f.registrasi}` · `{f.location}`: {f.detail}{sample}")
        lines.append("")

    return "\n".join(lines), findings
