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
