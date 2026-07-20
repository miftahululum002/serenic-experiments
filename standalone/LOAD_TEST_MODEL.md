# Pemodelan Load Testing: Standalone Coding API

## 1. Arsitektur Sistem

```
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│                  │       │                  │       │                  │
│   Load Tester   │──────►│   API Gateway    │──────►│  Worker (Async)  │
│  (load_test.py) │  POST │  (api.serenic.ai)│       │                  │
│                  │◄──────│                  │       │  Proses coding   │
│   Async Users   │  GET  │                  │       │  secara async    │
│                  │       │                  │       │                  │
└──────────────────┘       └──────────────────┘       └──────────────────┘
```

### Alur Request

```
User 1 ──POST──► [API] ──► [Worker Queue] ──► [Proses] ──► FINISHED
   │                                                              │
   └─────────────────── GET poll ─────────────────────────────────┘
```

1. **POST** `/coding` → Submit job, dapat `job_id`
2. **GET** `/coding/jobs/:job_id` → Poll status sampai `FINISHED`

---

## 2. Model Konkuensi

### 2.1 Concurrent User Model

Semua user dijalankan **bersamaan** menggunakan `asyncio`. Setiap user adalah coroutine independent yang melakukan POST lalu polling GET.

```python
# Pseudocode
async def user_session(user_id):
    job_id = POST(payload)          # Submit job
    while True:
        status = GET(job_id)        # Poll status
        if status == "FINISHED":
            return                  # Selesai
        sleep(poll_interval)        # Tunggu sebelum poll lagi
```

### 2.2 Ramp-Up (Opsional)

Untuk menghindari burst request yang terlalu besar, user dapat dikirim **bertahap**:

```
Ramp-up = 30 detik, 10 user
→ User 1: mulai detik 0
→ User 2: mulai detik 3
→ User 3: mulai detik 6
→ ...
→ User 10: mulai detik 27
```

Tanpa ramp-up, semua user mengirim request di detik 0 secara bersamaan.

---

## 3. Metrik yang Diukur

### 3.1 Throughput

| Metrik | Deskripsi | Satuan |
|--------|-----------|--------|
| **Throughput** | Jumlah job selesai per menit | jobs/menit |
| **Request Rate** | Jumlah POST per detik | req/detik |

### 3.2 Latency

| Metrik | Deskripsi | Rumus |
|--------|-----------|-------|
| **POST Latency** | Waktu dari kirim POST sampai dapat response | `post_response_at - post_sent_at` |
| **E2E Latency** | Waktu dari POST sampai job FINISHED | `get_final_at - post_sent_at` |
| **Poll Count** | Jumlah kali polling GET per job | counter |

### 3.3 Distribusi Latensi

Dihitung menggunakan percentile untuk memahami variasi:

| Percentile | Deskripsi |
|------------|-----------|
| **P50 (Median)** | 50% user mendapat hasil di bawah nilai ini |
| **P90** | 90% user mendapat hasil di bawah nilai ini |
| **P95** | 95% user mendapat hasil di bawah nilai ini |
| **P99** | 99% user mendapat hasil di bawah nilai ini |

### 3.4 Error Rate

| Tipe Error | Deskripsi |
|------------|-----------|
| **POST Error** | Request POST gagal (timeout, 5xx, dll) |
| **Job FAILED** | Job berhasil di-submit tapi worker gagal proses |
| **Poll Timeout** | Melewati `max_poll_time` tanpa hasil FINISHED |

---

## 4. Skenario Testing

### 4.1 Light Load

```bash
.venv/bin/python load_test.py -c 5 --poll-interval 2
```

| Parameter | Nilai |
|-----------|-------|
| Concurrent user | 5 |
| Poll interval | 2 detik |
| Max poll time | 10 menit |

**Tujuan:** Mengukur baseline performance saat beban ringan.

### 4.2 Medium Load

```bash
.venv/bin/python load_test.py -c 20 --ramp-up 30 --poll-interval 3
```

| Parameter | Nilai |
|-----------|-------|
| Concurrent user | 20 |
| Ramp-up | 30 detik |
| Poll interval | 3 detik |
| Max poll time | 10 menit |

**Tujuan:** Mengukur performa saat beban normal, mengecek apakah throughput stabil.

### 4.3 Heavy Load

```bash
.venv/bin/python load_test.py -c 50 --ramp-up 60 --max-poll-time 300
```

| Parameter | Nilai |
|-----------|-------|
| Concurrent user | 50 |
| Ramp-up | 60 detik |
| Poll interval | 2 detik |
| Max poll time | 5 menit |

**Tujuan:** Menemukan breaking point, melihat apakah ada degradasi performa.

### 4.4 Stress Test

```bash
.venv/bin/python load_test.py -c 100 --ramp-up 120 --max-poll-time 180
```

| Parameter | Nilai |
|-----------|-------|
| Concurrent user | 100 |
| Ramp-up | 120 detik |
| Poll interval | 2 detik |
| Max poll time | 3 menit |

**Tujuan:** Mengetahui batas maksimum sistem sebelum error rate naik signifikan.

---

## 5. Diagram Waktu (Timing Diagram)

```
Waktu (detik)
│
│  t=0        t=POST     t=POLL1    t=POLL2    t=FINISHED
│   │           │           │          │            │
│   ▼           ▼           ▼          ▼            ▼
│   ───────────●───────────●──────────●────────────●──────
│              │           │          │            │
│              ├── POST ──►│          │            │
│              │  Latency  │          │            │
│              │           │          │            │
│              └───────────┴──────────┴── E2E ────┘
│                                   Latency (total)
```

### Rincian Waktu

| Event | Timestamp | Keterangan |
|-------|-----------|------------|
| POST sent | `t0` | User mengirim POST request |
| POST response | `t1` | Server merespons, dapat `job_id` |
| Poll 1 | `t2` | GET pertama, status belum FINISHED |
| Poll 2 | `t3` | GET kedua, status belum FINISHED |
| ... | ... | ... |
| Poll N | `tN` | GET terakhir, status FINISHED |

**POST Latency** = `t1 - t0`

**E2E Latency** = `tN - t0` (total waktu dari submit sampai selesai)

---

## 6. Rumus Perhitungan

### Throughput

```
Throughput = Jobs Selesai / Durasi Test (menit)
```

Contoh: 50 job selesai dalam 10 menit → Throughput = 5 jobs/menit

### Rata-rata Latensi

```
Mean = (Σ E2E Latency semua user) / Total User
```

### Standard Deviasi

```
StdDev = √(Σ(x - mean)² / n)
```

Mengukur konsistensi latensi. Nilai tinggi = latensi tidak konsisten.

---

## 7. Output Report

### 7.1 File Output

| File | Format | Kegunaan |
|------|--------|----------|
| `load_test_<ts>.md` | Markdown | Laporan lengkap untuk dibaca |
| `load_test_<ts>.csv` | CSV | Data mentah untuk analisis di Excel/Pandas |
| `load_test_<ts>.json` | JSON | Raw data untuk integrasi tools lain |

### 7.2 Struktur Report

```
1. Ringkasan           → Total, berhasil, gagal, throughput
2. Latensi POST       → Mean, P50, P90, P95, P99
3. Latensi E2E        → Mean, P50, P90, P95, P99 + bucket
4. Analisis Konkuensi → Throughput per menit
5. Analisis Error     → Detail error yang terjadi
6. Kesimpulan         → Ringkasan temuan
```

---

## 8. Contoh Interpretasi Hasil

### Hasil yang Bagus

```
- Throughput: 15 jobs/menit
- E2E Latency P50: 45 detik, P95: 120 detik
- Error rate: 0%
- POST Latency: < 500ms
```

→ Sistem stabil, mampu handle 15 job/menit dengan latensi wajar.

### Hasil yang Perlu Diperhatikan

```
- Throughput: 5 jobs/menit (turun dari 15)
- E2E Latency P95: 600 detik (10 menit)
- Error rate: 15% (POST timeout)
- POST Latency P99: 5 detik
```

→ Sistem mulai struggling, perlu investigasi worker atau resource.

---

## 9. Rekomendasi

| Aspek | Rekomendasi |
|-------|-------------|
| **Poll interval** | Gunakan 2-3 detik, terlalu sering = overhead |
| **Max poll time** | Sesuaikan dengan expected processing time |
| **Ramp-up** | Gunakan untuk load > 10 user agar tidak burst |
| **Payload** | Gunakan payload realistis sesuai data production |
| **Monitoring** | Jalankan test saat off-peak untuk baseline murni |
