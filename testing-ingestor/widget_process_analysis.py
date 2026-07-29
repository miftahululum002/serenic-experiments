import json
import re
from pathlib import Path
from datetime import datetime


def parse_timestamp(ts_str):
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


def format_duration(seconds):
    if seconds is None:
        return "N/A"
    if seconds >= 60:
        return f"{seconds:.1f}s ({seconds/60:.2f} min)"
    return f"{seconds:.1f}s"


def format_size(bytes_val):
    if bytes_val >= 1024 * 1024:
        return f"{bytes_val/(1024*1024):.2f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val/1024:.2f} KB"
    else:
        return f"{bytes_val:.0f} B"


def analyze_widget(widget_id, base_dir="output"):
    base = Path(base_dir)
    log_file = base / f"widget_{widget_id}.log"
    json_file = base / f"widget_{widget_id}.json"

    start_pattern = re.compile(
        r"^(\S+)\s+INFO:.*Processing encounter update:\s+(\S+)\s+\|\s*Org:\s+(\S+)"
    )
    end_pattern = re.compile(
        r"^(\S+)\s+INFO:.*Completed processing encounter update:\s+(\S+)\s+\|\s*Org:\s+(\S+)"
    )
    job_duration_pattern = re.compile(r"job in (\d+):(\d+):(\d+)\.(\d+)s")

    print(f"{'=' * 70}")
    print(f"  Widget Analysis: {widget_id}")
    print(f"{'=' * 70}")

    # --- Parse Log ---
    start_time = None
    end_time = None
    org = None
    noreg = None
    job_duration = None
    job_start_time = None

    if log_file.exists():
        with open(log_file) as f:
            for line in f:
                m = start_pattern.match(line)
                if m:
                    ts_str, noreg, org = m.group(1), m.group(2), m.group(3)
                    start_time = parse_timestamp(ts_str)
                    continue

                m = end_pattern.match(line)
                if m:
                    ts_str, noreg, org = m.group(1), m.group(2), m.group(3)
                    end_time = parse_timestamp(ts_str)
                    continue

                m = job_duration_pattern.search(line)
                if m:
                    h, m_, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    job_duration = h * 3600 + m_ * 60 + s + ms / 1_000_000
                    continue

                if line.startswith("2026-07-29T08:40:28"):
                    if job_start_time is None:
                        job_start_time = parse_timestamp(line[:30])

        print(f"\n  Log File: {log_file}")

        if start_time and end_time:
            parse_duration = (end_time - start_time).total_seconds()
            print(f"\n  Encounter Processing:")
            print(f"    Start    : {start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            print(f"    End      : {end_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            print(f"    Duration : {format_duration(parse_duration)}")
        else:
            print(f"\n  Encounter Processing: INCOMPLETE")
            if start_time:
                print(f"    Start    : {start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
                print(f"    End      : N/A (no completion log)")
            else:
                print(f"    Start/End: Not found in log")

        if job_duration:
            print(f"\n  Total Job Duration (RQ Worker): {format_duration(job_duration)}")

        if job_start_time:
            overall_duration = (end_time or datetime.now()) - job_start_time
            job_end = end_time or datetime.now()
            print(f"\n  Overall Process:")
            print(f"    First log : {job_start_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            print(f"    Last log  : {job_end.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
            print(f"    Wall time : {format_duration(overall_duration.total_seconds())}")

        if noreg and org:
            print(f"\n  Metadata:")
            print(f"    Noregistrasi : {noreg}")
            print(f"    Organization : {org}")
    else:
        print(f"\n  Log file not found: {log_file}")

    # --- Parse Payload ---
    if json_file.exists():
        with open(json_file) as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = [data]

        total_items = len(data)
        bytes_total = len(json.dumps(data))
        key_count = sum(len(item.keys()) if isinstance(item, dict) else 0 for item in data)

        print(f"\n  Payload File: {json_file}")
        print(f"    Total records : {total_items}")
        print(f"    Total keys    : {key_count}")
        print(f"    Size          : {format_size(bytes_total)}")
    else:
        print(f"\n  Payload file not found: {json_file}")

    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Analyze widget processing log and payload")
    parser.add_argument("widget_id", help="Widget ID (e.g. 477896)")
    parser.add_argument("--dir", default="output", help="Base directory for widget files")
    args = parser.parse_args()
    analyze_widget(args.widget_id, args.dir)
