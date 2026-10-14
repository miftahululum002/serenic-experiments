#!/usr/bin/env python3
"""Ambil data WHOIS sebuah domain tanpa dependensi eksternal (socket port 43).

Alur: tanya whois.iana.org untuk cari server WHOIS TLD, lalu query server itu
(dan ikuti referral registrar kalau ada).

Contoh:
  python3 common/whois.py serenic.ai
  python3 common/whois.py serenic.ai google.com --raw
  python3 common/whois.py example.co.id --server whois.pandi.id
"""
import argparse
import json
import re
import socket
import sys
import urllib.request

IANA = "whois.iana.org"
KEYS = {
    "Registrar": r"^\s*Registrar:\s*(.+)$",
    "Dibuat": r"^\s*(?:Creation Date|Created On|created):\s*(.+)$",
    "Diperbarui": r"^\s*(?:Updated Date|Last Updated On|last-update):\s*(.+)$",
    "Kedaluwarsa": r"^\s*(?:Registry Expiry Date|Registrar Registration Expiration Date|Expiration Date|Expiry Date):\s*(.+)$",
    "Status": r"^\s*(?:Domain Status|Status):\s*(.+)$",
    "Name Server": r"^\s*(?:Name Server|nserver):\s*(.+)$",
}


def query(server, q, timeout=10):
    """Kirim query WHOIS ke server:43, kembalikan responsnya sebagai string."""
    try:
        conn = socket.create_connection((server, 43), timeout=timeout)
    except OSError as e:
        raise OSError(f"tidak bisa konek ke {server}:43 ({e})") from e
    with conn as s:
        s.sendall((q + "\r\n").encode())
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace")


def find_server(domain):
    """Cari server WHOIS untuk TLD domain lewat IANA."""
    m = re.search(r"^[ \t]*whois:[ \t]*(\S+)", query(IANA, domain.rsplit(".", 1)[-1]),
                  re.IGNORECASE | re.MULTILINE)
    return m.group(1) if m else None


def whois(domain, server=None):
    """Kembalikan (server_terakhir, teks_whois)."""
    server = server or find_server(domain)
    if not server:
        raise RuntimeError(f"server WHOIS untuk TLD '{domain}' tidak ditemukan")
    text = query(server, domain)
    # Ikuti referral ke registrar (mis. Verisign -> registrar) satu level.
    m = re.search(r"^[ \t]*Registrar WHOIS Server:[ \t]*(\S+)", text, re.IGNORECASE | re.MULTILINE)
    if m and m.group(1).lower() != server.lower():
        try:
            return m.group(1), query(m.group(1), domain)
        except OSError:
            pass  # registrar tidak bisa dihubungi, pakai hasil registry
    return server, text


def rdap(domain, timeout=15):
    """Fallback: ambil data lewat RDAP (HTTPS), kembalikan (ringkasan, json_mentah)."""
    req = urllib.request.Request(f"https://rdap.org/domain/{domain}",
                                 headers={"Accept": "application/rdap+json",
                                          "User-Agent": "Mozilla/5.0 (compatible; whois.py)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    out = {}
    for e in data.get("entities", []):
        if "registrar" in e.get("roles", []):
            vcard = e.get("vcardArray", [None, []])[1]
            name = next((v[3] for v in vcard if v[0] == "fn"), None)
            if name:
                out["Registrar"] = [name]
    events = {e["eventAction"]: e["eventDate"] for e in data.get("events", [])}
    for label, key in (("Dibuat", "registration"), ("Diperbarui", "last changed"),
                       ("Kedaluwarsa", "expiration")):
        if key in events:
            out[label] = [events[key]]
    if data.get("status"):
        out["Status"] = data["status"]
    ns = [n["ldhName"] for n in data.get("nameservers", []) if "ldhName" in n]
    if ns:
        out["Name Server"] = ns
    return out, data


def summarize(text):
    out = {}
    for label, pattern in KEYS.items():
        vals = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        vals = list(dict.fromkeys(v.strip() for v in vals))  # unik, urutan tetap
        if vals:
            out[label] = vals
    return out


def main():
    parser = argparse.ArgumentParser(description="Cek WHOIS domain.")
    parser.add_argument("domains", nargs="+", help="domain yang dicek, mis. serenic.ai")
    parser.add_argument("--server", help="paksa pakai server WHOIS tertentu")
    parser.add_argument("--raw", action="store_true", help="tampilkan respons mentah")
    args = parser.parse_args()

    rc = 0
    for domain in args.domains:
        domain = domain.strip().lower()
        print(f"== {domain}")
        try:
            server, text = whois(domain, args.server)
        except (OSError, RuntimeError) as e:
            print(f"  ⚠️  WHOIS port 43 gagal: {e}")
            print("  mencoba RDAP (HTTPS)...")
            try:
                info, raw = rdap(domain)
            except (OSError, ValueError) as e2:
                print(f"  ❌ RDAP juga gagal: {e2}")
                rc = 1
                continue
            if args.raw:
                print(json.dumps(raw, indent=2, ensure_ascii=False))
                continue
            for label, vals in info.items():
                print(f"  {label}: {', '.join(vals)}")
            continue
        print(f"  server: {server}")
        if args.raw:
            print(text)
            continue
        info = summarize(text)
        if not info:
            print("  (tidak ada field yang dikenali, coba --raw)")
        for label, vals in info.items():
            print(f"  {label}: {', '.join(vals)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
