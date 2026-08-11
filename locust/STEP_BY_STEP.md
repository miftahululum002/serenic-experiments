# Step-by-Step: Test Kapasitas Server Data Parsing

Tujuan: mengetahui kapasitas server (throughput worker, RPS, latency) saat
queue data parsing (`integration_data_parsing_agent_prod`) mulai jenuh.

Semua command dijalankan dari direktori `experiments/locust`.

---

## 0. Persiapan (sekali saja)

```bash
cd /Users/miftahululum002/projects/serenic/experiments/locust
```

### 0.1 Aktifkan venv & install dependency

```bash
python3 -m venv .venv          # jika belum ada
source .venv/bin/activate
pip install -r requirements.txt
```

### 0.2 Siapkan .env

```bash
cp .env.example .env
```

Isi minimal:
- `ORGANIZATION_ID` — harus ada di `organization.csv`
- `REDIS_HOST` / `REDIS_PORT` / `REDIS_USER` / `REDIS_PASSWORD` — untuk monitor queue

### 0.3 Payload update encounter (sudah disiapkan)

Update encounter memakai `norec`/`noregistrasi` yang **harus sudah ada di
database**. Data sudah disiapkan:

- `data/norec_pool.csv` — daftar 50 norec (sumber kebenaran; ubah isinya untuk
  mengganti data tes)
- `data/update_encounters_chunks/` — payload lengkap per norec, dipecah jadi
  chunk <3MB (sudah difilter hanya untuk norec yang ada di pool)

Locustfile memutar pool norec bergantian per request, jadi tiap request memakai
payload + id yang konsisten dan selalu menciptakan job parsing baru (hindari
dedup server).

Verifikasi koneksi Redis + queue:

```bash
source .venv/bin/activate
python -c "
from config import DATA_PARSING_AGENT
from monitor_queue_capacity import sample
import redis
from config import REDIS_HOST, REDIS_PORT, REDIS_USER, REDIS_PASSWORD
c = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, username=REDIS_USER, password=REDIS_PASSWORD)
print(sample(c, DATA_PARSING_AGENT))
"
```

Harusnya muncul output dengan `pending`, `workers_busy`, `workers_idle`.
Kalau error, periksa koneksi Redis / nilai .env.

---

## 1. Jalankan monitor queue (Terminal 1)

```bash
source .venv/bin/activate
mkdir -p results

python monitor_queue_capacity.py --interval 5 --output results/queue_monitor_capacity.csv
```

Script ini menampilkan tiap 5 detik:
- `pending` (backlog), `started`, `failed`, `finished`
- `busy/idle` (jumlah worker)
- `rate` (throughput proses, job/detik)
- `trend` (NAIK/TURUN/FLAT) dan status `OK` / `JENUH`
- `CAP` (estimasi kapasitas, muncul setelah jenuh)

Biarkan berjalan sampai load test selesai. Tekan `Ctrl+C` untuk berhenti.

> Opsional: monitor eklaim batch di terminal terpisah
> `python monitor_eklaim_queue.py --interval 5`

---

## 2. Jalankan beban Locust (Terminal 2)

**Pastikan monitor sudah berjalan** sebelum memulai beban.

Beban bertahap (recommended, biar titik jenuh kelihatan jelas):

```bash
source .venv/bin/activate

locust -f locustfile_update.py \
  -u 200 --spawn-rate 10 \
  --run-time 20m \
  --csv results/locust \
  --csv-full-history \
  --headless
```

Ubah skala beban sesuai kebutuhan, misalnya:
- `-u 50 --spawn-rate 5` untuk beban kecil dulu
- `-u 500 --spawn-rate 20` untuk uji skala besar

Jangan lupa `--csv-full-history` — wajib untuk analisis RPS + latency per waktu.

Selama test berjalan, amati Terminal 1:
- Status masih `OK` dan `trend` FLAT/TURUN → server belum jenuh, beban bisa dinaikkan.
- Status berubah `JENUH` dan muncul `CAP: x.xx job/s` → sudah ketemu batasnya.

---

## 2b. Mode nohup (anti putus SSH / internet down)

`nohup` + `&` membuat proses lanjut berjalan di background walau koneksi
SSH/terminal terputus. Kalau internet sempat down, proses tidak mati —
locust akan gagal mengirim saat offline lalu lanjut lagi saat online.

```bash
cd /Users/miftahululum002/projects/serenic/experiments/locust
source .venv/bin/activate
mkdir -p logs

# Monitor di background (coba `nohup: ignoring input` adalah normal)
nohup python monitor_queue_capacity.py \
  --interval 5 --output results/queue_monitor_capacity.csv \
  > logs/monitor_capacity.log 2>&1 &
echo $! > logs/monitor_capacity.pid

# Beban locust di background
nohup locust -f locustfile_update.py \
  -u 200 --spawn-rate 10 --run-time 20m \
  --csv results/locust --csv-full-history --headless \
  > logs/locust_capacity.log 2>&1 &
echo $! > logs/locust_capacity.pid
```

Pantau progress kapan saja (dari terminal mana pun, tanpa mengganggu proses):

```bash
# monitor queue (live: pending/rate/status JENUH/CAP)
tail -f logs/monitor_capacity.log

# status beban locust (RPS, latency)
tail -f logs/locust_capacity.log
```

Tunggu selesai: locust selesai sendiri setelah `--run-time` (20m), atau cek:

```bash
ps -p $(cat logs/locust_capacity.pid) && echo "masih jalan" || echo "locust selesai"
```

Setelah selesai, stop monitor lalu lanjut analisis (bagian 3):

```bash
kill $(cat logs/monitor_capacity.pid) 2>/dev/null || true
```

Periksa hasilnya (bagian 3) memakai `results/queue_monitor_capacity.csv` dan
`results/locust_stats_history.csv`.

> Alternatif lebih tangguh: `tmux` (perlu install). `tmux new -s cap`,
> jalankan perintah biasa, lalu `Ctrl+b d` untuk detach — proses tetap hidup
> dan bisa di-`tmux attach -t cap` lagi kapan pun.

---

## 2c. Jalankan di VM pakai nohup (satu perintah, semuanya)

`run_capacity_test.sh` menjalankan monitor + locust + analisis sekaligus.
Bungkus dengan `nohup` agar semua berjalan di background dan tetap hidup
meski koneksi SSH/terminal ke VM terputus.

Persiapan sekali di VM:

```bash
# salin project ke VM (dari lokal), lalu di dalam VM:
cd /path/ke/locust
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# isi ORGANIZATION_ID, REDIS_* dst sesuai environment VM
```

Jalankan (sekali perintah, di VM):

```bash
cd /path/ke/locust
source .venv/bin/activate
mkdir -p logs results

nohup env USERS=200 RUN_TIME=20m SPAWN_RATE=10 INTERVAL=5 \
  ./run_capacity_test.sh > logs/capacity_test.log 2>&1 &
echo $! > logs/capacity_test.pid
```

Catatan:
- `USERS`, `RUN_TIME`, `SPAWN_RATE`, `INTERVAL` opsional — sesuaikan skalanya.
- Script sudah `cd` ke direktori sendiri dan mengaktifkan venv, jadi path
  absolut tidak wajib.
- Selesai sendiri setelah `RUN_TIME`; log akhir berisi ringkasan kapasitas.

Pantau (kapan pun, tanpa mengganggu proses):

```bash
# progress live (log monitor + locust + analisis semua masuk ke sini)
tail -f logs/capacity_test.log

# apakah masih berjalan?
ps -p $(cat logs/capacity_test.pid) && echo "MASIH JALAN" || echo "SELESAI"
```

Setelah selesai, hasilnya (nama pakai timestamp, cek isi `results/`):

```bash
ls -t results/ | head
# monitor : results/queue_monitor_<timestamp>.csv
# locust  : results/locust_<timestamp>_stats_history.csv
# analisis: results/kapasitas_<timestamp>.txt
cat results/kapasitas_*.txt
```

> Catatan: jika kamu menutup sesi SSH, proses tetap jalan karena `nohup`.
> Untuk mematikan manual (mis. beban terlalu besar): `kill $(cat logs/capacity_test.pid)`.

---

## 3. Analisis hasil (setelah locust selesai)

Dari Terminal 2 (atau terminal baru):

```bash
source .venv/bin/activate

python analyze_capacity_full.py \
  --monitor results/queue_monitor_capacity.csv \
  --locust results/locust_stats_history.csv \
  --output results/kapasitas.txt
```

Output menampilkan:
- Backlog awal/akhir/puncak
- Throughput worker (job/detik, job/jam, job/hari)
- Status `[JENUH]` sejak kapan / `[OK]` belum jenuh
- **Peak RPS** + latency (avg/p95/p99)
- **RPS + latency saat jenuh** ← batas kapasitas server

Hasil ringkas juga disimpan ke `results/kapasitas.txt`.

---

## 4. Satu perintah otomatis (opsional)

Script `run_capacity_test.sh` menggabungkan langkah 1–3 sekaligus
(monitor background → locust → analisis):

```bash
source .venv/bin/activate
USERS=200 RUN_TIME=20m SPAWN_RATE=10 INTERVAL=5 ./run_capacity_test.sh
```

Hasilnya di `results/` dengan nama ber-*timestamp*.

---

## 5. Interpretasi hasil

| Indikator | Arti |
|---|---|
| `[OK]` backlog tidak tumbuh monoton | Worker masih sanggup → naikkan beban |
| `[JENUH]` backlog tumbuh monoton | Laju masuk > kapasitas proses → di titik ini = batas |
| `rate` / throughput (job/detik) | Kapasitas proses worker sebenarnya |
| RPS + latency saat jenuh | Batas request API sekaligus kualitas respons (avg/p95/p99) |
| Latency melonjak tiba-tiba | Backlog penuh (backpressure) — API mulai menolak/menunda |

> Perhatian: test mengirim data asli ke environment target. Gunakan staging
> dengan data tes, atau siapkan pembersihan data setelah test.
