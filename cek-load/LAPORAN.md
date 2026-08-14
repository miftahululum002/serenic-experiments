# Cek Kapasitas — Worker Background (Redis/RQ)

> Dokumen ini **dibuat otomatis** oleh `report.py`. Jangan diedit manual —
> jalankan ulang generatornya untuk memperbarui angka.

**Waktu observasi:** 14 Agustus 2026, 08:24–08:35 WIB
(6 menit sampling live)
**Sumber data:** Redis `127.0.0.1:6379` — 100% dari telemetri RQ
**Queue yang dianalisis:** `integration_data_parsing_agent_prod`

## 1. Jawaban Singkat

**Pool worker `integration_data_parsing_agent_prod`: YA, sudah 100% mentok.**
**Server (mesin): TIDAK terbukti mentok** — yang mentok adalah *jumlah worker* yang dialokasikan ke queue itu, bukan kapasitas mesinnya.

Selama 6 menit pengamatan, queue ini **tidak pernah punya satu pun worker idle** — persis 4 slot, semuanya selalu `busy`. Sementara itu 41 worker lain menganggur karena melayani queue yang kosong.

## 2. Angka Kapasitas Terukur

| Metrik | Nilai |
|---|---|
| Throughput terukur | **0,163 job/detik = 586 job/jam** |
| Isi 1 job | **1 encounter** (dari 39 payload) |
| Kapasitas efektif | **±586 encounter/jam** |
| Job selesai saat observasi | 60 job dalam 6 menit |
| Slot paralel | 3–4 (rata-rata 3,97) |
| Durasi per job | median **24,4 detik**, avg **23,8 detik**, p90 **29,3 detik**, maks **45,3 detik** |
| Kapasitas teoritis | 4,0 slot ÷ 23,8 detik = **600 job/jam** |

> Throughput terukur (586/jam) ≈ kapasitas teoritis (600/jam) — selisih 2,2%. Pool berjalan pada utilisasi **~100%**: praktis tidak ada kapasitas tersisa.


**Backlog saat ini:** 1.453 job → **ETA habis ±2,5 jam** (bila tidak ada job masuk lagi).
**Waktu tunggu job terdepan di antrean: ±3,4 jam** sebelum mulai diproses.

## 3. Tren Beban Masuk

| Jam | Backlog | Tren |
|---|---:|---|
| 08:24:59 | 1.496 | — |
| 08:26:16 | 1.486 | TURUN |
| 08:27:27 | 1.479 | TURUN |
| 08:28:35 | 1.470 | TURUN |
| 08:29:54 | 1.462 | TURUN |
| 08:30:59 | 1.452 | TURUN |

Backlog **menyusut** — laju masuk (±166/jam) sudah di bawah kapasitas proses. Hit data selesai; sekarang fase **menguras backlog** dengan laju ±586/jam.

Pada jendela 2 menit terpisah: **5 job baru masuk**, 19 job keluar antrean (16 status=finished, 3 status=started).
Seluruhnya benar-benar diproses — tidak ada yang dibuang/dedup, jadi angka throughput di atas sahih.

## 4. Kenapa Server Belum Tentu Mentok


**a. Alokasi worker.**

| Queue | Worker | Busy | Idle | Pending |
|---|---:|---:|---:|---:|
| **integration_data_parsing_agent_prod** | 4 | 4 | 0 | 1.501 |
| eklaim_batch_agent_prod | 0 | 0 | 0 | 87 |
| eklaim_batch:eklaim-1 | 0 | 0 | 0 | 3 |
| icd_matcher_agent_prod | 12 | 0 | 12 | 0 |
| icd_searcher_agent_prod | 8 | 1 | 7 | 0 |
| integration_data_analysis_administrative_agent_prod | 1 | 0 | 1 | 0 |
| integration_data_analysis_agent_prod | 16 | 6 | 10 | 0 |
| integration_data_analysis_auto_validation_agent_prod | 6 | 0 | 6 | 0 |
| integration_data_analysis_coordinator_agent_prod | 1 | 0 | 1 | 0 |
| medical_notes_agent_prod | 1 | 0 | 1 | 0 |
| ocr_agent_prod | 1 | 0 | 1 | 0 |
| standalone_coding_agent_prod | 2 | 0 | 2 | 0 |

Queue yang menumpuk (`integration_data_parsing_agent_prod`) tidak punya worker idle sama sekali, sementara 41 worker menganggur di queue yang backlog-nya 0. Worker idle **tidak bisa** membantu — tiap pool terikat ke queue-nya sendiri.


⚠️ **Queue tanpa worker sama sekali:** `eklaim_batch:eklaim-1` (3 job), `eklaim_batch_agent_prod` (87 job). Job di sini **tidak akan pernah diproses** sampai ada worker yang dijalankan untuk queue tersebut.


**b. Redis bukan bottleneck.** Memori 36.59M (maxmemory 0B), 231 ops/detik, **0 evicted keys**, **0 rejected connections**, uptime 14 hari.


**c. Downstream punya headroom.** 9 queue lain backlog-nya 0 dengan worker idle — kalau `integration_data_parsing_agent_prod` dipercepat, tahap berikutnya masih sanggup menyerap.


**d. Kegagalan job.**


| Queue | Gagal | Terbaru | Durasi median | Kena timeout |
|---|---:|---:|---:|---:|
| integration_data_analysis_administrative_agent_prod | 733 | 48,0 jam lalu | 0,4 detik | 0 dari 733 |
| icd_searcher_agent_prod | 238 | 6,4 jam lalu | 0,0 detik | 1 dari 238 |
| integration_data_parsing_agent_prod | 40 | 57,0 jam lalu | 0,1 detik | 0 dari 40 |
| icd_matcher_agent_prod | 19 | 288,9 jam lalu | 4 menit | 4 dari 19 |
| integration_data_analysis_agent_prod | 19 | 57,0 jam lalu | 0,3 detik | 0 dari 19 |
| standalone_coding_agent_prod | 12 | 1.043,9 jam lalu | 8,9 detik | 0 dari 12 |
| integration_data_analysis_auto_validation_agent_prod | 7 | 48,1 jam lalu | 1,2 detik | 0 dari 7 |
| integration_data_analysis_coordinator_agent_prod | 4 | 672,5 jam lalu | 19,5 detik | 0 dari 4 |
| ocr_agent_prod | 1 | 281,0 jam lalu | 11 menit | 0 dari 1 |

Kegagalan terbaru pun sudah berumur 6,4 jam, dan tidak ada kegagalan baru selama observasi — **bukan isu kapasitas**.

## 5. Yang Belum Bisa Dipastikan

Pengecekan ini hanya lewat port Redis (`6379`) — **tanpa akses CPU/RAM host**,
karena `report.py` dijalankan dari luar VM. Itu satu-satunya variabel yang
menentukan apakah menambah worker akan menaikkan throughput:

- CPU host masih longgar → menambah worker menaikkan throughput hampir linier.
- CPU host sudah jenuh → menambah worker **tidak akan** menambah throughput,
  hanya memperbanyak rebutan CPU.

**Cara melengkapinya:** salin folder ini ke VM yang menjalankan worker, lalu
jalankan `report.py` di sana. Metrik CPU/RAM akan ikut terukur otomatis dan
bagian ini berganti menjadi kesimpulan yang pasti.

## 6. Rekomendasi


1. **Cek CPU host lebih dulu** — jalankan `report.py` langsung di VM worker agar CPU/RAM ikut terukur. Ini menentukan semua langkah berikutnya.
2. **Jika CPU longgar:** naikkan worker `integration_data_parsing_agent_prod` bertahap dari 4 → 12 dengan menggeser jatah dari `icd_matcher_agent_prod` (12 idle), `integration_data_analysis_agent_prod` (10 idle), sambil memantau CPU tiap tahap. Batas atas teoretis: 586 → **±1.799 job/jam** (backlog habis ±48 menit) — angka ini mengasumsikan penskalaan linier dan CPU tidak jadi penghalang, jadi perlakukan sebagai batas atas, bukan target.
3. **Jika CPU jenuh:** tambah mesin / naikkan ukuran instance — menambah worker di mesin yang sama tidak akan menolong.
4. **Jalankan worker untuk `eklaim_batch:eklaim-1`** — 3 job menggantung tanpa consumer.
5. **Jalankan worker untuk `eklaim_batch_agent_prod`** — 87 job menggantung tanpa consumer.
6. **Pasang alarm** bila `pending` queue `integration_data_parsing_agent_prod` > 500 atau usia job terdepan > 15 menit.

## 7. Cara Reproduksi

```bash
cd experiments/cek-load
./.venv/bin/python report.py --queue integration_data_parsing_agent_prod --minutes 6
```

Langkah manual per bagian ada di [`README.md`](README.md).
