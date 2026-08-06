import argparse
import math
import re
from pathlib import Path
from datetime import datetime


def parse_timestamp(ts_str):
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


def percentile(sorted_values, p):
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, math.ceil(len(sorted_values) * p / 100) - 1)
    return sorted_values[index]


def analyze_log(filepath):
    fp = Path(filepath)
    if not fp.exists():
        print(f"File not found: {filepath}")
        return {}

    start_pattern = re.compile(
        r"^(\S+)\s+INFO:.*?\[Worker (\d+)\] \[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\] Start Processing INACBGS"
    )
    saved_pattern = re.compile(
        r"^(\S+)\s+INFO:.*?\[Worker (\d+)\] \[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\] INACBGS saved \(set=(\w+)\)"
    )
    add_pattern = re.compile(
        r"Added task for org ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}), encounter ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    )
    org_pattern = re.compile(
        r"\[Worker \d+\] \[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\] Managing Organization ID: ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    )

    pending = {}
    org_map = {}
    events = []

    with open(fp) as f:
        for line in f:
            m = add_pattern.search(line)
            if m:
                org_map[m.group(2)] = m.group(1)

            m = org_pattern.search(line)
            if m:
                org_map[m.group(1)] = m.group(2)

            m = start_pattern.match(line)
            if m:
                ts, worker, encounter = m.group(1), m.group(2), m.group(3)
                pending.setdefault(encounter, []).append(
                    (parse_timestamp(ts), f"worker-{worker}", "", "")
                )
                continue

            m = saved_pattern.match(line)
            if m:
                ts_str, worker, encounter, set_type = (
                    m.group(1),
                    m.group(2),
                    m.group(3),
                    m.group(4),
                )
                ts = parse_timestamp(ts_str)
                queue = pending.get(encounter, [])
                if queue:
                    start_ts, start_host, start_server, start_address = queue.pop(0)
                    start_ip, start_port = split_address(start_address)
                    events.append(
                        {
                            "encounter": encounter,
                            "organization": org_map.get(encounter, ""),
                            "host": start_host,
                            "server": start_server,
                            "ip": start_ip,
                            "port": start_port,
                            "set": set_type,
                            "start": start_ts,
                            "end": ts,
                            "duration_seconds": (ts - start_ts).total_seconds(),
                        }
                    )

    return events


def host_label(e):
    if e.get("server"):
        return f"{e['host']} ({e['server']})"
    return e["host"]


def split_address(addr):
    if not addr:
        return "", ""
    ip, _, port = addr.rpartition(":")
    return ip, port


def compute_rps(events):
    if not events:
        return {}, 0.0, 0.0, 0.0
    starts = sorted(e["start"] for e in events)
    ends = sorted(e["end"] for e in events)
    wall_clock = (ends[-1] - starts[0]).total_seconds()
    overall = len(events) / wall_clock if wall_clock > 0 else 0.0
    per_second = {}
    for e in events:
        bucket = e["start"].replace(microsecond=0)
        per_second[bucket] = per_second.get(bucket, 0) + 1
    counts = sorted(per_second.values())
    peak = counts[-1]
    active = len(counts)
    avg = len(events) / active if active else 0.0
    return per_second, overall, peak, avg


def print_summary(events):
    durations = [e["duration_seconds"] for e in events]
    print("  Latency (per processing cycle):")
    print(f"    Total cycles      : {len(events)}")
    print(f"    Unique encounters : {len(set(e['encounter'] for e in events))}")
    print(f"    Hosts             : {sorted(set(host_label(e) for e in events))}")
    print(
        f"    Servers           : {sorted(set(e['server'] for e in events if e['server']))}"
    )
    print(f"    Pass types        : {sorted(set(e['set'] for e in events))}")
    if durations:
        avg_s = sum(durations) / len(durations)
        sorted_durations = sorted(durations)
        std_s = (sum((d - avg_s) ** 2 for d in durations) / len(durations)) ** 0.5
        p50 = percentile(sorted_durations, 50)
        p90 = percentile(sorted_durations, 90)
        p95 = percentile(sorted_durations, 95)
        p99 = percentile(sorted_durations, 99)
        print(
            f"    Total duration    : {sum(durations):.1f}s ({sum(durations)/60:.2f} min)"
        )
        print(f"    Average           : {avg_s:.1f}s")
        print(f"    Std dev           : {std_s:.1f}s")
        print(f"    Median (p50)      : {p50:.1f}s")
        print(f"    p90               : {p90:.1f}s")
        print(f"    p95               : {p95:.1f}s")
        print(f"    p99               : {p99:.1f}s")
        print(f"    Min               : {min(durations):.1f}s")
        print(f"    Max               : {max(durations):.1f}s")

    rps_buckets, rps_overall, rps_peak, rps_avg = compute_rps(events)
    if events:
        print("  Throughput (requests/second):")
        print(f"    Overall RPS      : {rps_overall:.1f} req/s")
        print(f"    Active seconds   : {len(rps_buckets)}")
        print(f"    Avg (active) RPS : {rps_avg:.1f} req/s")
        print(f"    Peak RPS         : {rps_peak} req/s")

    sets = sorted(set(e["set"] for e in events))
    if len(sets) > 1:
        print("\n  Per pass type:")
        print(
            f"    {'Set':<12} {'Cycles':<8} {'Avg (s)':<10} {'Min (s)':<10} {'Max (s)':<10} {'p95 (s)':<10}"
        )
        for st in sets:
            sd = sorted(e["duration_seconds"] for e in events if e["set"] == st)
            n = len(sd)
            if n:
                avg = sum(sd) / n
                print(
                    f"    {st:<12} {n:<8} {avg:<10.1f} {min(sd):<10.1f} {max(sd):<10.1f} {percentile(sd, 95):<10.1f}"
                )

    print("\n  Per host:")
    hosts = sorted(set(e["host"] for e in events))
    if events:
        print(
            f"    {'Host':<12} {'Server':<22} {'IP':<22} {'Port':<6} {'Cycles':<8} {'Avg (s)':<10} {'Min (s)':<10} {'Max (s)':<10} {'p95 (s)':<10}"
        )
        for host in hosts:
            hd = sorted(e["duration_seconds"] for e in events if e["host"] == host)
            n = len(hd)
            if n:
                avg = sum(hd) / n
                servers = sorted(
                    set(
                        e["server"] for e in events if e["host"] == host and e["server"]
                    )
                )
                ips = sorted(
                    set(e["ip"] for e in events if e["host"] == host and e["ip"])
                )
                ports = sorted(
                    set(e["port"] for e in events if e["host"] == host and e["port"])
                )
                print(
                    f"    {host:<12} {','.join(servers) or '-':<22} {','.join(ips) or '-':<22} {','.join(ports) or '-':<6} {n:<8} {avg:<10.1f} {min(hd):<10.1f} {max(hd):<10.1f} {percentile(hd, 95):<10.1f}"
                )

    print("\n  Per server:")
    server_groups = sorted(set((e["server"], e["port"]) for e in events if e["server"]))
    if events:
        print(
            f"    {'Server':<22} {'Port':<6} {'Hosts':<20} {'IP':<22} {'Cycles':<8} {'Avg (s)':<10} {'Min (s)':<10} {'Max (s)':<10} {'p95 (s)':<10}"
        )
        for server, port in server_groups:
            sd = sorted(
                e["duration_seconds"]
                for e in events
                if e["server"] == server and e["port"] == port
            )
            n = len(sd)
            if n:
                avg = sum(sd) / n
                hosts = sorted(
                    set(
                        e["host"]
                        for e in events
                        if e["server"] == server and e["port"] == port
                    )
                )
                ips = sorted(
                    set(
                        e["ip"]
                        for e in events
                        if e["server"] == server and e["port"] == port and e["ip"]
                    )
                )
                print(
                    f"    {server:<22} {port:<6} {','.join(hosts):<20} {','.join(ips) or '-':<22} {n:<8} {avg:<10.1f} {min(sd):<10.1f} {max(sd):<10.1f} {percentile(sd, 95):<10.1f}"
                )


def analyze(filepath, count=0):
    events = analyze_log(filepath)
    events.sort(key=lambda e: e["start"])
    if count:
        events = events[:count]

    lines = []

    def p(line=""):
        print(line)
        lines.append(line)

    p("=" * 120)
    p(f"Log File     : {filepath}")
    p(f"Generated    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"Total Cycles : {len(events)}")
    p(f"Unique Enc   : {len(set(e['encounter'] for e in events))}")
    p(f"Hosts        : {sorted(set(host_label(e) for e in events))}")
    p(f"Servers      : {sorted(set(e['server'] for e in events if e['server']))}")
    p("=" * 120)

    header = (
        f"{'No':<5} {'Encounter ID':<38} {'Org ID':<38} {'Host':<12} {'Server':<22} {'IP':<22} {'Port':<6} {'Set':<10}"
        f" {'Start':<22} {'End':<22} {'Duration (s)':<14}"
    )
    separator = (
        f"{'-'*5} {'-'*38} {'-'*38} {'-'*12} {'-'*22} {'-'*22} {'-'*6} {'-'*10}"
        f" {'-'*22} {'-'*22} {'-'*14}"
    )
    p(f"\n{header}")
    p(separator)

    for i, e in enumerate(events, 1):
        row = (
            f"{i:<5} {e['encounter']:<38} {e['organization']:<38} {e['host']:<12} {e['server'] or '-':<22}"
            f" {e['ip'] or '-':<22} {e['port'] or '-':<6} {e['set']:<10}"
            f" {e['start'].strftime('%Y-%m-%d %H:%M:%S'):<22}"
            f" {e['end'].strftime('%Y-%m-%d %H:%M:%S'):<22}"
            f" {e['duration_seconds']:<14.1f}"
        )
        p(row)

    p(f"\n{'=' * 120}")
    p("SUMMARY")
    p(f"{'=' * 120}")
    print_summary(events)

    report_dir = Path("output/eklaim")
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = report_dir / f"eklaim_scale_analysis_{timestamp}.md"

    md_lines = [
        "# EKlaim Scale Analysis Report",
        "",
        f"- **Log File**: `{filepath}`",
        f"- **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Total Cycles**: {len(events)}",
        f"- **Unique Encounters**: {len(set(e['encounter'] for e in events))}",
        f"- **Hosts**: {', '.join(sorted(set(host_label(e) for e in events)))}",
        f"- **Servers**: {', '.join(sorted(set(e['server'] for e in events if e['server'])))}",
        "",
        "## Details",
        "",
        "| No | Encounter ID | Org ID | Host | Server | IP | Port | Set | Start | End | Duration (s) |",
        "|----|--------------|--------|------|--------|----|------|-----|-------|-----|--------------|",
    ]

    for i, e in enumerate(events, 1):
        md_lines.append(
            f"| {i} | {e['encounter']} | {e['organization']} | {e['host']} | {e['server'] or '-'} | {e['ip'] or '-'} | {e['port'] or '-'} | {e['set']} |"
            f" {e['start'].strftime('%Y-%m-%d %H:%M:%S')} |"
            f" {e['end'].strftime('%Y-%m-%d %H:%M:%S')} |"
            f" {e['duration_seconds']:.1f} |"
        )

    md_lines.extend(["", "## Summary", "", "### Latency (per processing cycle)", ""])
    durations = [e["duration_seconds"] for e in events]
    if durations:
        avg_s = sum(durations) / len(durations)
        sorted_durations = sorted(durations)
        std_s = (sum((d - avg_s) ** 2 for d in durations) / len(durations)) ** 0.5
        md_lines.extend(
            [
                "| Metric | Value |",
                "|--------|-------|",
                f"| Total cycles | {len(events)} |",
                f"| Unique encounters | {len(set(e['encounter'] for e in events))} |",
                f"| Total duration | {sum(durations):.1f}s ({sum(durations)/60:.2f} min) |",
                f"| Average | {avg_s:.1f}s |",
                f"| Std dev | {std_s:.1f}s |",
                f"| Median (p50) | {percentile(sorted_durations, 50):.1f}s |",
                f"| p90 | {percentile(sorted_durations, 90):.1f}s |",
                f"| p95 | {percentile(sorted_durations, 95):.1f}s |",
                f"| p99 | {percentile(sorted_durations, 99):.1f}s |",
                f"| Min | {min(durations):.1f}s |",
                f"| Max | {max(durations):.1f}s |",
            ]
        )

    rps_buckets, rps_overall, rps_peak, rps_avg = compute_rps(events)
    if events:
        md_lines.extend(["", "### Throughput (requests/second)", ""])
        md_lines.extend(
            [
                "| Metric | Value |",
                "|--------|-------|",
                f"| Overall RPS | {rps_overall:.1f} req/s |",
                f"| Active seconds | {len(rps_buckets)} |",
                f"| Avg (active) RPS | {rps_avg:.1f} req/s |",
                f"| Peak RPS | {rps_peak} req/s |",
            ]
        )
        md_lines.extend(["", "### RPS per Second", ""])
        md_lines.extend(["| Time | RPS |", "|------|-----|"])
        for ts in sorted(rps_buckets):
            md_lines.append(
                f"| {ts.strftime('%Y-%m-%d %H:%M:%S')} | {rps_buckets[ts]} |"
            )

    sets = sorted(set(e["set"] for e in events))
    if len(sets) > 1:
        md_lines.extend(["", "### Per Pass Type", ""])
        md_lines.extend(
            [
                "| Set | Cycles | Avg (s) | Min (s) | Max (s) | p95 (s) |",
                "|-----|--------|---------|---------|---------|---------|",
            ]
        )
        for st in sets:
            sd = sorted(e["duration_seconds"] for e in events if e["set"] == st)
            n = len(sd)
            if n:
                avg = sum(sd) / n
                md_lines.append(
                    f"| {st} | {n} | {avg:.1f} | {min(sd):.1f} | {max(sd):.1f} |"
                    f" {percentile(sd, 95):.1f} |"
                )

    md_lines.extend(["", "### Per Host", ""])
    hosts = sorted(set(e["host"] for e in events))
    if hosts:
        md_lines.extend(
            [
                "| Host | Server | IP | Port | Cycles | Avg (s) | Min (s) | Max (s) | p95 (s) |",
                "|------|--------|----|------|--------|---------|---------|---------|---------|",
            ]
        )
        for host in hosts:
            hd = sorted(e["duration_seconds"] for e in events if e["host"] == host)
            n = len(hd)
            if n:
                avg = sum(hd) / n
                servers = sorted(
                    set(
                        e["server"] for e in events if e["host"] == host and e["server"]
                    )
                )
                ips = sorted(
                    set(e["ip"] for e in events if e["host"] == host and e["ip"])
                )
                ports = sorted(
                    set(e["port"] for e in events if e["host"] == host and e["port"])
                )
                md_lines.append(
                    f"| {host} | {', '.join(servers) or '-'} | {', '.join(ips) or '-'} | {', '.join(ports) or '-'} | {n} |"
                    f" {avg:.1f} | {min(hd):.1f} | {max(hd):.1f} | {percentile(hd, 95):.1f} |"
                )

    md_lines.extend(["", "### Per Server", ""])
    server_groups = sorted(set((e["server"], e["port"]) for e in events if e["server"]))
    if server_groups:
        md_lines.extend(
            [
                "| Server | Port | Hosts | IP | Cycles | Avg (s) | Min (s) | Max (s) | p95 (s) |",
                "|--------|------|-------|----|--------|---------|---------|---------|---------|",
            ]
        )
        for server, port in server_groups:
            sd = sorted(
                e["duration_seconds"]
                for e in events
                if e["server"] == server and e["port"] == port
            )
            n = len(sd)
            if n:
                avg = sum(sd) / n
                hosts = sorted(
                    set(
                        e["host"]
                        for e in events
                        if e["server"] == server and e["port"] == port
                    )
                )
                ips = sorted(
                    set(
                        e["ip"]
                        for e in events
                        if e["server"] == server and e["port"] == port and e["ip"]
                    )
                )
                md_lines.append(
                    f"| {server} | {port} | {', '.join(hosts)} | {', '.join(ips) or '-'} | {n} |"
                    f" {avg:.1f} | {min(sd):.1f} | {max(sd):.1f} | {percentile(sd, 95):.1f} |"
                )

    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    p(f"\nMarkdown report saved to: {md_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze eklaim scale log for latency per encounter, org, and host"
    )
    parser.add_argument("--file", type=str, required=True, help="Path to file.log")
    parser.add_argument("--count", type=int, default=0, help="Only take the first N cycles (0 = all)")
    args = parser.parse_args()

    analyze(args.file, args.count)
