#!/bin/bash
# Cek apakah serenic.ai / www.serenic.ai masih nyangkut ke Vercel
SERVER_IP="34.101.100.117"
NS="dns1.registrar-servers.com"
RESOLVERS="$NS 8.8.8.8 1.1.1.1"
FAIL=0

echo "== 1. Record DNS (CNAME/A/AAAA) per resolver"
for host in serenic.ai www.serenic.ai; do
  for r in $RESOLVERS; do
    for t in CNAME A AAAA; do
      out=$(dig @"$r" "$host" "$t" +short +time=5 +tries=3 2>&1)
      if echo "$out" | grep -q 'timed out\|no servers'; then
        echo "  ⚠️  $host $t @$r -> timeout, jalankan ulang"; continue
      fi
      ans=$(echo "$out" | grep -v '^;' | tr '\n' ' ')
      [ -z "${ans// }" ] && continue
      if echo "$ans" | grep -qiE 'vercel|76\.76\.21|66\.33\.60'; then
        echo "  ❌ $host $t @$r -> $ans"; FAIL=1
      else
        echo "  ✅ $host $t @$r -> $ans"
      fi
    done
  done
done

echo "== 2. Respons HTTP"
for url in http://serenic.ai https://serenic.ai https://www.serenic.ai; do
  hdr=$(curl -sIL --max-time 15 "$url")
  code=$(echo "$hdr" | grep -i '^HTTP' | tail -1 | awk '{print $2}')
  if echo "$hdr" | grep -qi 'x-vercel-error\|server: Vercel'; then
    echo "  ❌ $url -> $code (dilayani Vercel)"; FAIL=1
  elif [ "$code" = "200" ]; then
    echo "  ✅ $url -> $code"
  else
    echo "  ❌ $url -> ${code:-tidak ada respons}"; FAIL=1
  fi
done

echo "== 3. Cek server nginx langsung ($SERVER_IP)"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
  --resolve www.serenic.ai:443:$SERVER_IP https://www.serenic.ai/)
[ "$code" = "200" ] && echo "  ✅ nginx -> $code" || { echo "  ❌ nginx -> $code"; FAIL=1; }

echo
[ $FAIL -eq 0 ] && echo "SEMUA OK ✅" || echo "MASIH ADA MASALAH ❌"
exit $FAIL