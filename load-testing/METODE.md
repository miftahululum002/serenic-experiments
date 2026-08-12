# Metode pengujian untuk `emr-integration-data-parsing-worker_PROD`

## Kenapa load test biasa tidak berlaku di sini

`api-server_PROD` hanya **menerima dan menaruh pekerjaan di antrean**. Kerja
sebenarnya ada di worker. Konsekuensinya:

```
POST /integrations/v2/encounters/update
      │
      ├─ validasi + parse payload   ← sinkron, di proses API (uvicorn --workers 2)
      ├─ simpan payload ke disk     ← sinkron
      └─ data_parsing_queue.enqueue(...)  → balas 200 dalam hitungan detik
                    │
                    ▼
        emr-integration-data-parsing-worker_PROD
              1 job = 1 request = N encounter, diproses BERURUTAN
              ingestor commit tiap 5 encounter
                    │
                    └─ enqueue ke analysis coordinator (kalau IS_CODEX_API_V2_PROCESS=true)
```

Kalau kamu mengukur RPS dan p95 HTTP, API akan tampak sehat pada beban berapa
pun — karena yang diukur cuma biaya menaruh job ke Redis. Sementara antrean bisa
tumbuh ribuan job dan user menunggu berjam-jam.

Tiga fakta dari kode yang menentukan bentuk pengujian:

| Fakta | Sumber | Akibat untuk metode test |
|---|---|---|
| `Worker.work()` memproses satu job pada satu waktu | `emr_integration_data_parsing_worker.py` (bagian `__main__`) | Konkurensi parsing = **jumlah replika container** (saat ini **4**), bukan jumlah thread. Skala diuji dengan `--scale`. |
| Satu request = satu job = N encounter berurutan | `worker_process_update_encounter_parsing_v2` | Satuan kapasitas adalah **encounter/menit**, bukan request/detik. Request berisi 500 encounter tetap dikerjakan **satu worker**; tiga replika lain menganggur. |
| Encounter yang tidak ada di DB akan di-skip | `ingestor.py:613` `"Encounter ... not found, skipping update"` | Wajib seed encounter dulu, kalau tidak job selesai dalam milidetik dan hasilnya palsu. |
| Ada advisory lock per encounter | `_acquire_encounter_ingest_advisory_lock` | Request yang memuat encounter sama akan saling menunggu. Tiap request harus dapat encounter berbeda. |
| `job_timeout='12h'` | route v2 `data_parsing_queue.enqueue` | Satu job macet memblokir satu replika selama 12 jam. Ini risiko yang harus ikut diuji. |

## Metode yang dipakai

**Open-loop, bukan closed-loop.** Request dijadwalkan pada waktu absolut dan
tetap berangkat walaupun yang sebelumnya belum selesai — seperti kiriman HIS
rumah sakit yang tidak peduli server sedang sibuk. Closed-loop (model "N user"
milik Locust) akan ikut melambat saat server melambat, sehingga penumpukan
tersembunyi. Istilahnya *coordinated omission*.

**Kapasitas dinilai dari kemiringan antrean, bukan dari status HTTP:**

> **λ_max** = laju kedatangan tertinggi yang membuat kedalaman antrean tetap
> datar (dQ/dt ≈ 0) selama satu langkah penuh.

**Metrik yang dicatat:**

| Metrik | Kenapa penting |
|---|---|
| kedalaman antrean & kemiringannya | satu-satunya penanda stabil / tidak |
| umur job terdepan (lag) | perkiraan waktu tunggu yang dirasakan user |
| `enqueued_at → ended_at` | end-to-end sebenarnya |
| `started_at → ended_at` | waktu layanan murni, untuk sizing |
| worker sibuk vs total | membuktikan worker yang jadi bottleneck |
| kedalaman antrean hilir | membuktikan isolasi berhasil / ada cascade |

RQ sudah menyimpan `enqueued_at`, `started_at`, `ended_at` di tiap job, jadi
semua ini bisa dibaca langsung dari Redis tanpa menyentuh kode aplikasi.

## Empat pengujian, berurutan

| | Test | Pertanyaan yang dijawab | Perlu Redis |
|---|---|---|---|
| **T1** | `t1_service_time.py` | Berapa detik per encounter? Berapa overhead tetap per job? | ya |
| **T2** | `t2_ingress.py` | Apakah API pernah jadi bottleneck? | tidak |
| **T3** | `t3_saturation.py` | Berapa encounter/menit yang sanggup ditahan terus-menerus? | ya |
| **T4** | `t4_burst_drain.py` | Kalau 500 encounter masuk sekaligus, selesai jam berapa? | ya |
| **T5** | `t5_parallel_scaling.py` | Apakah 4 replika benar-benar memberi 4x? | ya |

**T1 adalah fondasinya** — angka di T3 dan T4 tidak bisa ditafsirkan tanpa tahu
biaya per encounter. T1 juga yang paling murah: sekitar 15 menit.

### Isolasi worker parsing

Kalau `IS_CODEX_API_V2_PROCESS=true`, worker parsing mengantre pekerjaan ke
analysis coordinator → analysis worker → **LLM coding berbayar** dan **dispatch
eKlaim** (`emr_integration_data_analysis_worker.py:363`). Yang terukur jadi
pipeline penuh, bukan worker parsing.

Di produksi, env var itu tidak bisa dimatikan begitu saja karena berlaku untuk
trafik asli juga. Solusinya ingress terpisah: `api-server_LOADTEST` dengan
`IS_CODEX_API_V2_PROCESS=false` (lihat `docker-compose.loadtest.yml`). Trafik
asli tetap dianalisis, trafik test berhenti di parsing.

Harness memantau antrean hilir dan memberi tahu kalau cascade tetap terjadi.
Ukur dua-duanya kalau perlu: isolasi untuk sizing worker parsing, non-isolasi
untuk kapasitas sistem sesungguhnya (tapi di produksi yang kedua berbiaya nyata).

### Baseline: syarat tambahan di produksi

Di produksi antrean tidak pernah kosong, jadi λ_max yang terukur adalah
**kapasitas tambahan di atas beban asli**, bukan kapasitas absolut. T3 mengukur
beban latar lebih dulu (`--baseline`) dan menolak melanjutkan kalau antrean sudah
menumpuk sebelum test dimulai — menambah beban saat itu hanya memperparah
keterlambatan pasien nyata.

## Paralelisme ditentukan pengirim, bukan ukuran request

Karena satu request = satu job = satu worker, jumlah request bersamaan-lah yang
menentukan berapa replika terpakai. Ukuran request tidak berpengaruh sama sekali:

| Cara kirim | Worker terpakai | Waktu selesai |
|---|---|---|
| 1 request × 400 encounter | 1 dari 4 | ~4× lebih lama |
| 4 request × 100 encounter | 4 dari 4 | ~1× |

Konsekuensi yang mudah terlewat: **kalau HIS mengirim satu batch besar per jam,
menambah replika worker tidak menaikkan apa pun.** Yang harus diubah adalah cara
pengirim memecah batch. T5 mengukur ini dan mencetak saran ukuran batch.

## Sizing dari hasil

Dengan `S` = detik per encounter (dari T1) dan `R` = jumlah replika (**saat ini 4**):

```
kapasitas teoretis = R × 3600 / S          encounter/jam
replika dibutuhkan = λ_target × S / 0.7    (0.7 = utilisasi aman)
```

Utilisasi jangan melewati 70–80%. Di atas itu waktu tunggu naik jauh lebih cepat
daripada beban — sifat sistem antrean, bukan bug.

Ekstrapolasi ×4 itu **hipotesis, bukan hasil ukur**. T3 mengujinya lewat rata-rata
worker sibuk pada saat antrean mulai menumpuk:

- **sibuk ≈ 4/4** → memang terbatas jumlah worker; menambah replika akan menolong
- **sibuk jelas di bawah 4 tapi antrean tetap menumpuk** → worker sedang menunggu
  sumber daya bersama. Tersangka berurutan: connection pool Postgres (4 worker +
  API berebut), advisory lock encounter, commit per 5 encounter di ingestor, dan
  disk `shared_data`. Di kondisi ini menambah replika justru memperburuk kontensi.

Untuk memetakan di mana penskalaannya patah, jalankan T3 pada 1, 2, lalu 4
replika dan bandingkan λ_max. Efisiensi penskalaan bisa dihitung otomatis dengan
`--theoretical-epm` diisi angka ekstrapolasi dari T1.

## Yang bisa membuat hasil menyesatkan

- **Kohort belum di-seed** — job selesai instan tanpa mengerjakan apa pun.
- **Encounter dipakai ulang antar request bersamaan** — advisory lock membuat
  worker saling menunggu; yang terukur jadi kontensi lock, bukan kapasitas.
- **`force_ingest_completed=false` pada run berulang** — encounter yang sudah
  stale hanya di-ingest billing-nya, beban jadi jauh lebih ringan dari run
  pertama. Harness men-set `true` secara default.
- **Payload template terlalu ringan** — beban worker ditentukan isi `sources`.
  Pakai payload produksi asli sebagai template, dan uji sensitivitasnya dengan
  `--weight`.
- **Load generator jadi bottleneck** — payload di sini berukuran MB. Harness
  mencatat *schedule lag* dan memperingatkan kalau generatornya yang tertinggal.
- **Lingkungan dipakai bersama** — beban lain di server yang sama akan mencemari
  pengukuran. Harness memeriksa antrean kosong sebelum mulai.
