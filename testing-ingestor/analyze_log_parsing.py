import argparse
import json
import re
from pathlib import Path
from datetime import datetime


def parse_timestamp(ts_str):
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


def analyze_log(filepath):
    fp = Path(filepath)
    if not fp.exists():
        print(f"File not found: {filepath}")
        return {}

    start_pattern = re.compile(
        r"^(\S+)\s+INFO:.*Processing encounter update:\s+(\S+)\s+\|\s+Org:\s+(\S+)"
    )
    end_pattern = re.compile(
        r"^(\S+)\s+INFO:.*Completed processing encounter update:\s+(\S+)\s+\|\s+Org:\s+(\S+)"
    )

    starts = {}
    ends = {}
    org_map = {}

    with open(fp) as f:
        for line in f:
            m = start_pattern.match(line)
            if m:
                ts_str, noreg, org = m.group(1), m.group(2), m.group(3)
                starts[noreg] = parse_timestamp(ts_str)
                org_map[noreg] = org
                continue

            m = end_pattern.match(line)
            if m:
                ts_str, noreg, org = m.group(1), m.group(2), m.group(3)
                ends[noreg] = parse_timestamp(ts_str)

    results = {}
    for noreg in starts:
        start = starts[noreg]
        end = ends.get(noreg)
        if end:
            duration = (end - start).total_seconds()
        else:
            duration = None
        results[noreg] = {
            "noregistrasi": noreg,
            "organization": org_map.get(noreg, ""),
            "start": start,
            "end": end,
            "duration_seconds": duration,
        }

    return results


def load_payload(filepath):
    fp = Path(filepath)
    if not fp.exists():
        print(f"Payload file not found: {filepath}")
        return {}

    with open(fp) as f:
        data = json.load(f)

    if not isinstance(data, list):
        data = [data]

    results = {}
    for item in data:
        noreg = item.get("noregistrasi")
        if noreg:
            size_bytes = len(json.dumps(item))
            results[noreg] = {
                "noregistrasi": noreg,
                "size_bytes": size_bytes,
                "size_kb": size_bytes / 1024,
                "size_mb": size_bytes / (1024 * 1024),
            }

    return results


def format_size(bytes_val):
    if bytes_val >= 1024 * 1024:
        return f"{bytes_val/(1024*1024):.2f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val/1024:.2f} KB"
    else:
        return f"{bytes_val:.0f} B"


def analyze(log_file=None, payload_file=None):
    log_results = {}
    payload_results = {}

    if log_file:
        log_results = analyze_log(log_file)

    if payload_file:
        payload_results = load_payload(payload_file)

    all_noregs = sorted(set(list(log_results.keys()) + list(payload_results.keys())))

    lines = []

    def p(line=""):
        print(line)
        lines.append(line)

    p("=" * 90)
    if log_file:
        p(f"Log File     : {log_file}")
    if payload_file:
        p(f"Payload File : {payload_file}")
    p(f"Generated    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"Total Records: {len(all_noregs)}")
    p("=" * 90)

    has_log = bool(log_results)
    has_payload = bool(payload_results)

    header = f"{'No':<4} {'Noregistrasi':<16}"
    separator = f"{'-'*4} {'-'*16}"

    if has_payload:
        header += f" {'Payload Size':<14}"
        separator += f" {'-'*14}"

    if has_log:
        header += f" {'Start':<22} {'End':<22} {'Duration (s)':<14} {'Duration (min)':<14}"
        separator += f" {'-'*22} {'-'*22} {'-'*14} {'-'*14}"

    p(f"\n{header}")
    p(separator)

    total_duration = 0
    total_payload_bytes = 0
    completed = 0

    for i, noreg in enumerate(all_noregs, 1):
        row = f"{i:<4} {noreg:<16}"

        if has_payload:
            pdata = payload_results.get(noreg)
            if pdata:
                row += f" {format_size(pdata['size_bytes']):<14}"
                total_payload_bytes += pdata["size_bytes"]
            else:
                row += f" {'N/A':<14}"

        if has_log:
            ldata = log_results.get(noreg)
            if ldata:
                start_str = ldata["start"].strftime("%Y-%m-%d %H:%M:%S")
                end_str = ldata["end"].strftime("%Y-%m-%d %H:%M:%S") if ldata["end"] else "N/A"
                if ldata["duration_seconds"] is not None:
                    dur_s = f"{ldata['duration_seconds']:.1f}"
                    dur_m = f"{ldata['duration_seconds']/60:.2f}"
                    total_duration += ldata["duration_seconds"]
                    completed += 1
                else:
                    dur_s = "N/A"
                    dur_m = "N/A"
                row += f" {start_str:<22} {end_str:<22} {dur_s:<14} {dur_m:<14}"
            else:
                row += f" {'N/A':<22} {'N/A':<22} {'N/A':<14} {'N/A':<14}"

        p(row)

    p(f"\n{'=' * 90}")
    p("SUMMARY")
    p(f"{'=' * 90}")

    if has_payload:
        p(f"\n  Payload:")
        p(f"    Total size        : {format_size(total_payload_bytes)}")
        p(f"    Average per record: {format_size(total_payload_bytes / len(all_noregs)) if all_noregs else 'N/A'}")

    if has_log:
        log_count = len(log_results)
        durations = [v["duration_seconds"] for v in log_results.values() if v["duration_seconds"] is not None]
        p(f"\n  Latency:")
        p(f"    Total encounters  : {log_count}")
        p(f"    Completed         : {completed}")
        p(f"    Incomplete        : {log_count - completed}")
        if durations:
            avg_s = sum(durations) / len(durations)
            p(f"    Total duration    : {total_duration:.1f}s ({total_duration/60:.2f} min)")
            p(f"    Average           : {avg_s:.1f}s ({avg_s/60:.2f} min)")
            p(f"    Min               : {min(durations):.1f}s ({min(durations)/60:.2f} min)")
            p(f"    Max               : {max(durations):.1f}s ({max(durations)/60:.2f} min)")

    p(f"{'=' * 90}")

    # Write markdown report
    report_dir = Path("report") / "analysis"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = report_dir / f"parsing_analysis_{timestamp}.md"

    md_lines = [
        "# Parsing Analysis Report",
        "",
        f"- **Log File**: `{log_file}`" if log_file else "",
        f"- **Payload File**: `{payload_file}`" if payload_file else "",
        f"- **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Total Records**: {len(all_noregs)}",
        "",
        "## Details",
        "",
    ]

    header_md = "| No | Noregistrasi |"
    separator_md = "|----|--------------|"
    if has_payload:
        header_md += " Payload Size |"
        separator_md += "--------------|"
    if has_log:
        header_md += " Start | End | Duration (s) | Duration (min) |"
        separator_md += "-------|-----|--------------|----------------|"

    md_lines.append(header_md)
    md_lines.append(separator_md)

    for i, noreg in enumerate(all_noregs, 1):
        row = f"| {i} | {noreg} |"
        if has_payload:
            pdata = payload_results.get(noreg)
            row += f" {format_size(pdata['size_bytes']) if pdata else 'N/A'} |"
        if has_log:
            ldata = log_results.get(noreg)
            if ldata:
                start_str = ldata["start"].strftime("%Y-%m-%d %H:%M:%S")
                end_str = ldata["end"].strftime("%Y-%m-%d %H:%M:%S") if ldata["end"] else "N/A"
                dur_s = f"{ldata['duration_seconds']:.1f}" if ldata["duration_seconds"] is not None else "N/A"
                dur_m = f"{ldata['duration_seconds']/60:.2f}" if ldata["duration_seconds"] is not None else "N/A"
                row += f" {start_str} | {end_str} | {dur_s} | {dur_m} |"
            else:
                row += f" N/A | N/A | N/A | N/A |"
        md_lines.append(row)

    md_lines.extend(["", "## Summary", ""])

    if has_payload:
        md_lines.extend([
            "### Payload",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total size | {format_size(total_payload_bytes)} |",
            f"| Average per record | {format_size(total_payload_bytes / len(all_noregs)) if all_noregs else 'N/A'} |",
            "",
        ])

    if has_log:
        durations = [v["duration_seconds"] for v in log_results.values() if v["duration_seconds"] is not None]
        md_lines.extend([
            "### Latency",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total encounters | {len(log_results)} |",
            f"| Completed | {completed} |",
            f"| Incomplete | {len(log_results) - completed} |",
        ])
        if durations:
            avg_s = sum(durations) / len(durations)
            md_lines.extend([
                f"| Total duration | {total_duration:.1f}s ({total_duration/60:.2f} min) |",
                f"| Average | {avg_s:.1f}s ({avg_s/60:.2f} min) |",
                f"| Min | {min(durations):.1f}s ({min(durations)/60:.2f} min) |",
                f"| Max | {max(durations):.1f}s ({max(durations)/60:.2f} min) |",
            ])

    with open(md_path, "w") as f:
        f.write("\n".join([l for l in md_lines if l is not None]))

    p(f"\nMarkdown report saved to: {md_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze container log and payload for parsing latency")
    parser.add_argument("--file", type=str, default=None, help="Path to container.log")
    parser.add_argument("--payload", type=str, default=None, help="Path to payload JSON file")
    args = parser.parse_args()

    if not args.file and not args.payload:
        parser.error("At least one of --file or --payload is required")

    analyze(log_file=args.file, payload_file=args.payload)
