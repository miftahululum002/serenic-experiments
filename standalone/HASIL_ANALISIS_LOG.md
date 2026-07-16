# Hasil Analisis Log Worker — SIMRS Standalone Coding Agent

> **Log file:** `logs/worker.log` (29,525 baris)
> **Periode:** 2026-07-09 22:50 s/d 2026-07-15 (terakhir tercatat)
> **Worker:** `b760565c2a2446f7bed9dcfaa2750312`
> **Queue:** `standalone_coding_agent_prod`

---

## 0. Ringkasan Umum

| Metrik | Nilai |
|---|---|
| Total job diproses | **299** |
| Berhasil (Success) | **282** (94.3%) |
| Timeout lalu tetap selesai | **17** (5.7%) |
| Gagal total (Failed) | **0** (0.0%) |
| Total terkena INACBG timeout | **17** (5.7%) |

---

## 1. Distribusi Status Job

| Status | Jumlah | Persentase |
|---|---|---|
| Success | 282 | 94.3% |
| Timeout tapi Selesai | 17 | 5.7% |

---

## 2. Statistik Latency Durasi Proses

| Metrik | Nilai |
|---|---|
| Jumlah data valid | 299 |
| Rata-rata (Mean) | **52.64 detik** (0.9 menit) |
| Median (P50) | **35.32 detik** (0.6 menit) |
| Standar Deviasi | 68.43 detik |
| Persentil 75 (P75) | 39.64 detik |
| Persentil 90 (P90) | 46.76 detik |
| Persentil 95 (P95) | 315.73 detik |
| Persentil 99 (P99) | 330.61 detik |
| Paling Cepat | 11.67 detik |
| Paling Lama | 342.71 detik |

### Latency per Status

| Status | Count | Mean | Median | Max |
|---|---|---|---|---|
| Success | 282 | 36.08s | 34.97s | 121.20s |
| Timeout tapi Selesai | 17 | 327.39s | 327.73s | 342.71s |

---

## 3. Detail Job yang Terkena Timeout (300s INACBG)

| Metrik | Nilai |
|---|---|
| Total job timeout | 17 |
| Rata-rata waktu tambahan setelah timeout | +27.39 detik |
| Median waktu tambahan setelah timeout | +27.73 detik |
| Max waktu tambahan setelah timeout | +42.71 detik |
| Semua job timeout akhirnya selesai? | **YA** |

### Daftar Run ID yang Timeout

| No | Run ID | Latency | Tambahan | Status |
|---|---|---|---|---|
| 1 | `a936cc68-4ca7-48c3-b121-765bc36c5881` | 326.5s | +26.5s | OK |
| 2 | `183692f9-aa64-4ddb-a800-555d50113b7b` | 342.1s | +42.1s | OK |
| 3 | `04c79d67-2841-4e3c-82e0-187d0e440b57` | 342.7s | +42.7s | OK |
| 4 | `1f4844ad-a37f-4329-8fb9-a15dca30cb14` | 324.0s | +24.0s | OK |
| 5 | `f8de3ac0-a9b3-47af-abde-3095c5065de0` | 332.6s | +32.6s | OK |
| 6 | `493ecb22-84a2-4240-96ef-15cb1ea99c11` | 329.7s | +29.7s | OK |
| 7 | `cabcde68-6b59-4bf5-a88e-c584bb3d68f2` | 320.7s | +20.7s | OK |
| 8 | `ea538efd-025b-4ae1-91c9-48dc2e516e17` | 330.4s | +30.4s | OK |
| 9 | `58f6bf6d-ec72-4cb6-bcba-2527e8835966` | 315.2s | +15.2s | OK |
| 10 | `3d3946e6-433e-46aa-8d97-bfacaed44579` | 314.5s | +14.5s | OK |
| 11 | `b9308ef8-3651-4bce-992a-e8829e6f8b1b` | 329.8s | +29.8s | OK |
| 12 | `8221db5a-1dad-48f1-85ac-095fe3d63790` | 327.7s | +27.7s | OK |
| 13 | `2c80c60c-a0b7-4dd0-a90e-ff70d32f8cb7` | 321.3s | +21.3s | OK |
| 14 | `c80bfd62-6a2b-4376-9860-5c0e6734e3c0` | 328.0s | +28.0s | OK |
| 15 | `b2c6eef1-3ee0-45d3-8e3a-35cbc54233a0` | 330.6s | +30.6s | OK |
| 16 | `f117b97d-f5ce-4225-af65-d78641acf7ae` | 325.7s | +25.7s | OK |
| 17 | `34382c2e-9c9f-4739-9838-7d595cdac5b5` | 324.2s | +24.2s | OK |

---

## 4. Distribusi Warning/Error Types

| Jumlah | Tipe Warning |
|---|---|
| 10x | `RuleContextOutputMapper: entity tidak punya rule coverage` |

> Catatan: Tidak ada `ERROR` level atau `Traceback/Exception` di seluruh log.

---

## 5. Distribusi Hasil INACBGS

| Hasil | Jumlah |
|---|---|
| INACBGS=yes | 269 job |
| INACBGS=no | 17 job |

> INACBGS=no konsisten = job yang terkena INACBG timeout.

---

## 6. Distribusi Jumlah ICD per Job

| Metrik | Nilai |
|---|---|
| ICD-10 per job — Mean | 3.0 |
| ICD-10 per job — Median | 2 |
| ICD-10 per job — Max | 15 |
| ICD-9-CM per job — Mean | 0.9 |
| Job dengan ICD-9-CM | 242 |

---

## 7. Distribusi Latency (Bucket Detik)

| Bucket | Jumlah | Persentase |
|---|---|---|
| < 60 detik | 278 | 93.0% |
| 60–120 detik | 3 | 1.0% |
| 120–180 detik | 1 | 0.3% |
| 180–240 detik | 0 | 0.0% |
| 240–300 detik | 0 | 0.0% |
| 300–360 detik | 17 | 5.7% |
| 360–600 detik | 0 | 0.0% |
| > 600 detik | 0 | 0.0% |

---

## 8. Kesimpulan & Rekomendasi

### Temuan Utama

1. **Semua 299 job BERHASIL selesai** dari sisi worker pipeline. Tidak ada satupun yang gagal total.

2. **Masalah utama: INACBG polling timeout 300 detik** — 17 job (5.7%) terkena timeout ini. Ini adalah **penyebab langsung** SIMRS menampilkan warning "Job Status: Failed" dan "Job Status: Queue".

3. **Pipeline coding ICD berjalan normal** — 282 job tanpa timeout (rata-rata 36 detik), 17 job meski timeout tetap selesai (rata-rata 327 detik). Semua job yang timeout memiliki waktu tambahan **+15 s/d +42 detik** setelah timeout, menunjukkan worker tetap memproses sampai selesai.

4. **Tidak ada error kritis** — hanya 10x warning `RuleContextOutputMapper` untuk entity `undernutrition` yang tidak punya rule coverage (fallback ke searcher).

### Rekomendasi

| Prioritas | Aksi |
|---|---|
| **HIGH** | Periksa service **INACBG** / Redis queue INACBG — timeout konsisten 300s pada SEMUA job menunjukkan INACBG service tidak merespons sama sekali |
| **MEDIUM** | Tambahkan rule coverage untuk entity `undernutrition` di `RuleContextOutputMapper` untuk menghilangkan warning |
| **LOW** | Investigasi latency outlier (>60 detik) — ada 4 job yang lambat meski tanpa timeout |
