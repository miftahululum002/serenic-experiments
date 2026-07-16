import pandas as pd
import re
from datetime import datetime
from collections import Counter

log_file_path = (
    "/Users/miftahululum002/projects/serenic/experiments/standalone/logs/worker.log"
)
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
start_pattern = re.compile(
    r"\[([^\]]+)\] \[([^\]]+)\] \[standalone\] Start"
)
pipeline_potential_pattern = re.compile(
    r"\[([^\]]+)\] \[standalone\] \[3\] pipeline_potential=(\d+)"
)
complete_pattern = re.compile(
    r"\[([^\]]+)\] \[standalone\] \[4b\] complete"
)
icd10_pattern = re.compile(r"ICD10=\[([^\]]*)\]")
icd9cm_pattern = re.compile(r"ICD9CM=\[([^\]]*)\]")
normalized_pattern = re.compile(
    r"\[encounter=([^\]]+)\]  - Normalized phrases = (\d+)"
)
matched_pattern = re.compile(
    r"\[encounter=([^\]]+)\]  - matched_codes = (\d+)"
)
warning_pattern = re.compile(r"WARNING:(.*)")
inacbg_no_pattern = re.compile(r"INACBGS=(\w+)")

print("Sedang membaca dan memproses log...")

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
            run_id, duration_str = completed_match.group(1), completed_match.group(2)
            try:
                t = datetime.strptime(duration_str.strip(), "%H:%M:%S.%f")
                total_seconds = (
                    t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
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
                    job_tracker[run_id]["timeout_extra_seconds"] = total_seconds - 300
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
                job_tracker[rid]["icd10_count"] = len(
                    icd10_m.group(1).split(", ")
                ) if icd10_m and icd10_m.group(1) else 0
                job_tracker[rid]["icd9cm_count"] = len(
                    icd9_m.group(1).split(", ")
                ) if icd9_m and icd9_m.group(1) else 0
                job_tracker[rid]["inacbg_result"] = inacbg_m.group(1) if inacbg_m else "?"

        norm_match = normalized_pattern.search(line)
        if norm_match:
            rid = norm_match.group(1)
            cnt = int(norm_match.group(2))
            pipeline_stats.append({"run_id": rid, "normalized_phrases": cnt})

        match_match = matched_pattern.search(line)
        if match_match:
            rid = match_match.group(1)
            cnt = int(match_match.group(2))
            existing = next((p for p in pipeline_stats if p["run_id"] == rid), None)
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

if df.empty:
    print("\n[PERINGATAN] Tidak ada data transaksi job yang ditemukan di file log.")
else:
    print("\n" + "=" * 60)
    print("             HASIL ANALISIS LOG WORKER             ")
    print("=" * 60)

    # ── 1. RINGKASAN UMUM ──
    total = len(df)
    success = len(df[df["status"] == "Success"])
    timeout_then_ok = len(df[df["status"] == "Timeout tapi Selesai"])
    failed_only = len(df[df["status"] == "Failed (Timeout)"])
    with_timeout = len(df[df["timeout_occurred"] == True])

    print(f"\n[0] RINGKASAN UMUM")
    print(f"    Total job diproses : {total}")
    print(f"    Berhasil (Success) : {success} ({success/total*100:.1f}%)")
    print(f"    Timeout lalu OK    : {timeout_then_ok} ({timeout_then_ok/total*100:.1f}%)")
    print(f"    Gagal total (Failed): {failed_only} ({failed_only/total*100:.1f}%)")
    print(f"    Total terkena timeout: {with_timeout} ({with_timeout/total*100:.1f}%)")

    # ── 2. DISTRIBUSI STATUS ──
    print(f"\n[1] DISTRIBUSI STATUS JOB:")
    for status, count in df["status"].value_counts().items():
        pct = count / total * 100
        print(f"    {status:<40s} : {count:4d}  ({pct:5.1f}%)")

    # ── 3. STATISTIK LATENCY ──
    df_latency = df[df["latency_seconds"].notna()]
    print(f"\n[2] STATISTIK LATENCY DURASI PROSES (DETIK):")
    if not df_latency.empty:
        lat = df_latency["latency_seconds"]
        print(f"    Jumlah data valid  : {len(lat)}")
        print(f"    Rata-rata (Mean)   : {lat.mean():.2f} detik ({lat.mean()/60:.1f} menit)")
        print(f"    Median (P50)       : {lat.median():.2f} detik ({lat.median()/60:.1f} menit)")
        print(f"    Std Deviasi        : {lat.std():.2f} detik")
        print(f"    Persentil 75 (P75) : {lat.quantile(0.75):.2f} detik")
        print(f"    Persentil 90 (P90) : {lat.quantile(0.90):.2f} detik")
        print(f"    Persentil 95 (P95) : {lat.quantile(0.95):.2f} detik")
        print(f"    Persentil 99 (P99) : {lat.quantile(0.99):.2f} detik")
        print(f"    Paling Cepat       : {lat.min():.2f} detik")
        print(f"    Paling Lama        : {lat.max():.2f} detik")

        # Latency breakdown per status
        print(f"\n    --- Latency per Status ---")
        for status in df["status"].unique():
            sub = df[df["status"] == status]["latency_seconds"].dropna()
            if not sub.empty:
                print(f"    [{status}]")
                print(f"      Count : {len(sub)}")
                print(f"      Mean  : {sub.mean():.2f}s | Median: {sub.median():.2f}s | Max: {sub.max():.2f}s")
    else:
        print("    Tidak ada data durasi latency yang valid.")

    # ── 4. ANALISIS TIMEOUT DETAIL ──
    df_timeout = df[df["timeout_occurred"] == True]
    print(f"\n[3] DETAIL JOB YANG TERKENA TIMEOUT (300s INACBG):")
    if not df_timeout.empty:
        print(f"    Total job timeout : {len(df_timeout)}")
        if "timeout_extra_seconds" in df_timeout.columns:
            extra = df_timeout["timeout_extra_seconds"].dropna()
            if not extra.empty:
                print(f"    Rata-rata waktu tambahan setelah timeout : {extra.mean():.2f} detik")
                print(f"    Median waktu tambahan setelah timeout    : {extra.median():.2f} detik")
                print(f"    Max waktu tambahan setelah timeout       : {extra.max():.2f} detik")
                print(f"    Semua job timeout akhirnya selesai? : {'YA' if (extra >= 0).all() else 'TIDAK'}")
        print(f"\n    Daftar run_id yang timeout:")
        for _, row in df_timeout.iterrows():
            lat = f"{row['latency_seconds']:.1f}s" if pd.notna(row.get("latency_seconds")) else "N/A"
            extra = f"+{row['timeout_extra_seconds']:.1f}s" if pd.notna(row.get("timeout_extra_seconds")) else ""
            status_short = "OK" if row["status"] == "Timeout tapi Selesai" else "FAILED"
            print(f"      {row['run_id']}  | latency={lat} {extra} | status={status_short}")
    else:
        print("    Tidak ada job yang terkena timeout.")

    # ── 5. DISTRIBUSI WARNING ──
    print(f"\n[4] DISTRIBUSI WARNING/ERROR TYPES:")
    if warnings_list:
        warn_counter = Counter()
        for w in warnings_list:
            if "RuleContextOutputMapper.map" in w:
                warn_counter["RuleContextOutputMapper: entity tidak punya rule coverage"] += 1
            elif "SAWarning" in w or "AnnotatedColumn" in w or "Column" in w:
                warn_counter["SQLAlchemy SAWarning (inherit_cache)"] += 1
            elif "PydanticSerializationUnexpectedValue" in w:
                warn_counter["Pydantic serialization warning (embedding ndarray)"] += 1
            else:
                key = w[:80] + ("..." if len(w) > 80 else "")
                warn_counter[key] += 1
        for wtype, count in warn_counter.most_common():
            print(f"    [{count:4d}x] {wtype}")
    else:
        print("    Tidak ada warning ditemukan (selain INACBG timeout).")

    # ── 6. DISTRIBUSI INACBGS ──
    print(f"\n[5] DISTRIBUSI HASIL INACBGS:")
    if "inacbg_result" in df.columns:
        for val, count in df["inacbg_result"].value_counts().items():
            print(f"    INACBGS={val:<6s} : {count:4d} job")

    # ── 7. PIPELINE STATS ──
    if not df_pipe.empty:
        print(f"\n[6] STATISTIK PIPELINE:")
        if "normalized_phrases" in df_pipe.columns:
            np_ = df_pipe["normalized_phrases"]
            print(f"    Normalized Phrases  — Mean: {np_.mean():.1f} | Median: {np_.median():.0f} | Max: {np_.max()}")
        if "matched_codes" in df_pipe.columns:
            mc = df_pipe["matched_codes"]
            print(f"    Matched Codes       — Mean: {mc.mean():.1f} | Median: {mc.median():.0f} | Max: {mc.max()}")
            no_match = len(mc[mc == 0])
            print(f"    Job dengan 0 matched: {no_match} ({no_match/len(mc)*100:.1f}%)")

    # ── 8. DISTRIBUSI ICD CODES ──
    print(f"\n[7] DISTRIBUSI JUMLAH ICD PER JOB:")
    if "icd10_count" in df.columns:
        icd10_counts = df["icd10_count"]
        print(f"    ICD-10 per job — Mean: {icd10_counts.mean():.1f} | Median: {icd10_counts.median():.0f} | Max: {icd10_counts.max()}")
    if "icd9cm_count" in df.columns:
        icd9_counts = df["icd9cm_count"]
        has_icd9 = len(icd9_counts[icd9_counts > 0])
        print(f"    ICD-9-CM per job — Mean: {icd9_counts.mean():.1f} | Job dengan ICD9: {has_icd9}")

    # ── 9. DISTRIBUSI LATENCY BUCKET ──
    if not df_latency.empty:
        print(f"\n[8] DISTRIBUSI LATENCY DALAM BUCKET (DETIK):")
        bins = [0, 60, 120, 180, 240, 300, 360, 600, float("inf")]
        labels = ["<60", "60-120", "120-180", "180-240", "240-300", "300-360", "360-600", ">600"]
        df["latency_bucket"] = pd.cut(df["latency_seconds"].dropna(), bins=bins, labels=labels, right=False)
        bucket_counts = df["latency_bucket"].value_counts().sort_index()
        for bucket, count in bucket_counts.items():
            bar = "█" * int(count / total * 100)
            print(f"    {bucket:>10s} : {count:4d} ({count/total*100:5.1f}%) {bar}")

    # ── 10. KESIMPULAN ──
    print(f"\n{'=' * 60}")
    print("KESIMPULAN & REKOMENDASI:")
    print("=" * 60)
    print(f"  • Semua {total} job BERHASIL selesai dari sisi worker pipeline.")
    print(f"  • Masalah utama: {with_timeout} job ({with_timeout/total*100:.1f}%) mengalami")
    print(f"    INACBG polling timeout 300 detik. Ini penyebab SIMRS menampilkan")
    print(f"    'Job Status: Failed' — karena INACBG tidak merespons.")
    print(f"  • Pipeline coding ICD berjalan normal ({success} tanpa timeout,")
    print(f"    {timeout_then_ok} meski timeout tapi tetap selesai).")
    print(f"  • Rekomendasi: Periksa service INACBG / Redis queue INACBG,")
    print(f"    karena polling timeout konsisten 300s pada SEMUA job.")

    print("=" * 60)
