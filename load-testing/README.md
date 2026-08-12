# Load testing — `emr-integration-data-parsing-worker_PROD`

Harness untuk mengukur kapasitas jalur `POST /integrations/v2/encounters/update`
→ `data_parsing_queue` → worker parsing.

Latar belakang metodenya ada di **[METODE.md](METODE.md)**. Baca itu dulu kalau
belum jelas kenapa RPS bukan satuan yang tepat di sini.

## Menguji di produksi

Target pengujian ini adalah **server produksi**. Itu keputusan yang sah — sering
kali staging tidak merepresentasikan beban asli — tapi ada tiga konsekuensi yang
harus ditangani lebih dulu.

### 1. Cascade: parsing bukan ujung rantai

```
POST /encounters/update
  → parsing worker
      → analysis coordinator
          → analysis worker  ── LLM coding (token berbayar)
                             └─ dispatch eKlaim  (emr_integration_data_analysis_worker.py:363)
                                  → eklaim-batch-worker → pool server eKlaim
```

Lewat `api-server_PROD`, encounter sintetis Anda akan **memakai token LLM
sungguhan** dan **memakai kapasitas server eKlaim** yang dipakai klaim asli.

`IS_CODEX_API_V2_PROCESS` adalah env var global — mematikannya di
`api-server_PROD` berarti mematikan analisis untuk trafik asli juga. Tidak bisa.

**Jalan keluarnya: ingress terpisah.** `docker-compose.loadtest.yml` menambahkan
`api-server_LOADTEST` — image, database, dan Redis yang sama, tapi
`IS_CODEX_API_V2_PROCESS=false` dan port sendiri yang hanya bind ke localhost:

```bash
docker compose -f docker-compose.app.prod.yml -f docker-compose.loadtest.yml \
  up -d api-server_LOADTEST
ssh -N -L 8099:127.0.0.1:${LOADTEST_API_PORT} user@server
```

Lalu di `.env`: `API_BASE_URL=http://127.0.0.1:8099`, `AUTH_MODE=direct`,
`CONSUMER_ID=<org>`.

Trafik asli tetap lewat `api-server_PROD` dan tetap dianalisis. Trafik test
berhenti di worker parsing. Worker parsing tetap dipakai bersama — memang itu
yang ingin diukur.

Hapus container-nya setelah selesai:

```bash
docker compose -f docker-compose.app.prod.yml -f docker-compose.loadtest.yml \
  rm -sf api-server_LOADTEST
```

### 2. Beban test berebut worker dengan pasien nyata

Keempat replika parsing dipakai bersama. Kalau Anda mendorong sampai λ_max,
encounter pasien asli akan mengantre di belakang encounter sintetis Anda.

Mitigasinya:

- Jalankan di **jam paling sepi**. Cari jamnya dulu dengan
  `python tools/queue_sampler.py --interval 5` selama sehari penuh.
- T3 mengukur **beban latar** (`--baseline 120`) sebelum menambah beban, dan
  akan menolak melanjutkan kalau antrean sudah menumpuk sebelum test dimulai.
- T3 berhenti otomatis pada langkah pertama yang menumpuk
  (`--stop-after-unstable 1`, default).
- Siapkan tombol darurat sebelum mulai: `python tools/abort.py`.

Urutan risiko dari yang paling ringan: **T1** (satu job pada satu waktu, dampak
minimal) → **T4** dengan `--total` kecil → **T3** (sengaja mendorong melewati
kapasitas — ini yang paling berisiko, jadwalkan di jendela pemeliharaan).

### 3. Data sintetis masuk ke database produksi

Encounter `LT-<runid>-*` akan muncul di data organisasi yang dipakai — termasuk
di webapp dan dashboard pengguna.

**Gunakan organisasi khusus test**, bukan `apiKey`/`CONSUMER_ID` milik rumah
sakit yang sedang berjalan. Ini mitigasi paling penting dan paling murah.

Semua identifier tercatat lengkap di `results/cohort.json`, jadi pembersihan
bisa ditarget persis. Harness sengaja tidak menghapus otomatis.

### Kalau λ_max harus diukur absolut

Angka λ_max dari T3 di produksi adalah **kapasitas tambahan di atas beban asli**,
bukan kapasitas total. Untuk angka absolut, ulangi di jam ketika beban asli
mendekati nol. T3 mencetak kedua tafsiran itu.

## Persiapan

```bash
cd experiments/load-testing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Profil target

Konfigurasi dibagi dua lapis supaya kredensial tidak perlu diduplikasi dan
target tidak mungkin tertukar:

| File | Isi |
|---|---|
| `.env` | milik bersama: `TEMPLATE_PAYLOAD`, output |
| `.env.dev` | staging `api.staging.serenic.ai` — 1 replika, gateway, **apiKey staging** |
| `.env.prod` | produksi via `api-server_LOADTEST` — 4 replika, direct, **apiKey prod** |

apiKey staging dan produksi berbeda, jadi **tiap profil memegang kuncinya
sendiri** — tidak ada kunci bersama di `.env` yang bisa tidak sengaja terpakai
untuk target yang salah.

Profil dipilih dengan salah satu dari, berurutan:

1. **`APP_MODE` di `.env`** — set sekali, lalu perintah ditulis biasa:
   ```
   APP_MODE=dev
   ```
   ```bash
   python tests/t1_service_time.py --sizes 1,5 --repeats 1
   ```
2. **`PROFILE` di depan perintah** — menimpa `APP_MODE`, untuk sekali jalan:
   ```bash
   PROFILE=prod python tests/t1_service_time.py --sizes 1,5,10,25,50 --repeats 2
   ```

Shell sengaja menang atas `.env`. Arah itu juga yang aman: `APP_MODE=dev` yang
terlupakan hanya membuat perintah mengenai staging, sedangkan `PROFILE=prod`
yang diketik eksplisit selalu dihormati.

Setiap perintah mencetak profil aktif **beserta asalnya**, supaya `APP_MODE`
yang tertinggal dari sesi lain tidak menyesatkan:

```
[profil aktif: dev dari APP_MODE di .env]  apiKey sha256:63d41663 (panjang 32)
```

Prioritas nilai: env var shell → `.env.<profil>` → `.env`. Jadi override sekali
jalan bisa langsung di depan perintah, mis. `WORKER_REPLICAS=2 python tests/...`.

Setiap profil menulis ke direktori hasil sendiri (`results/dev`, `results/prod`)
dan memakai prefix identifier sendiri (`LTDEV-`, `LT-`) supaya data dan hasilnya
tidak mungkin tercampur.

Yang masih perlu Anda isi:

- `.env.dev` → `API_KEY` (kunci staging), `REDIS_URL`
- `.env.prod` → `CONSUMER_ID` (organisasi khusus test), `API_KEY` (kunci
  produksi — hanya terpakai kalau `AUTH_MODE` diubah ke `gateway`, tapi tetap
  diisi supaya berpindah mode tidak diam-diam memakai kunci staging), `REDIS_URL`
- keduanya → `PARSING_QUEUE` dan antrean hilir, dari `tools/inspect_redis.py`

Harness menolak jalan kalau masih ada placeholder `<ISI...>` yang belum diganti.

Setiap perintah mencetak **sidik jari kredensial** yang sedang dipakai, misalnya
`apiKey sha256:45ba26b5 (panjang 18)`. Cocokkan sekali di awal; setelah itu
sidik jari yang tidak sesuai dengan profilnya langsung terlihat sebelum request
pertama dikirim. Nilai kuncinya sendiri tidak pernah dicetak.
- `REDIS_URL` — kalau Redis tidak publik, buat tunnel dulu:
  `ssh -N -L 6380:127.0.0.1:6379 user@server` lalu pakai `redis://127.0.0.1:6380/0`
- `TEMPLATE_PAYLOAD` — satu payload `/encounters/update` produksi yang asli

Lalu temukan nama antrean yang sebenarnya (berasal dari env var di server,
tidak bisa ditebak dari kode) — untuk tiap profil:

```bash
PROFILE=dev  python tools/inspect_redis.py
PROFILE=prod python tools/inspect_redis.py
```

Sekaligus ini memverifikasi hal penting: **Redis staging harus terpisah dari
Redis produksi.** Kalau nama antrean dan isinya sama persis di kedua profil,
berarti staging berbagi Redis dengan produksi dan "gladi bersih di staging"
sebenarnya menulis ke produksi.

Salin nama antrean parsing ke `PARSING_QUEUE`, dan antrean analisis ke
`ANALYSIS_COORDINATOR_QUEUE` (untuk memantau cascade).

Isolasi worker parsing dilakukan lewat `api-server_LOADTEST` (lihat bagian
produksi di atas), bukan dengan mengubah `prod.env` `api-server_PROD`.

## Urutan menjalankan

### Fase 1 — gladi bersih di staging (~30 menit)

Spesifikasi server, ukuran database, dan jumlah replika staging berbeda dari
produksi, jadi **angka kapasitas dari sini tidak berlaku di produksi**. Tujuannya
hanya membuktikan harness-nya jalan sebelum menyentuh produksi:

```bash
PROFILE=dev python tools/inspect_redis.py
PROFILE=dev python tools/seed_encounters.py --count 30
PROFILE=dev python tests/t1_service_time.py --sizes 1,5 --repeats 1
PROFILE=dev python analyze/report.py results/dev/*_queue.csv
```

Sukses = payload diterima, job ketemu di antrean, timing terbaca, CSV dan
grafik jadi. **Abaikan angkanya.** Yang ditangkap di sini: payload ditolak 400,
nama antrean salah, master data inline ditolak saat seed, layout key RQ berbeda.

### Fase 2 — pengukuran sebenarnya di produksi

Semua perintah di bawah pakai `PROFILE=prod`.

### 0. Seed kohort — wajib

Encounter yang tidak ada di DB akan dilewati worker (`ingestor.py:613`), jadi
tanpa langkah ini semua job selesai dalam milidetik dan hasilnya palsu.

```bash
python tools/seed_encounters.py --count 600
```

Sediakan berlebih: tiap request test memakai potongan encounter yang berbeda
supaya tidak saling menunggu advisory lock.

### 1. T1 — biaya per encounter (~15 menit)

```bash
python tests/t1_service_time.py --sizes 1,5,10,25,50 --repeats 2
```

Keluaran: `durasi_job = a + b × jumlah_encounter`. Nilai `b` adalah detik per
encounter; kapasitas satu replika = `3600 / b` encounter/jam, kapasitas armada =
dikalikan 4 replika. T1 juga mencetak perintah T3 yang sudah terisi langkahnya.

T1 sengaja mengirim satu request pada satu waktu, jadi hanya satu dari empat
replika yang terpakai — itu memang tujuannya: mengukur waktu layanan murni tanpa
kontensi. T3 yang membuktikan apakah ×4 benar-benar tercapai.

Uji juga sensitivitas ukuran payload:

```bash
python tests/t1_service_time.py --sizes 10 --repeats 3 --weight 3
```

### 2. T2 — batas sisi API (~10 menit, opsional)

```bash
python tests/t2_ingress.py --rates 6,12,24,48 --duration 120 --encounters 5
```

Biasanya menunjukkan API jauh di atas worker. Kalau begitu, abaikan sisi API.

### 3. T3 — titik jenuh (~1 jam)

Pakai langkah yang dicetak T1 (40%, 70%, 100%, 140% dari kapasitas armada).
Contoh kalau T1 memberi 15 encounter/menit per replika → armada 4 replika = 60:

```bash
python tests/t3_saturation.py --steps 24,42,60,84 --encounters 10 --step-duration 600
```

Pastikan `--encounters` cukup kecil sehingga tiap langkah menghasilkan jauh
lebih banyak request daripada 4 — kalau tidak, sebagian replika menganggur dan
λ_max terukur terlalu rendah.

Keluaran: **λ_max** dalam encounter/menit. Ini jawaban "sanggup berapa".

`--step-duration` minimal 5× durasi satu job, kalau tidak kemiringan antrean
cuma derau.

### 3b. T5 — penskalaan paralel (~15 menit)

Satu request = satu job = **satu worker**, berapa pun encounter di dalamnya.
Jadi ekstrapolasi "4 replika = 4×" adalah hipotesis yang harus dibuktikan:

```bash
python tests/t5_parallel_scaling.py --levels 1,2,4 --encounters 10
```

Metrik paling diagnostiknya bukan throughput, tapi **waktu layanan per job**.
Kalau paralelisme sehat, satu job selesai dalam waktu yang sama walaupun ada 4
job berjalan bersamaan. Kalau membengkak, worker saling menghambat lewat
connection pool Postgres, advisory lock encounter, atau disk `shared_data`.

T5 juga mencetak saran ukuran batch untuk sisi pengirim.

### 4. T4 — ledakan beban (~30 menit)

```bash
python tests/t4_burst_drain.py --total 500 --encounters 25 --slo-minutes 30
```

Keluaran: waktu pengosongan dan persentase job yang memenuhi SLO.

### 5. Grafik

```bash
python analyze/report.py results/t3_saturation_*_queue.csv
```

Menghasilkan PNG tiga panel: kedalaman antrean, lag tertua, worker sibuk.
Datar = stabil. Naik = sudah lewat kapasitas.

## Uji penskalaan replika

Saat ini `emr-integration-data-parsing-worker_PROD` jalan **4 replika**
(`WORKER_REPLICAS=4` di `.env`). Karena satu container RQ mengerjakan satu job
pada satu waktu, itu berarti konkurensi parsing = 4.

T3 sudah otomatis mendiagnosis apakah angka 4 itu yang membatasi, dengan
membandingkan rata-rata worker sibuk terhadap jumlah replika saat antrean mulai
menumpuk:

- **sibuk ≈ 4/4** → memang terbatas jumlah worker, menambah replika akan menolong
- **sibuk < 3.4/4 tapi antrean menumpuk** → worker menunggu sumber daya bersama
  (connection pool Postgres, advisory lock encounter, commit DB, disk
  `shared_data`). Menambah replika justru bisa memperburuk kontensi.

Untuk memetakan di mana penskalaannya patah, jalankan T3 pada 1, 2, lalu 4
replika dan bandingkan λ_max:

```bash
docker compose -f docker-compose.app.prod.yml up -d \
  --scale emr-integration-data-parsing-worker_PROD=2
# perbarui WORKER_REPLICAS=2 di .env, lalu jalankan ulang T3
```

Service ini tidak memakai `container_name`, jadi `--scale` bisa langsung dipakai.

Untuk mengukur efisiensi penskalaan terhadap ekstrapolasi T1:

```bash
python tests/t3_saturation.py --steps ... --theoretical-epm <angka dari T1>
```

## Memantau tanpa memberi beban

```bash
python tools/queue_sampler.py --interval 5 --out results/harian.csv
```

Berguna dijalankan pada jam sibuk produksi untuk mengetahui bentuk trafik yang
sebenarnya, sebelum menentukan target pengujian.

## Arsip request & response

Setiap request dan response disimpan mengikuti konvensi server:

```
payload/{ORGID}/{yyyymmdd}/{nama_endpoint}_{yyyymmdd}_{HHMMSS}_{ms}_{seq}.json
response/{ORGID}/{yyyymmdd}/{nama_endpoint}_{yyyymmdd}_{HHMMSS}_{ms}_{seq}.json
```

Nomor `{seq}` **sama untuk pasangan request-response**, dan file response juga
menyimpan nama file request-nya di field `request_file` — jadi bisa ditelusuri
bolak-balik.

Nama endpoint diturunkan dari URL dengan konvensi yang sama seperti
`save_request_to_file` di server: `/encounters/update` → `update_encounters`,
`/encounters/new` → `new_encounters`.

Milidetik dan nomor urut ditambahkan karena beban test mengirim banyak request
dalam detik yang sama — tanpa itu file akan saling menimpa.

File response berisi status dan waktu, bukan cuma body — saat beban tinggi yang
paling sering dicari justru status code dan pesan error:

```json
{
  "endpoint": "update_encounters",
  "seq": 2,
  "request_file": "update_encounters_20260812_231038_170_000002.json",
  "status_code": 400,
  "ok": false,
  "latency_ms": 0.1,
  "received_at": "2026-08-12T23:10:38.170",
  "body": { "detail": "Validation failed. Request ID: req-def" }
}
```

Kegagalan transport (timeout, connection reset) juga diarsipkan dengan
`status_code: 0` dan field `error` — itu menandai tidak ada respons sama
sekali, berbeda dari server yang membalas error.

**Penulisan dilakukan di thread terpisah.** Ini bukan detail implementasi yang
bisa diabaikan: kalau file 53 KB–6 MB ditulis di jalur request, angka latency
yang diukur ikut memuat biaya disk lokal dan pengukurannya tidak sah. Biaya di
jalur pemanggil terukur ~7 mikrodetik per request. Konsekuensinya, kalau proses
dihentikan paksa, beberapa payload terakhir bisa belum sempat tertulis.

Pengaturan di `.env`:

| | |
|---|---|
| `SAVE_PAYLOADS=1` / `SAVE_RESPONSES=1` | matikan dengan `0`, terpisah |
| `PAYLOAD_DIR=payload` / `RESPONSE_DIR=response` | direktori akar |
| `ORGID=` | nama folder organisasi; kosong → `ORG_LABEL` → `CONSUMER_ID` |
| `PAYLOAD_MAX_MB=2000` / `RESPONSE_MAX_MB=500` | pengaman disk, budget terpisah |

Satu encounter ~53 KB, jadi T3 yang panjang bisa menghasilkan puluhan GB.
Setelah batas tercapai pengarsipan berhenti tapi **test tetap berjalan** —
arsip tidak pernah dibiarkan menggagalkan pengukuran. Response jauh lebih kecil
(~1 KB), karena itu budget-nya terpisah: payload boleh mentok tanpa ikut
mematikan arsip response yang justru paling berguna untuk diagnosis.

## Struktur

```
config.py                 konfigurasi dari .env
lib/payload.py            kloning payload template + remap id
lib/payload_store.py      arsip payload terkirim (thread terpisah)
lib/rq_probe.py           baca state RQ langsung dari Redis (tanpa unpickle)
lib/driver.py             driver beban open-loop
lib/sampler.py            sampler kedalaman antrean
tools/inspect_redis.py    verifikasi koneksi + temukan nama antrean
tools/seed_encounters.py  siapkan kohort encounter
tools/queue_sampler.py    pemantau live
tests/t1..t5              skenario pengujian
analyze/report.py         grafik + ringkasan teks
```

## Siklus hidup kohort

Kohort **dipakai ulang** untuk semua test — tidak perlu seed ulang tiap kali.
`build_update_request` mengirim `force_ingest_completed=true`, jadi update
berulang pada encounter yang sama tetap mengerjakan beban penuh.

```
seed sekali  →  T1, T2, T3, T4, T5 berkali-kali  →  cleanup di akhir
```

Hapus hanya kalau memang selesai, atau kalau butuh jenis encounter berbeda
(`--admission rajal`, misalnya).

**Kurang encounter?** Tambah, jangan seed ulang:

```bash
python tools/seed_encounters.py --count 600 --append
```

Tanpa `--append`, kohort lama diarsipkan ke `cohort_<timestamp>.json` dan
harness memberitahu bahwa encounter lamanya **masih ada di database** beserta
perintah untuk menghapusnya. Sebelumnya berkas itu ditimpa diam-diam, dan
encounter lama jadi yatim — hanya bisa dibersihkan lewat `--prefix`.

## Pembersihan

`tools/cleanup_encounters.py` menghapus encounter beserta seluruh turunannya
(31 tabel, urutan menghormati foreign key — disalin dari
`experiments/testing-ingestor/utils/query.py`). Satu encounter = satu
transaksi, jadi kegagalan pada satu encounter tidak membatalkan sisanya.

Butuh `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` di `.env`.

```bash
python tools/cleanup_encounters.py                 # dry-run dari cohort.json
python tools/cleanup_encounters.py --apply         # hapus, minta konfirmasi
python tools/cleanup_encounters.py --prefix LTDEV  # sapu sisa run lama
```

Pengaman:

- **dry-run default**, dan `--apply` masih meminta diketik `hapus`
- selalu dibatasi satu `managing_organization`
- menolak jalan tanpa kohort atau prefix — tidak ada mode "hapus semua"
- memperingatkan kalau ada identifier yang **tidak** berawalan
  `SYNTHETIC_PREFIX`, karena kemungkinan besar itu bukan data test
- setelah berhasil, `cohort.json` dipindahkan ke `cohort_terhapus.json`

Pemindahan kohort itu penting: kohort yang encounter-nya sudah dihapus akan
membuat test berikutnya **diam-diam mengukur nol**. Ingestor melewati encounter
yang tidak ditemukan (`ingestor.py:613`), job selesai dalam milidetik, dan tidak
ada satu pun pesan error.

**Tidak dihapus**: master data. Praktisi yang tercipta dari `performer_id`
objek memakai id asli dari template (mis. `23350`), jadi tidak bisa dibedakan
dari praktisi sungguhan. Bersihkan manual kalau perlu.
