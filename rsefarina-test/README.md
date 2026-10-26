# RSEFARINA API test

Script Python untuk testing API SIMRS RS Efarina (`POST /api/serenic`) per
resource & rentang tanggal, lalu membuat laporan konsistensi key dan temuan
yang mungkin belum sesuai format Serenic.

Hanya memakai standard library Python 3 (tanpa `pip install`). Semua request
read-only.

## Setup

```bash
cd /Users/miftahululum002/projects/serenic/experiments/rsefarina-test
cp .env.example .env
```

Isi `.env` (nilai sama dengan `.env` di `cron-api-scheduler`):

```
RSEFARINA_API_BASE_URL=
RSEFARINA_API_KEY=
```

`.env` dan `output/` tidak di-commit (`.gitignore`).

## Input registrasi

Default `registrasi.csv`:

```csv
noregistrasi,admission_type,period_start,period_end
REG/IP/261015-0001,ranap,"2026-10-15 02:03:00.000","2026-10-19 05:07:00.000"
```

- Kolom wajib: `noregistrasi` (juga diterima: `no_registrasi`, `registration_no`).
- `period_start` / `period_end` dipakai untuk `date_start` / `date_end`. Hanya
  tanggalnya yang dipakai (jam diabaikan).
- `admission_type`, `period_start`, `period_end` ditampilkan di laporan.
- Duplikat dibuang, urutan dipertahankan.

Tanpa kolom `period_start`, tanggal diambil dari no registrasi
(`REG/IP/261017-0014` → 2026-10-17).

## Cara pakai

```bash
python3 rsefarina_test.py                                  # registrasi.csv, rentang = periode
python3 rsefarina_test.py --mode variants                  # 7 variasi rentang
python3 rsefarina_test.py --mode variants --limit 10       # hanya 10 registrasi pertama
python3 rsefarina_test.py --mode variants --limit 10 --offset 10   # registrasi ke-11 s/d 20
python3 rsefarina_test.py data/lain.csv                     # CSV lain
python3 rsefarina_test.py --resources asesmen_awal resume_medis
python3 rsefarina_test.py --start 2026-10-17 --end 2026-10-27
```

| Opsi | Default | Keterangan |
|---|---|---|
| `file` | `registrasi.csv` | CSV berisi kolom `noregistrasi` |
| `--mode` | `single` | `single` atau `variants` |
| `--resources` | semua (11) | resource yang dites |
| `--start`, `--end` | – | override rentang (mode `single`) |
| `--env` | `./.env` | path `.env` |
| `--out` | `./output` | folder output |
| `--concurrency` | 3 | request paralel |
| `--timeout` | 60 | detik per request |
| `--retries` | 1 | retry untuk status 408/429/5xx & error koneksi |
| `--no-raw` | – | jangan simpan response API (default: disimpan) |
| `--limit` | semua | jumlah registrasi yang dites |
| `--offset` | 0 | lewati N registrasi pertama di CSV |

Exit code `1` kalau ada request yang error.

Resource: `triase_igd`, `diagnosis_aktif`, `diagnosis_awal`, `cppt`,
`asesmen_awal`, `resume_medis`, `penunjang_lab`, `laporan_operasi`,
`penunjang_radiologi`, `obat`, `billing`.

## Rentang tanggal

**`single`** (default): `--start`/`--end` kalau diisi; selain itu
`period_start..period_end` (label `periode`); tanpa rentang kalau periode
tidak ada.

**`variants`**: 7 rentang per registrasi.

| Label | `date_start` | `date_end` | Contoh REG/IP/261015-0001 (15–19 Okt) |
|---|---|---|---|
| `tanpa_rentang` | kosong | kosong | `..` |
| `periode` | period_start | period_end | `2026-10-15..2026-10-19` |
| `start..` | period_start | kosong | `2026-10-15..` |
| `start..end+1` | period_start | period_end + 1 | `2026-10-15..2026-10-20` |
| `start-1..end+1` | period_start − 1 | period_end + 1 | `2026-10-14..2026-10-20` |
| `cron_penuh` | period_start − 1 | besok | `2026-10-14..2026-10-27` |
| `cron_incremental` | hari ini − 2 | besok | `2026-10-24..2026-10-27` |

- `period_end` kosong (belum pulang) → besok.
- `cron_penuh` & `cron_incremental` meniru `dateHelper.buildDateRangeForEncounter`
  di `cron-api-scheduler` (incremental diasumsikan checkpoint ≈ hari ini).
- Jumlah request = registrasi × rentang × resource. 66 registrasi: `single`
  726 request (~1,5 menit), `variants` 5.082 request (±9 menit) — perhatikan
  beban ke server RS. Batasi dengan `--limit`/`--offset` dan `--resources`,
  mis. `--mode variants --limit 10` = 10 × 7 × 11 = 770 request.

## Output

Satu folder per run, `output/<YYYYMMDD>/<HHMMSS>/`, berisi raw response dan
laporannya:

```
output/20261026/221047/
  REG-IP-261015-0001/periode/asesmen_awal.json         # response API apa adanya
  REG-IP-261015-0001/periode/triase_igd.error.json     # kalau gagal: request, status, body error
  summary.json                                   # status, durasi, ringkasan isi per request
  key_consistency.txt                            # konsistensi key (teks)
  laporan.md                                     # laporan lengkap
```

### Laporan (`laporan.md`)

1. **Ringkasan** — jumlah error/warning/info per aturan, registrasi
   terdampak, dan apakah sudah ditangani mapper cron.
2. **Isi per registrasi** — total item per resource (rentang dasar:
   `tanpa_rentang`, lalu `periode`).
3. **Konsistensi key** — per section, digabung dari semua registrasi & rentang:
   key yang tidak selalu ada, tipe campur, field selalu `null`, section yang
   selalu kosong.
4. **Detail temuan** — per aturan, dengan registrasi, lokasi, contoh id.

## Aturan temuan (`report.py`)

| Aturan | Level | Deteksi | Ditangani mapper cron |
|---|---|---|---|
| `request-error` | ❌ | request gagal | – |
| `concept-array` | ⚠️/❌ | `concept` berupa array (Serenic: object); ⚠️ di `resume_medis.pemeriksaan_fisik` (selalu array, dinormalisasi mapper), ❌ di section lain | `mapPemeriksaanFisikConcept` (resume_medis) |
| `value-type-mismatch` | ❌ | `value` tidak sesuai `value_type` | belum |
| `duplicate-id` | ❌ | id ganda dalam satu section | hanya `obat.obat` |
| `obat-reference` | ❌ | `medication_id` / `medication_request_id` tidak ditemukan | `dropDanglingObatReferences` |
| `service-request-reference` | ❌ | `service_request_id` laporan tidak ada di `pemesanan` source yang sama | belum |
| `range-data-loss` | ❌/⚠️ | (variants) data berkurang dibanding `tanpa_rentang`; ❌ untuk resource sekali per encounter | belum |
| `range-error` | ❌ | (variants) rentang error, tanpa rentang OK | belum |
| `service-request-order` | ⚠️ | `laporan_radiologi.service_request_id` di asesmen_awal/resume_medis | `dropRadiologiServiceRequestId` |
| `category-uppercase` | ⚠️ | `category` huruf besar (`SOAP`, `RAD`) | hanya `SOAP` |
| `performer-format` | ⚠️ | performer tidak berpola `MD-xxxxx` | `mapDispensePerformerId` |
| `diagnosis-code` | ⚠️/ℹ️ | `code` = `""` (⚠️) / semua `null` (ℹ️) | belum |
| `prosedur-null` | ⚠️ | `prosedur.status` / `category` null | belum |
| `assessment-text-missing` | ⚠️ | `assessment_text` kosong padahal section lain ada | `ensureAssessmentText` |
| `billing-null-period` | ⚠️ | `billable_period_start` null | belum |
| `placeholder-medication` | ⚠️ | `riwayat_penggunaan_obat` tanpa `medication_name` | belum |
| `empty-entry` | ℹ️ | entry dikirim tapi semua section kosong | entry kosong dibuang |
| `blank-string` | ℹ️ | field string kosong `""` | belum |

Kolom "ditangani mapper" merujuk ke
`cron-api-scheduler/src/modules/hospitals/rsefarina/mapper/index.js`
(`MAPPER_HANDLED` di `report.py`). Laporan memeriksa **raw response API**,
bukan hasil mapper.

## Hasil run 2026-10-26

### `single` (periode), 66 registrasi — `output/20261026/221047/laporan.md`

726 request, 0 error.

| Temuan | Registrasi | Ditangani mapper |
|---|---|---|
| `concept-array` | 60/66 | ya |
| `obat-reference` | 48/66 | ya |
| `duplicate-id` | 42/66 | hanya `obat.obat` |
| `prosedur-null` | 41/66 | belum |
| `service-request-order` | 34/66 | ya |
| `placeholder-medication` | 31/66 | belum |
| `diagnosis-code` (`""`) | 13/66 | belum |
| `assessment-text-missing` | 13/66 | ya (placeholder `-`) |

### `variants`, 6 registrasi sampel (2 rajal, 4 ranap) — `output/20261026/221204/laporan.md`

462 request, 0 error. Registrasi yang kehilangan data dibanding `tanpa_rentang`:

| Varian | `asesmen_awal` | `resume_medis` | `triase_igd` | `laporan_operasi` | `billing` |
|---|---|---|---|---|---|
| `periode` | 4/6 | 4/6 | 1/6 | – | 1/6 |
| `start..` | – | – | – | – | 1/6 |
| `start..end+1` | 4/6 | 4/6 | 1/6 | – | 1/6 |
| `start-1..end+1` | 6/6 | 6/6 | 6/6 | 2/6 | 1/6 |
| `cron_penuh` | 6/6 | 6/6 | 6/6 | 2/6 | 1/6 |

- `start..` (`date_end` kosong) hampir tanpa kehilangan; hanya billing
  bertanggal `null`.
- `start-1` / `cron_penuh` selalu kehilangan `asesmen_awal`, `resume_medis`,
  `triase_igd`.

Lihat juga `cron-api-scheduler/notes/rsefarina-*.md`.

## File

| File | Isi |
|---|---|
| `rsefarina_test.py` | CLI: baca input, request API, simpan output |
| `report.py` | konsistensi key, aturan temuan, render laporan Markdown |
| `registrasi.csv` | input default |
| `.env.example` | template credential |
