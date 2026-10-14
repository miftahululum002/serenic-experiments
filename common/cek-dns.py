#!/usr/bin/env python3
"""Cek apakah host masih nyangkut ke Vercel (versi Python dari cek-dns.sh).

Contoh:
  python3 debug/cek-dns.py
  python3 debug/cek-dns.py serenic.ai www.serenic.ai
  python3 debug/cek-dns.py app.serenic.ai --server-ip 34.101.100.117
"""
import argparse
import re
import subprocess
import sys

VERCEL_PATTERN = re.compile(r"vercel|76\.76\.21|66\.33\.60", re.IGNORECASE)


def run(cmd):
    """Jalankan command, kembalikan stdout+stderr sebagai string."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        return "timed out"


def check_dns(hosts, resolvers):
    print("== 1. Record DNS (CNAME/A/AAAA) per resolver")
    ok = True
    for host in hosts:
        for r in resolvers:
            for t in ("CNAME", "A", "AAAA"):
                out = run(["dig", f"@{r}", host, t, "+short", "+time=5", "+tries=3"])
                if "timed out" in out or "no servers" in out:
                    print(f"  ⚠️  {host} {t} @{r} -> timeout, jalankan ulang")
                    continue
                ans = " ".join(l for l in out.splitlines() if l.strip() and not l.startswith(";"))
                if not ans:
                    continue
                if VERCEL_PATTERN.search(ans):
                    print(f"  ❌ {host} {t} @{r} -> {ans}")
                    ok = False
                else:
                    print(f"  ✅ {host} {t} @{r} -> {ans}")
    return ok


def check_http(hosts):
    print("== 2. Respons HTTP")
    ok = True
    for host in hosts:
        for url in (f"http://{host}", f"https://{host}"):
            hdr = run(["curl", "-sIL", "--max-time", "15", url])
            codes = re.findall(r"^HTTP/\S+\s+(\d+)", hdr, re.MULTILINE)
            code = codes[-1] if codes else ""
            if re.search(r"x-vercel-error|server: vercel", hdr, re.IGNORECASE):
                print(f"  ❌ {url} -> {code} (dilayani Vercel)")
                ok = False
            elif code == "200":
                print(f"  ✅ {url} -> {code}")
            else:
                print(f"  ❌ {url} -> {code or 'tidak ada respons'}")
                ok = False
    return ok


def check_server(hosts, server_ip):
    print(f"== 3. Cek server nginx langsung ({server_ip})")
    ok = True
    for host in hosts:
        code = run([
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "15",
            "--resolve", f"{host}:443:{server_ip}", f"https://{host}/",
        ]).strip()
        if code == "200":
            print(f"  ✅ {host} via nginx -> {code}")
        elif code in ("301", "302", "307", "308"):
            print(f"  ✅ {host} via nginx -> {code} (redirect)")
        else:
            print(f"  ❌ {host} via nginx -> {code}")
            ok = False
    return ok


def main():
    parser = argparse.ArgumentParser(description="Cek apakah host masih mengarah ke Vercel.")
    parser.add_argument("hosts", nargs="*", default=["serenic.ai", "www.serenic.ai"],
                        help="host yang dicek (default: serenic.ai www.serenic.ai)")
    parser.add_argument("--server-ip", default="34.101.100.117",
                        help="IP server nginx (default: 34.101.100.117)")
    parser.add_argument("--ns", default="dns1.registrar-servers.com",
                        help="nameserver authoritative (default: dns1.registrar-servers.com)")
    parser.add_argument("--resolvers", nargs="+", default=["8.8.8.8", "1.1.1.1"],
                        help="resolver publik tambahan (default: 8.8.8.8 1.1.1.1)")
    args = parser.parse_args()

    resolvers = [args.ns] + args.resolvers
    results = [
        check_dns(args.hosts, resolvers),
        check_http(args.hosts),
        check_server(args.hosts, args.server_ip),
    ]

    print()
    if all(results):
        print("SEMUA OK ✅")
        return 0
    print("MASIH ADA MASALAH ❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
