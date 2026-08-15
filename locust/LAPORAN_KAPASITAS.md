# Laporan Uji Kapasitas Server Data Parsing

**Tanggal uji:** 11 Agustus 2026 (8 skenario)
**Server target:** GCE `serenic-prod` — project `serenic-aurio-mvp`, zone `asia-southeast2-a`, instance `2068346421968103618`
**Queue:** `integration_data_parsing_agent_prod`
**Endpoint diuji:** `POST /integrations/v2/encounters/update` (6 norec/request)

---

## 1. Ringkasan Eksekutif

Server **tidak mampu melayani beban uji** pada semua skenario:

- **Latency sangat tinggi sejak awal** — median respons 29–53 detik bahkan untuk
  test dengan hanya 6–18 request.
- Pada beban berkelanjutan (run 5, ~19 menit), median respons melonjak ke
  **132 detik** (p95 = 204 s, p99 = 248 s).
- **RPS efektif sangat rendah** — 0.1–1.4 req/s, jauh di bawah kemampuan normal
  endpoint HTTP.
- **CPU server berada di 79% saat idle/baseline** dan terkunci **95–99%** selama
  test → server CPU-bound; worker parsing menguras CPU yang dibutuhkan API.
- Backlog queue **terus bertumbuh** (~1.868 → ~2.110 dan naik) karena laju masuk
  melebihi laju proses worker.

**Kesimpulan:** kapasitas efektif server saat ini **≈ 0 RPS dengan latency > 30 s**
sekalipun dalam kondisi nyaris tanpa beban. Titik macet ada di **kapasitas CPU
server**, bukan pada jumlah user uji.

---

## 2. Konfigurasi Uji

| Parameter | Nilai |
|---|---|
| User simulas | 200 |
| Spawn rate | 10 user/detik |
| Payload | 6 norec/request dari pool `norec_pool.csv` (50 norec) |
| Auto-stop | aktif saat backlog jenuh terdeteksi (run 6–8) |
| Durasi target | hingga 20 menit / hingga jenuh |

---

## 3. Matriks Hasil per Run

| # | Mulai | Durasi | Request | Gagal | % Gagal | RPS | Median | Avg | p95 | p99 | Status |
|---|-------|-------:|--------:|------:|--------:|----:|-------:|----:|----:|----:|--------|
| 1 | 15:12:17 | 106 s | 376 | 376 | 100% | 3.51 | 41 s | 37.3 s | 60 s | 66 s | Gagal (422) |
| 2 | 15:23:12 | 330 s | 1.370 | 1.370 | 100% | 4.14 | 43 s | 42.9 s | 56 s | 69 s | Gagal (422) |
| 3 | 15:44:33 | 68 s | 116 | 116 | 100% | 1.75 | 29 s | 31.0 s | 50 s | 54 s | Gagal (422) |
| 4 | 15:48:13 | 84 s | 122 | 122 | 100% | 1.44 | 30 s | 33.5 s | 66 s | 73 s | Gagal (422) |
| 5 | 16:29:06 | 1.121 s | 1.560 | 153 | 9.8% | 1.39 | **132 s** | 126.9 s | 204 s | 248 s | Berhasil, latency meledak |
| 6 | 17:09:36 | 66 s | 18 | 0 | 0% | 0.27 | 35 s | 36.0 s | 52 s | 52 s | Auto-stop jenuh |
| 7 | 17:20:06 | 75 s | 10 | 0 | 0% | 0.13 | 50 s | 53.0 s | 71 s | 71 s | Auto-stop jenuh |
| 8 | 17:27:00 | 63 s | 6 | 0 | 0% | 0.10 | 35 s | 37.5 s | 50 s | 50 s | Auto-stop jenuh |

> RPS pada kolom = rata-rata keseluruhan run. Puncak tertinggi sepanjang seluruh
> pengujian: 12.1 req/s (run 5).

---

## 4. Rincian per Fase

### Fase A — Run 1–4 (15:12–15:48): 100% error, latency tetap tinggi

- Semua request gagal dengan **HTTP 422 Unprocessable Entity** (payload ditolak
  validasi API) ditambah bug `with self.client.post(...)` pada locustfile lama
  (tiap request ikut dicatat sebagai exception).
- **Temuan kunci:** meski hanya memproduksi error, respons memakan **29–43 detik**
  (median). Artinya: bukan hanya sukses yang lambat — **server melayani bahkan
  error respons pun dalam puluhan detik**.

### Fase B — Run 5 (16:29:06, ±19 menit): payload valid, latency meledak

- 90.2% sukses, namun median **132 s**, p95 **204 s**, p99 **248 s**, max **290 s**.
- Pola konsentris: semakin lama beban dipertahankan, semakin memburuk (lihat
  lampiran stats_history) — server tidak pernah pulih karena CPU terus jenuh.
- Kegagalan (153): **502 Bad Gateway ×123**, **500 ×21**, 422 ×7, 401 ×2.

### Fase C — Run 6–8 (17:09–17:27): auto-stop saat jenuh

- Hanya 6–18 request berhasil diselesaikan (0 gagal), tapi masing-masing butuh
  **35–53 detik**. RPS 0.10–0.27.
- Monitor mendeteksi backlog tumbuh monoton → test dihentikan otomatis.
- Meski beban "ringan", server tetap jenuh — mengonfirmasi bahwa batas kapasitas
  sudah terlampaui sejak awal.

---

## 5. Statistik Queue (Redis, live)

| Metrik | Nilai | Keterangan |
|---|---|---|
| Pending backlog | ~1.868 → **~2.110** | tumbuh +0.36 job/detik |
| Sedang diproses (started) | **4 job** (konstan) | hanya ~4 worker menarik job dari queue ini |
| Finished | ~76–84 | registry (TTL pembersihan aktif) |
| Failed | 40 (konstan) | registry |
| Worker busy | 13–27 | termasuk queue lain (mis. eklaim batch) |
| Worker idle | 27–41 | |

Laju (sampling 33 dtk): **masuk ≈ +0.36 job/s > drain < 0.1 job/s** → backlog tumbuh.

> Selisih `finished` registry tidak reliabel untuk laju (RQ membersihkan job lama
> sesuai TTL; terlihat `jobs_processed` bernilai negatif di run 7).

---

## 6. Analisis CPU (GCE serenic-prod)

| Periode | Mean CPU | Max CPU |
|---|---|---|
| Baseline (sebelum test, 23:03–23:29) | **79.0%** | 95.6% |
| Saat test (23:30–23:59) | **92.1%** | **98.7%** |

- Server sudah **79% CPU dalam kondisi normal** → hampir tidak ada headroom.
- Selama uji, CPU **pinned 95–99%** → API dan worker parsing berebut resource yang
  sama → API lambat → backlog menumpuk → sistem makin berat (efek domino).

---

## 7. Analisis Error

| Error | Jumlah | Makna |
|---|---|---|
| `422 Unprocessable Entity` | 376+1370+116+122+7 | payload/validasi ditolak API (run 1–4 dominan) |
| `502 Bad Gateway` | 123 | proxy/gateway gagal — server overload |
| `500 Internal Server Error` | 21 | error server saat kelebihan beban |
| `401 Unauthorized` | 2 | token sesekali invalid |
| `LocustError` (with-block) | 376 | bug locustfile lama (sudah diperbaiki) |

---

## 8. Akar Masalah

1. **Kapasitas CPU tidak memadai** — baseline 79% berarti nyaris tanpa headroom;
   beban kecil langsung membuat CPU 95–99%.
2. **Worker parsing dominan memakan CPU** — 14–27 worker busy, hanya ±4 job parsing
   berjalan paralel, tiap job berat → sumber utama beban CPU.
3. **Efek domino** — CPU habis → API lambat (30–290 s) → backlog tumbuh → sistem
   makin terbebani → error 500/502.

---

## 9. Rekomendasi

| No | Aksi | Prioritas |
|---|---|---|
| 1 | **Scale-up VM** (tambah vCPU) — headroom CPU saat ini tidak ada | Tinggi |
| 2 | **Batasi concurrency worker parsing** agar CPU selalu menyisakan resource untuk API | Tinggi |
| 3 | Pisahkan beban: jalankan worker parsing di instance terpisah dari API | Sedang |
| 4 | Evaluasi efisiensi job parsing (durasi rata-rata/job, batch, model AI) | Sedang |
| 5 | Investigasi slow-path di endpoint (antrean DB, lock, dsb.) yang membuat 30 s+ | Sedang |
| 6 | Ulangi uji kapasitas setelah perbaikan untuk membandingkan RPS/latency | Sedang |

---

## 10. Lampiran (File Data)

- `results/queue_monitor_20260811_170936.csv` — backlog run 6 (1.868 → 1.874)
- `results/queue_monitor_20260811_172006.csv` — backlog run 7 (1.960 → 1.965)
- `results/locust_20260811_15xxxx_stats_history.csv` — timeseries run 1–4
- `results/locust_20260811_162906_stats_history.csv` — timeseries run 5 (±19 menit)
- `results/locust_20260811_17xxxx_stats_history.csv` — timeseries run 6–8
- `results/kapasitas_20260811_172006.txt` & `kapasitas_20260811_172700.txt` — hasil analisis otomatis
- `CPU_Utilization.csv` — metrik CPU GCE
- `CPU_Usage_vs._Reserved_(vCPUs).csv` — vCPU vs pemakaian
