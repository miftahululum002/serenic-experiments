# Load Test Kapasitas Worker Parsing (Locust)

Uji kapasitas background data-parsing workers. Endpoint API hanya memasukkan job ke
Redis RQ queue (`integration_data_parsing_agent_prod`), sedangkan worker parsing
yang memprosesnya. Kapasitas diukur dari **backlog queue + throughput proses**, bukan
hanya response time API.

## Struktur

```
locust/
├── locustfile.py            # skenario beban (5 endpoint, mix 80/15/5)
├── monitor_queue.py         # monitor backlog queue data parsing selama test
├── monitor_eklaim_queue.py  # monitor backlog queue eklaim batch selama test
├── monitor_queue_capacity.py# monitor live + estimasi kapasitas (JENUH/CAP)
├── analyze_capacity.py      # analisis titik jenuh dari hasil monitor
├── analyze_worker_capacity.py # analisis throughput worker (job/detik)
├── analyze_capacity_full.py # backlog + RPS + latency pada titik jenuh
├── data/                    # payload template (dari testing-ingestor/preset)
└── requirements.txt
```

## 1. Setup

```bash
cd experiments/locust
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Set env dulu — `locustfile.py` dan monitor* otomatis membaca `.env`
(API key/host/version diambil dari `organization.csv` via `ORGANIZATION_ID`, koneksi
Redis dari `REDIS_HOST/PORT/USER/PASSWORD`). Sesuaikan `.env` bila perlu:

```bash
cp .env.example .env
# isi ORGANIZATION_ID (harus ada di organization.csv)
# sesuaikan REDIS_HOST, REDIS_PORT, REDIS_USER, REDIS_PASSWORD utk monitor_queue
# nama queue eklaim bisa diatur via AGENT_QUEUE_EKLAIM_BATCH (default eklaim_batch_agent_prod)
```

## 2. Jalankan test

Mulai monitor queue dulu (terminal 1):

```bash
# data parsing queue
python monitor_queue.py --interval 5 --output results/queue_monitor.csv

# eklaim batch queue (terpisah, terminal lain)
python monitor_eklaim_queue.py --interval 5 --output results/eklaim_monitor.csv
```

`monitor_eklaim_queue.py` juga menampilkan rincian pending/started per organisasi
(kolom `org_pending`, `org_started`) — job eklaim membawa `managing_organization_id`.

Jalankan Locust (terminal 2) — headless, ramp-up bertahap. Host default diambil dari
`api_url` organisasi; opsional override dengan `--host`:

```bash
locust -f locustfile.py \
  -u 50 --spawn-rate 5 \
  --run-time 30m \
  --csv results/locust \
  --csv-full-history
```

Ramp-up dianjurkan bertahap (mis. `-u 10` → `-u 50` → `-u 100` → `-u 200`,
step per 10–15 menit) supaya titik jenuh terlihat jelas. Alternatif: jalankan
tanpa `--run-time` lalu buka UI di `http://localhost:8089` dan atur beban interaktif.

Opsional `UNIQUE_NOREC=1` untuk mengacak `norec/noregistrasi` per request
(default statis apa adanya — perhatian: `norec` duplikat bisa didedup server
sehingga tidak membuat job baru).

## 3. Analisis

Setelah test, kombinasikan hasil locust + monitor (lapor RPS + latency saat jenuh):

```bash
python analyze_capacity_full.py --monitor results/queue_monitor.csv \
  --locust results/locust_stats_history.csv --output results/kapasitas.txt
```

Jika hanya ingin throughput worker (tanpa RPS/latency):

```bash
python analyze_worker_capacity.py --monitor results/queue_monitor.csv
```

## 4. Interpretasi

- **`[OK] backlog tidak tumbuh monoton`** → worker masih sanggup; naikkan beban.
- **`[JENUH] backlog tumbuh monoton`** → laju masuk melebihi kapasitas proses.
  RPS request & latency (avg/p95/p99) di titik itu (dari CSV locust) ≈ batas
  kapasitas worker.
- **Throughput pemrosesan (job/detik)** = jumlah job worker mampu diproses
  per detik — inilah kapasitas sebenarnya yang dicari.
- Response time API yang naik tiba-tiba biasanya karena queue penuh (backpressure).

Perhatian: test ini mengirim data asli ke environment target. Pastikan memakai
staging dengan data tes, atau siapkan pembersihan data setelah test.
