import pandas as pd
import re
import sys
import os
from datetime import datetime
from collections import Counter

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILES = [
    os.path.join(LOG_DIR, "worker-standalone1.log"),
    os.path.join(LOG_DIR, "worker-standalone2.log"),
]

job_tracker = {}
warnings_list = []
pipeline_stats = []
job_codes = {}

enqueued_pattern = re.compile(
    r"\[([^\]]+)\] \[([^\]]+)\] \[standalone\] INACBG task enqueued"
)
timeout_pattern = re.compile(
    r"\[([^\]]+)\] \[([^\]]+)\] \[standalone\] INACBG polling timed out"
)
completed_pattern = re.compile(
    r"INFO:rq.worker:.*run_id='([^']+)'.* job in ([\d.:]+)s on worker"
)
start_pattern = re.compile(r"\[([^\]]+)\] \[([^\]]+)\] \[standalone\] Start")
pipeline_potential_pattern = re.compile(
    r"\[([^\]]+)\] \[standalone\] \[3\] pipeline_potential=(\d+)"
)
complete_pattern = re.compile(r"\[([^\]]+)\] \[standalone\] \[4b\] complete")
icd10_pattern = re.compile(r"ICD10=\[([^\]]*)\]")
icd9cm_pattern = re.compile(r"ICD9CM=\[([^\]]*)\]")
normalized_pattern = re.compile(r"\[encounter=([^\]]+)\]  - Normalized phrases = (\d+)")
matched_pattern = re.compile(r"\[encounter=([^\]]+)\]  - matched_codes = (\d+)")
warning_pattern = re.compile(r"WARNING:(.*)")
inacbg_no_pattern = re.compile(r"INACBGS=(\w+)")

print("Sedang membaca dan memproses log...")

for log_file_path in LOG_FILES:
    if not os.path.exists(log_file_path):
        print(f"  [SKIP] File tidak ditemukan: {log_file_path}")
        continue
    print(f"  [READ] {os.path.basename(log_file_path)}")
    with open(log_file_path, "r") as f:
        for line in f:
            enqueued_match = enqueued_pattern.search(line)
            if enqueued_match:
                timestamp, run_id = enqueued_match.group(1), enqueued_match.group(2)
                job_tracker[run_id] = {
                    "run_id": run_id,
                    "start_time": timestamp,
                    "status": "Queue",
                    "timeout_occurred": False,
                    "latency_seconds": None,
                    "timeout_extra_seconds": None,
                    "source_file": os.path.basename(log_file_path),
                }
                continue

            timeout_match = timeout_pattern.search(line)
            if timeout_match:
                timestamp, run_id = timeout_match.group(1), timeout_match.group(2)
                if run_id in job_tracker:
                    job_tracker[run_id]["status"] = "Failed (Timeout)"
                    job_tracker[run_id]["timeout_occurred"] = True
                continue

            completed_match = completed_pattern.search(line)
            if completed_match:
                run_id, duration_str = (
                    completed_match.group(1),
                    completed_match.group(2),
                )
                try:
                    t = datetime.strptime(duration_str.strip(), "%H:%M:%S.%f")
                    total_seconds = (
                        t.hour * 3600
                        + t.minute * 60
                        + t.second
                        + t.microsecond / 1e6
                    )
                except ValueError:
                    try:
                        t = datetime.strptime(duration_str.strip(), "%H:%M:%S")
                        total_seconds = t.hour * 3600 + t.minute * 60 + t.second
                    except:
                        continue

                if run_id in job_tracker:
                    job_tracker[run_id]["latency_seconds"] = total_seconds
                    if job_tracker[run_id]["timeout_occurred"]:
                        job_tracker[run_id]["status"] = "Timeout tapi Selesai"
                        job_tracker[run_id]["timeout_extra_seconds"] = (
                            total_seconds - 300
                        )
                    else:
                        job_tracker[run_id]["status"] = "Success"
                else:
                    job_tracker[run_id] = {
                        "run_id": run_id,
                        "start_time": None,
                        "status": "Success",
                        "timeout_occurred": False,
                        "latency_seconds": total_seconds,
                        "timeout_extra_seconds": None,
                        "source_file": os.path.basename(log_file_path),
                    }

            start_match = start_pattern.search(line)
            if start_match:
                ts, rid = start_match.group(1), start_match.group(2)
                if rid in job_tracker:
                    job_tracker[rid]["start_time"] = ts

            pp_match = pipeline_potential_pattern.search(line)
            if pp_match:
                rid, val = pp_match.group(1), pp_match.group(2)
                if rid in job_tracker:
                    job_tracker[rid]["pipeline_potential"] = int(val)

            comp_match = complete_pattern.search(line)
            if comp_match:
                rid = comp_match.group(1)
                if rid in job_tracker:
                    icd10_m = icd10_pattern.search(line)
                    icd9_m = icd9cm_pattern.search(line)
                    inacbg_m = inacbg_no_pattern.search(line)
                    job_tracker[rid]["icd10_count"] = (
                        len(icd10_m.group(1).split(", "))
                        if icd10_m and icd10_m.group(1)
                        else 0
                    )
                    job_tracker[rid]["icd9cm_count"] = (
                        len(icd9_m.group(1).split(", "))
                        if icd9_m and icd9_m.group(1)
                        else 0
                    )
                    job_tracker[rid]["inacbg_result"] = (
                        inacbg_m.group(1) if inacbg_m else "?"
                    )

            norm_match = normalized_pattern.search(line)
            if norm_match:
                rid = norm_match.group(1)
                cnt = int(norm_match.group(2))
                pipeline_stats.append(
                    {
                        "run_id": rid,
                        "normalized_phrases": cnt,
                        "source_file": os.path.basename(log_file_path),
                    }
                )

            match_match = matched_pattern.search(line)
            if match_match:
                rid = match_match.group(1)
                cnt = int(match_match.group(2))
                existing = next(
                    (p for p in pipeline_stats if p["run_id"] == rid), None
                )
                if existing:
                    existing["matched_codes"] = cnt

            warn_match = warning_pattern.search(line)
            if warn_match:
                msg = warn_match.group(1).strip()
                if "INACBG polling timed out" in msg:
                    continue
                warnings_list.append(msg)

df = pd.DataFrame(list(job_tracker.values()))
df_pipe = pd.DataFrame(pipeline_stats) if pipeline_stats else pd.DataFrame()

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_MD = os.path.join(OUTPUT_DIR, "analisis_hasil.md")

lines = []


def w(text=""):
    lines.append(text)


if df.empty:
    w("# Hasil Analisis Log Worker")
    w("")
    w("> **PERINGATAN:** Tidak ada data transaksi job yang ditemukan di file log.")
else:
    total = len(df)
    success = len(df[df["status"] == "Success"])
    timeout_then_ok = len(df[df["status"] == "Timeout tapi Selesai"])
    failed_only = len(df[df["status"] == "Failed (Timeout)"])
    with_timeout = len(df[df["timeout_occurred"] == True])
    df_latency = df[df["latency_seconds"].notna()]

    w("# Hasil Analisis Log Worker (Multi File)")
    w("")
    w(f"> Dianalisis pada: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    w(f"> Jumlah file log: **{len([f for f in LOG_FILES if os.path.exists(f)])}** file")
    w("")

    # ── Tabel Ringkasan Per File ──
    w("## Ringkasan Per File Log")
    w("")
    w("| File | Total Job | Success | Timeout OK | Failed | Terkena Timeout | Rata-rata Latency |")
    w("|------|-----------|---------|------------|--------|-----------------|-------------------|")
    for fname in sorted(df["source_file"].unique()):
        sub = df[df["source_file"] == fname]
        sub_total = len(sub)
        sub_success = len(sub[sub["status"] == "Success"])
        sub_timeout_ok = len(sub[sub["status"] == "Timeout tapi Selesai"])
        sub_failed = len(sub[sub["status"] == "Failed (Timeout)"])
        sub_with_timeout = len(sub[sub["timeout_occurred"] == True])
        df_sub_lat = sub[sub["latency_seconds"].notna()]
        avg_lat = f"{df_sub_lat['latency_seconds'].mean():.1f}s" if not df_sub_lat.empty else "N/A"
        w(f"| `{fname}` | {sub_total} | {sub_success} | {sub_timeout_ok} | {sub_failed} | {sub_with_timeout} | {avg_lat} |")
    w("")

    # ── Ringkasan Gabungan ──
    w("## Ringkasan Gabungan")
    w("")
    w("| Metrik | Jumlah | Persentase |")
    w("|--------|--------|------------|")
    w(f"| Total job diproses | {total} | 100% |")
    w(f"| Berhasil (Success) | {success} | {success/total*100:.1f}% |")
    w(f"| Timeout lalu OK | {timeout_then_ok} | {timeout_then_ok/total*100:.1f}% |")
    w(f"| Gagal total (Failed) | {failed_only} | {failed_only/total*100:.1f}% |")
    w(f"| Total terkena timeout | {with_timeout} | {with_timeout/total*100:.1f}% |")
    w("")

    # ── Distribusi Status ──
    w("## Distribusi Status Job")
    w("")
    w("| Status | Jumlah | Persentase |")
    w("|--------|--------|------------|")
    for status, count in df["status"].value_counts().items():
        pct = count / total * 100
        w(f"| {status} | {count} | {pct:.1f}% |")
    w("")

    # ── Statistik Latency ──
    w("## Statistik Latency (Detik)")
    w("")
    if not df_latency.empty:
        lat = df_latency["latency_seconds"]
        w("| Metrik | Nilai |")
        w("|--------|-------|")
        w(f"| Jumlah data valid | {len(lat)} |")
        w(f"| Rata-rata (Mean) | {lat.mean():.2f} detik ({lat.mean()/60:.1f} menit) |")
        w(f"| Median (P50) | {lat.median():.2f} detik ({lat.median()/60:.1f} menit) |")
        w(f"| Std Deviasi | {lat.std():.2f} detik |")
        w(f"| Persentil 75 (P75) | {lat.quantile(0.75):.2f} detik |")
        w(f"| Persentil 90 (P90) | {lat.quantile(0.90):.2f} detik |")
        w(f"| Persentil 95 (P95) | {lat.quantile(0.95):.2f} detik |")
        w(f"| Persentil 99 (P99) | {lat.quantile(0.99):.2f} detik |")
        w(f"| Paling Cepat | {lat.min():.2f} detik |")
        w(f"| Paling Lama | {lat.max():.2f} detik |")
        w("")

        w("### Latency per Status")
        w("")
        w("| Status | Count | Mean | Median | Max |")
        w("|--------|-------|------|--------|-----|")
        for status in df["status"].unique():
            sub = df[df["status"] == status]["latency_seconds"].dropna()
            if not sub.empty:
                w(f"| {status} | {len(sub)} | {sub.mean():.2f}s | {sub.median():.2f}s | {sub.max():.2f}s |")
        w("")
    else:
        w("> Tidak ada data durasi latency yang valid.")
        w("")

    # ── Detail Timeout ──
    w("## Detail Job Timeout (300s INACBG)")
    w("")
    df_timeout = df[df["timeout_occurred"] == True]
    if not df_timeout.empty:
        w(f"Total job timeout: **{len(df_timeout)}**")
        w("")
        if "timeout_extra_seconds" in df_timeout.columns:
            extra = df_timeout["timeout_extra_seconds"].dropna()
            if not extra.empty:
                w("| Metrik | Nilai |")
                w("|--------|-------|")
                w(f"| Rata-rata waktu tambahan | {extra.mean():.2f} detik |")
                w(f"| Median waktu tambahan | {extra.median():.2f} detik |")
                w(f"| Max waktu tambahan | {extra.max():.2f} detik |")
                w(f"| Semua job timeout selesai? | {'YA' if (extra >= 0).all() else 'TIDAK'} |")
                w("")
        w("### Daftar run_id yang timeout")
        w("")
        w("| Run ID | Latency | Tambahan | Status |")
        w("|--------|---------|----------|--------|")
        for _, row in df_timeout.iterrows():
            lat = f"{row['latency_seconds']:.1f}s" if pd.notna(row.get("latency_seconds")) else "N/A"
            extra_s = f"+{row['timeout_extra_seconds']:.1f}s" if pd.notna(row.get("timeout_extra_seconds")) else ""
            status_short = "OK" if row["status"] == "Timeout tapi Selesai" else "FAILED"
            w(f"| `{row['run_id']}` | {lat} | {extra_s} | {status_short} |")
        w("")
    else:
        w("> Tidak ada job yang terkena timeout.")
        w("")

    # ── Warning Distribution ──
    w("## Distribusi Warning / Error")
    w("")
    if warnings_list:
        warn_counter = Counter()
        for wmsg in warnings_list:
            if "RuleContextOutputMapper.map" in wmsg:
                warn_counter["RuleContextOutputMapper: entity tidak punya rule coverage"] += 1
            elif "SAWarning" in wmsg or "AnnotatedColumn" in wmsg or "Column" in wmsg:
                warn_counter["SQLAlchemy SAWarning (inherit_cache)"] += 1
            elif "PydanticSerializationUnexpectedValue" in wmsg:
                warn_counter["Pydantic serialization warning (embedding ndarray)"] += 1
            else:
                key = wmsg[:80] + ("..." if len(wmsg) > 80 else "")
                warn_counter[key] += 1
        w("| Jumlah | Tipe Warning |")
        w("|--------|--------------|")
        for wtype, count in warn_counter.most_common():
            w(f"| {count}x | {wtype} |")
        w("")
    else:
        w("> Tidak ada warning ditemukan (selain INACBG timeout).")
        w("")

    # ── INACBGS Distribution ──
    w("## Distribusi Hasil INACBGS")
    w("")
    if "inacbg_result" in df.columns:
        w("| INACBGS | Jumlah Job |")
        w("|---------|------------|")
        for val, count in df["inacbg_result"].value_counts().items():
            w(f"| {val} | {count} |")
        w("")

    # ── Pipeline Stats ──
    if not df_pipe.empty:
        w("## Statistik Pipeline")
        w("")
        w("| Metrik | Mean | Median | Max |")
        w("|--------|------|--------|-----|")
        if "normalized_phrases" in df_pipe.columns:
            np_ = df_pipe["normalized_phrases"]
            w(f"| Normalized Phrases | {np_.mean():.1f} | {np_.median():.0f} | {np_.max()} |")
        if "matched_codes" in df_pipe.columns:
            mc = df_pipe["matched_codes"]
            no_match = len(mc[mc == 0])
            w(f"| Matched Codes | {mc.mean():.1f} | {mc.median():.0f} | {mc.max()} |")
        w("")
        if "matched_codes" in df_pipe.columns:
            mc = df_pipe["matched_codes"]
            no_match = len(mc[mc == 0])
            w(f"Job dengan 0 matched: **{no_match}** ({no_match/len(mc)*100:.1f}%)")
            w("")

    # ── ICD Codes Distribution ──
    w("## Distribusi Jumlah ICD Per Job")
    w("")
    if "icd10_count" in df.columns or "icd9cm_count" in df.columns:
        w("| Tipe ICD | Mean | Median | Max | Keterangan |")
        w("|----------|------|--------|-----|------------|")
        if "icd10_count" in df.columns:
            icd10_counts = df["icd10_count"]
            w(f"| ICD-10 | {icd10_counts.mean():.1f} | {icd10_counts.median():.0f} | {icd10_counts.max()} | |")
        if "icd9cm_count" in df.columns:
            icd9_counts = df["icd9cm_count"]
            has_icd9 = len(icd9_counts[icd9_counts > 0])
            w(f"| ICD-9-CM | {icd9_counts.mean():.1f} | {icd9_counts.median():.0f} | {icd9_counts.max()} | Job dengan ICD9: {has_icd9} |")
        w("")

    # ── Latency Bucket ──
    if not df_latency.empty:
        w("## Distribusi Latency Bucket")
        w("")
        bins = [0, 60, 120, 180, 240, 300, 360, 600, float("inf")]
        labels = ["<60", "60-120", "120-180", "180-240", "240-300", "300-360", "360-600", ">600"]
        df["latency_bucket"] = pd.cut(
            df["latency_seconds"].dropna(), bins=bins, labels=labels, right=False
        )
        bucket_counts = df["latency_bucket"].value_counts().sort_index()
        w("| Bucket (detik) | Jumlah | Persentase | Grafik |")
        w("|-----------------|--------|------------|--------|")
        for bucket, count in bucket_counts.items():
            bar = "█" * int(count / total * 100)
            w(f"| {bucket} | {count} | {count/total*100:.1f}% | {bar} |")
        w("")

    # ── Kesimpulan ──
    w("## Kesimpulan & Rekomendasi")
    w("")
    w(f"- Semua **{total}** job BERHASIL selesai dari sisi worker pipeline.")
    if with_timeout > 0:
        w(f"- Masalah utama: **{with_timeout}** job ({with_timeout/total*100:.1f}%) mengalami INACBG polling timeout 300 detik.")
        w("  Ini penyebab SIMRS menampilkan _Job Status: Failed_ — karena INACBG tidak merespons.")
        w(f"- Pipeline coding ICD berjalan normal ({success} tanpa timeout, {timeout_then_ok} meski timeout tapi tetap selesai).")
        w("- **Rekomendasi:** Periksa service INACBG / Redis queue INACBG, karena polling timeout konsisten 300s pada SEMUA job.")
    else:
        w("- Tidak ada job yang mengalami timeout. Semua berjalan lancar.")
    w("")

md_content = "\n".join(lines)

with open(OUTPUT_MD, "w") as f:
    f.write(md_content)

print(md_content)
print(f"\n{'=' * 60}")
print(f"Laporan tersimpan ke: {OUTPUT_MD}")
print(f"{'=' * 60}")
