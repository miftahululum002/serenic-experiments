import pandas as pd
import json
import os
from datetime import datetime
from collections import Counter

CSV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "_standalone.csv",
)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_MD = os.path.join(OUTPUT_DIR, "analisis_csv_hasil.md")

lines = []


def w(text=""):
    lines.append(text)


print("Sedang membaca CSV...")
df = pd.read_csv(CSV_PATH)
print(f"  Total baris: {len(df)}")


# ── Parse output JSON ──
def parse_output(val):
    if pd.isna(val) or val.strip() == "":
        return None
    try:
        return json.loads(val)
    except:
        return None


df["output_parsed"] = df["output"].apply(parse_output)


def safe_get(row, key, default=None):
    if row is None:
        return default
    return row.get(key, default)


df["inacbg_data"] = df["output_parsed"].apply(lambda x: safe_get(x, "inacbg"))
df["icd10_codes"] = df["output_parsed"].apply(lambda x: safe_get(x, "icd10_codes", []))
df["icd9cm_codes"] = df["output_parsed"].apply(
    lambda x: safe_get(x, "icd9cm_codes", [])
)

# ── Parse timestamps ──
df["created_time_dt"] = pd.to_datetime(df["created_time"], errors="coerce")
df["updated_time_dt"] = pd.to_datetime(df["updated_time"], errors="coerce")
df["processing_seconds"] = (
    df["updated_time_dt"] - df["created_time_dt"]
).dt.total_seconds()


# ── Parse INACBG fields ──
def get_inacbg_field(row, field, default=None):
    if row is None or not isinstance(row, dict):
        return default
    return row.get(field, default)


df["group_code"] = df["inacbg_data"].apply(lambda x: get_inacbg_field(x, "group_code"))
df["total_cost"] = df["inacbg_data"].apply(
    lambda x: get_inacbg_field(x, "total_cost", 0)
)
df["description_inacbg"] = df["inacbg_data"].apply(
    lambda x: get_inacbg_field(x, "description", "")
)
df["special_cmg"] = df["inacbg_data"].apply(
    lambda x: get_inacbg_field(x, "special_cmg", [])
)

# ── Status normalization ──
df["status_clean"] = df["status"].str.replace("JobStatus.", "", regex=False)

total = len(df)
w("# Hasil Analisis CSV Standalone")
w("")
w(f"> **File:** `{os.path.basename(CSV_PATH)}`")
w(f"> **Dianalisis pada:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
w(f"> **Total baris:** {total}")
w("")

# ══════════════════════════════════════════════
# 1. RINGKASAN STATUS
# ══════════════════════════════════════════════
w("## 1. Ringkasan Status Job")
w("")
status_counts = df["status_clean"].value_counts()
w("| Status | Jumlah | Persentase |")
w("|--------|--------|------------|")
for st, cnt in status_counts.items():
    w(f"| {st} | {cnt} | {cnt/total*100:.1f}% |")
w("")

# ══════════════════════════════════════════════
# 2. GENDER
# ══════════════════════════════════════════════
w("## 2. Distribusi Gender")
w("")
gender_counts = df["gender"].value_counts()
w("| Gender | Jumlah | Persentase |")
w("|--------|--------|------------|")
for g, cnt in gender_counts.items():
    w(f"| {g} | {cnt} | {cnt/total*100:.1f}% |")
w("")

# ══════════════════════════════════════════════
# 3. BPJS CLASS
# ══════════════════════════════════════════════
w("## 3. Distribusi Kelas BPJS")
w("")
bpjs_counts = df["bpjs_class"].value_counts().sort_index()
w("| Kelas | Jumlah | Persentase |")
w("|-------|--------|------------|")
for cls, cnt in bpjs_counts.items():
    w(f"| Kelas {int(cls)} | {cnt} | {cnt/total*100:.1f}% |")
w("")

# ══════════════════════════════════════════════
# 4. PROCESSING TIME
# ══════════════════════════════════════════════
w("## 4. Statistik Waktu Proses (Processing Time)")
w("")
df_valid_proc = df[df["processing_seconds"].notna() & (df["processing_seconds"] >= 0)]
if not df_valid_proc.empty:
    proc = df_valid_proc["processing_seconds"]
    w("| Metrik | Nilai |")
    w("|--------|-------|")
    w(f"| Jumlah data valid | {len(proc)} |")
    w(f"| Rata-rata (Mean) | {proc.mean():.1f} detik ({proc.mean()/60:.1f} menit) |")
    w(f"| Median (P50) | {proc.median():.1f} detik ({proc.median()/60:.1f} menit) |")
    w(f"| Std Deviasi | {proc.std():.1f} detik |")
    w(f"| Min | {proc.min():.1f} detik |")
    w(f"| Max | {proc.max():.1f} detik ({proc.max()/60:.1f} menit) |")
    w(f"| P25 | {proc.quantile(0.25):.1f} detik |")
    w(f"| P75 | {proc.quantile(0.75):.1f} detik |")
    w(f"| P90 | {proc.quantile(0.90):.1f} detik |")
    w(f"| P95 | {proc.quantile(0.95):.1f} detik |")
    w(f"| P99 | {proc.quantile(0.99):.1f} detik |")
    w("")

    # Processing time bucket
    w("### Bucket Waktu Proses")
    w("")
    bins = [0, 30, 60, 120, 300, 600, 1800, 3600, float("inf")]
    labels = ["<30s", "30-60s", "60-120s", "2-5m", "5-10m", "10-30m", "30-60m", ">60m"]
    df_valid_proc = df_valid_proc.copy()
    df_valid_proc["proc_bucket"] = pd.cut(
        df_valid_proc["processing_seconds"], bins=bins, labels=labels, right=False
    )
    bucket_counts = df_valid_proc["proc_bucket"].value_counts().sort_index()
    valid_total = len(df_valid_proc)
    w("| Bucket | Jumlah | Persentase | Grafik |")
    w("|--------|--------|------------|--------|")
    for bucket, count in bucket_counts.items():
        pct = count / valid_total * 100
        bar = "█" * max(1, int(pct / 2))
        w(f"| {bucket} | {count} | {pct:.1f}% | {bar} |")
    w("")
else:
    w("> Tidak ada data waktu proses yang valid.")
    w("")

# ══════════════════════════════════════════════
# 5. INACBG RESULT
# ══════════════════════════════════════════════
w("## 5. Hasil INACBG")
w("")
df_with_inacbg = df[df["inacbg_data"].notna()]
df_null_inacbg = df[df["inacbg_data"].isna()]
w(
    f"- Job dengan hasil INACBG: **{len(df_with_inacbg)}** ({len(df_with_inacbg)/total*100:.1f}%)"
)
w(
    f"- Job tanpa INACBG (null): **{len(df_null_inacbg)}** ({len(df_null_inacbg)/total*100:.1f}%)"
)
w("")

# Group code distribution
if not df_with_inacbg.empty:
    w("### Distribusi Group Code INACBG")
    w("")
    gc_counts = df_with_inacbg["group_code"].value_counts()
    w("| Group Code | Jumlah | Persentase |")
    w("|------------|--------|------------|")
    for gc, cnt in gc_counts.head(20).items():
        w(f"| {gc} | {cnt} | {cnt/len(df_with_inacbg)*100:.1f}% |")
    w("")

    # Cost distribution
    w("### Distribusi Total Cost (INACBG)")
    w("")
    costs = df_with_inacbg["total_cost"].dropna()
    costs_valid = costs[costs > 0]
    if not costs_valid.empty:
        w("| Metrik | Nilai |")
        w("|--------|-------|")
        w(f"| Jumlah dengan cost > 0 | {len(costs_valid)} |")
        w(f"| Rata-rata (Mean) | Rp {costs_valid.mean():,.0f} |")
        w(f"| Median (P50) | Rp {costs_valid.median():,.0f} |")
        w(f"| Min | Rp {costs_valid.min():,.0f} |")
        w(f"| Max | Rp {costs_valid.max():,.0f} |")
        w(f"| P25 | Rp {costs_valid.quantile(0.25):,.0f} |")
        w(f"| P75 | Rp {costs_valid.quantile(0.75):,.0f} |")
        w(f"| P90 | Rp {costs_valid.quantile(0.90):,.0f} |")
        w(f"| Total | Rp {costs_valid.sum():,.0f} |")
        w("")

    # Cost bucket
    w("### Bucket Total Cost")
    w("")
    cost_bins = [
        0,
        1_000_000,
        5_000_000,
        10_000_000,
        20_000_000,
        50_000_000,
        100_000_000,
        float("inf"),
    ]
    cost_labels = [
        "<1jt",
        "1-5jt",
        "5-10jt",
        "10-20jt",
        "20-50jt",
        "50-100jt",
        ">100jt",
    ]
    costs_df = pd.DataFrame({"cost": costs_valid})
    costs_df["cost_bucket"] = pd.cut(
        costs_df["cost"], bins=cost_bins, labels=cost_labels, right=False
    )
    cb_counts = costs_df["cost_bucket"].value_counts().sort_index()
    w("| Bucket | Jumlah | Persentase |")
    w("|--------|--------|------------|")
    for bucket, count in cb_counts.items():
        w(f"| {bucket} | {count} | {count/len(costs_valid)*100:.1f}% |")
    w("")

    # Description distribution (top 15)
    w("### Deskripsi INACBG (Top 15)")
    w("")
    desc_counts = df_with_inacbg["description_inacbg"].value_counts()
    w("| Deskripsi | Jumlah |")
    w("|-----------|--------|")
    for desc, cnt in desc_counts.head(15).items():
        w(f"| {desc} | {cnt} |")
    w("")

# ══════════════════════════════════════════════
# 6. ICD CODES
# ══════════════════════════════════════════════
w("## 6. Kode ICD")
w("")

# ICD-10
all_icd10 = []
for codes in df["icd10_codes"]:
    if isinstance(codes, list):
        all_icd10.extend(codes)
icd10_counter = Counter(all_icd10)
df_with_icd10 = df[
    df["icd10_codes"].apply(lambda x: isinstance(x, list) and len(x) > 0)
]
df_no_icd10 = df[
    df["icd10_codes"].apply(lambda x: not isinstance(x, list) or len(x) == 0)
]

w(
    f"- Job dengan ICD-10: **{len(df_with_icd10)}** ({len(df_with_icd10)/total*100:.1f}%)"
)
w(f"- Job tanpa ICD-10: **{len(df_no_icd10)}** ({len(df_no_icd10)/total*100:.1f}%)")
w(f"- Total kode ICD-10 unik: **{len(icd10_counter)}**")
w("")

if icd10_counter:
    w("### Top 20 Kode ICD-10")
    w("")
    w("| Kode ICD-10 | Jumlah | Persentase |")
    w("|-------------|--------|------------|")
    for code, cnt in icd10_counter.most_common(20):
        w(f"| {code} | {cnt} | {cnt/len(all_icd10)*100:.1f}% |")
    w("")

    # Jumlah ICD-10 per job
    icd10_per_job = df["icd10_codes"].apply(
        lambda x: len(x) if isinstance(x, list) else 0
    )
    w("### Jumlah ICD-10 Per Job")
    w("")
    w("| Metrik | Nilai |")
    w("|--------|-------|")
    w(f"| Rata-rata | {icd10_per_job.mean():.1f} |")
    w(f"| Median | {icd10_per_job.median():.0f} |")
    w(f"| Max | {icd10_per_job.max()} |")
    w(f"| Job dengan 0 kode | {len(icd10_per_job[icd10_per_job == 0])} |")
    w("")

# ICD-9-CM
all_icd9 = []
for codes in df["icd9cm_codes"]:
    if isinstance(codes, list):
        all_icd9.extend(codes)
icd9_counter = Counter(all_icd9)
df_with_icd9 = df[
    df["icd9cm_codes"].apply(lambda x: isinstance(x, list) and len(x) > 0)
]
df_no_icd9 = df[
    df["icd9cm_codes"].apply(lambda x: not isinstance(x, list) or len(x) == 0)
]

w(
    f"- Job dengan ICD-9-CM: **{len(df_with_icd9)}** ({len(df_with_icd9)/total*100:.1f}%)"
)
w(f"- Job tanpa ICD-9-CM: **{len(df_no_icd9)}** ({len(df_no_icd9)/total*100:.1f}%)")
w(f"- Total kode ICD-9-CM unik: **{len(icd9_counter)}**")
w("")

if icd9_counter:
    w("### Top 20 Kode ICD-9-CM")
    w("")
    w("| Kode ICD-9-CM | Jumlah | Persentase |")
    w("|---------------|--------|------------|")
    for code, cnt in icd9_counter.most_common(20):
        w(f"| {code} | {cnt} | {cnt/len(all_icd9)*100:.1f}% |")
    w("")

# ══════════════════════════════════════════════
# 7. JOB FAILED DETAIL
# ══════════════════════════════════════════════
df_failed = df[df["status_clean"] == "FAILED"]
df_started = df[df["status_clean"] == "STARTED"]
w("## 7. Job Gagal & Belum Selesai")
w("")
w(f"- Job FAILED: **{len(df_failed)}** ({len(df_failed)/total*100:.1f}%)")
w(
    f"- Job STARTED (belum selesai): **{len(df_started)}** ({len(df_started)/total*100:.1f}%)"
)
w("")

if not df_failed.empty:
    w("### Detail Job FAILED")
    w("")
    note_counts = df_failed["note"].value_counts()
    w("| Note | Jumlah |")
    w("|------|--------|")
    for note, cnt in note_counts.items():
        w(f"| {note} | {cnt} |")
    w("")

    if len(df_failed) <= 20:
        w("| ID | Created | Note |")
        w("|-----|---------|------|")
        for _, row in df_failed.iterrows():
            created = (
                row["created_time"][:19] if pd.notna(row["created_time"]) else "N/A"
            )
            w(f"| `{row['id'][:8]}...` | {created} | {row.get('note', '')} |")
        w("")

if not df_started.empty:
    w("### Detail Job STARTED (Belum Selesai)")
    w("")
    w("| ID | Created | Updated |")
    w("|-----|---------|---------|")
    for _, row in df_started.iterrows():
        created = row["created_time"][:19] if pd.notna(row["created_time"]) else "N/A"
        updated = row["updated_time"][:19] if pd.notna(row["updated_time"]) else "N/A"
        w(f"| `{row['id'][:8]}...` | {created} | {updated} |")
    w("")

# ══════════════════════════════════════════════
# 8. ADMISSION TYPE & DATE
# ══════════════════════════════════════════════
w("## 8. Tipe Rawat Inap & Periode")
w("")
adm_counts = df["admission_type"].value_counts()
w("| Admission Type | Jumlah | Persentase |")
w("|----------------|--------|------------|")
for at, cnt in adm_counts.items():
    w(f"| {at} | {cnt} | {cnt/total*100:.1f}% |")
w("")

df["created_date"] = df["created_time_dt"].dt.date
date_counts = df["created_date"].value_counts().sort_index()
w("### Distribusi Tanggal Dibuat (created_time)")
w("")
w("| Tanggal | Jumlah | Persentase | Grafik |")
w("|---------|--------|------------|--------|")
for dt, cnt in date_counts.items():
    bar = "█" * max(1, int(cnt / date_counts.max() * 30))
    w(f"| {dt} | {cnt} | {cnt/total*100:.1f}% | {bar} |")
w("")

# ══════════════════════════════════════════════
# 9. KESIMPULAN
# ══════════════════════════════════════════════
w("## 9. Kesimpulan")
w("")
w(f"- Total **{total}** job dianalisis dari file CSV.")
finished = len(df[df["status_clean"] == "FINISHED"])
w(f"- **{finished}** job ({finished/total*100:.1f}%) berhasil selesai (FINISHED).")
w(f"- **{len(df_failed)}** job ({len(df_failed)/total*100:.1f}%) gagal (FAILED).")
w(
    f"- **{len(df_started)}** job ({len(df_started)/total*100:.1f}%) masih dalam proses (STARTED)."
)
if not df_with_inacbg.empty:
    w(f"- Dari {len(df_with_inacbg)} job dengan hasil INACBG:")
    failed_inacbg = len(df_with_inacbg[df_with_inacbg["total_cost"] == 0])
    w(f"  - **{failed_inacbg}** job memiliki total_cost = 0 (FAILED: EMPTY RESULT).")
    successful_inacbg = len(df_with_inacbg[df_with_inacbg["total_cost"] > 0])
    w(f"  - **{successful_inacbg}** job memiliki cost > 0.")
if not df_valid_proc.empty:
    w(
        f"- Rata-rata waktu proses: **{df_valid_proc['processing_seconds'].mean():.1f} detik** ({df_valid_proc['processing_seconds'].mean()/60:.1f} menit)."
    )
w("")

# ── Generate output ──
md_content = "\n".join(lines)

with open(OUTPUT_MD, "w") as f:
    f.write(md_content)

print(md_content)
print(f"\n{'=' * 60}")
print(f"Laporan tersimpan ke: {OUTPUT_MD}")
print(f"{'=' * 60}")
