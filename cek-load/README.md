# cek-load — Cek Kapasitas Worker Background (Redis/RQ)

Sekumpulan script read-only untuk menjawab satu pertanyaan: **apakah server
worker sudah mencapai kapasitas maksimalnya atau belum?**

Semua data diambil murni dari telemetri RQ di Redis — tidak perlu SSH, gcloud,
atau akses ke host aplikasi. Semua script **hanya membaca**, tidak pernah
menulis/menghapus apa pun di Redis.

Hasil analisis terakhir ada di [`LAPORAN.md`](LAPORAN.md).

---

## Prasyarat

Redis prod harus bisa diakses di `127.0.0.1:6379` (port-forward / tunnel).
Pastikan port-forward sudah aktif sebelum menjalankan apa pun:

```bash
nc -z 127.0.0.1 6379 && echo "REDIS OK" || echo "REDIS BELUM TERSAMBUNG"
```

---

## Setup (sekali saja)

```bash
cd experiments/cek-load

python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Kredensial Redis — salin dari project tetangga, lalu sesuaikan bila perlu
grep -E '^(REDIS_|AGENT_QUEUE_)' ../locust/.env > .env
```

Isi `.env` yang dipakai:

| Variabel | Keterangan |
|---|---|
| `REDIS_HOST`, `REDIS_PORT` | alamat Redis (lewat port-forward) |
| `REDIS_USER`, `REDIS_PASSWORD` | kredensial, boleh kosong |
| `AGENT_QUEUE_DATA_PARSING` | queue yang dianalisis, default `integration_data_parsing_agent_prod` |
| `HOST_SSH` | *(opsional)* target SSH VM worker — hanya bila CPU/RAM mau dibaca dari laptop |
| `HOST_SSH_CMD` | *(opsional)* pembungkus SSH khusus, mis. `gcloud compute ssh … --command` |

> Opsional: `pip install serenic_mlkit` bila ingin `payload.py` membaca isi job
> lebih dalam. Tanpa itu pun `payload.py` tetap jalan (pakai stub generik).

---

## Cara Cepat — Satu Perintah

Untuk sekadar mendapat jawabannya, tidak perlu menjalankan script satu per satu.
`report.py` menjalankan seluruh rangkaian pemeriksaan, menarik kesimpulan dari
angka yang terukur, lalu menulis laporan Markdown:

```bash
./.venv/bin/python report.py                        # ±8 menit, hasil ke reports/
./.venv/bin/python report.py --output LAPORAN.md    # timpa laporan utama
./.venv/bin/python report.py --minutes 15           # sampling lebih panjang
./.venv/bin/python report.py --skip-fate            # lebih cepat (±6 menit)
./.venv/bin/python report.py --queue ocr_agent_prod # queue lain
```

Ringkasan kesimpulan juga dicetak ke terminal di akhir, mis.:

```
Kesimpulan: pool MENTOK, backlog TURUN, throughput 378 job/jam
```

> **Durasi sampling menentukan ketelitian.** Run 1–2 menit bisa meleset jauh
> (mis. 477 vs 560 job/jam pada dua run berturutan) karena hanya menangkap
> belasan job. Pakai minimal `--minutes 6`; untuk angka yang dipakai mengambil
> keputusan, `--minutes 15` ke atas.

Ambang batas kesimpulan ada di konstanta paling atas `report.py`
(`SATURATION_RATIO`, `RECENT_FAILURE_H`) bila perlu disesuaikan.

---

## Menjalankan di VM Server (mengukur CPU & RAM)

Dijalankan dari laptop, script ini hanya bisa membuktikan *pool worker* mentok —
tidak bisa memastikan apakah **mesinnya** yang jadi penghambat. Jalankan di VM
yang menjalankan worker, dan CPU/RAM ikut terukur otomatis:

```bash
# di VM
git clone <repo> && cd experiments/cek-load
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# Redis diakses langsung, bukan lewat port-forward
cp .env.example .env    # set REDIS_HOST/REDIS_PORT sesuai VM

./.venv/bin/python report.py --minutes 15
```

Cek metrik mesin saja, tanpa menyentuh Redis:

```bash
./.venv/bin/python host.py                 # potret + sampling CPU/RAM 5 detik
./.venv/bin/python host.py --seconds 30    # sampling lebih panjang
```

`host.py` membaca `/proc` langsung memakai pustaka standar Python — **tidak
perlu `psutil` atau paket tambahan apa pun**. Kalau dijalankan di luar Linux
tanpa `--ssh`, bagian ini otomatis dilewati (`report.py` tetap jalan, hanya bab
CPU/RAM yang diganti catatan).

**Yang ikut terukur:** CPU terpakai (rata-rata/puncak/p90, dipecah jadi
user/system/iowait/**steal**), jumlah core yang jenuh >90%, load average per
core, PSI (`/proc/pressure`), serta proses paling boros CPU dan RAM.

**RAM diukur sepanjang jendela pengamatan**, bukan sekali di akhir — tiap tick
sampling ikut membaca `/proc/meminfo`, sehingga laporan menampilkan pemakaian
rata-rata, **puncak**, sisa (`MemAvailable`) terkecil, serta pertumbuhan swap
selama observasi. Ini penting karena RAM biasanya memuncak saat beberapa job
besar kebetulan berjalan bersamaan, lalu turun lagi begitu job selesai — potret
sesaat gampang melewatkan momen itu. Penilaian "mesin mentok" memakai angka
**puncak**, bukan rata-rata.

### Kalau terpaksa dijalankan dari laptop

CPU/RAM tetap bisa dibaca dari VM lewat SSH — `/proc` VM diambil dari jauh,
satu koneksi per sampling (SSH `ControlMaster` dipakai ulang):

```bash
./.venv/bin/python report.py --minutes 15 --ssh serenic-prod.asia-southeast2-a.serenic-aurio-mvp
./.venv/bin/python host.py --ssh serenic-prod.asia-southeast2-a.serenic-aurio-mvp
```

Target bisa juga disimpan permanen di `.env` sebagai `HOST_SSH`. Untuk akses
yang butuh pembungkus lain (mis. IAP), pakai `HOST_SSH_CMD` / `--ssh-cmd` —
skrip remote ditambahkan sebagai argumen terakhir:

```bash
HOST_SSH_CMD="gcloud compute ssh serenic-prod --zone asia-southeast2-a --tunnel-through-iap --command"
```

> Jalur SSH hanya cadangan; kalau bisa, jalankan langsung di VM. Tiap tick lewat
> `gcloud` memakan beberapa detik, sedangkan `ssh` biasa hanya puluhan milidetik.

**Efeknya ke kesimpulan** — bab "Yang Belum Bisa Dipastikan" berganti menjadi
"Kondisi Mesin", dan jawaban utamanya jadi tegas:

| CPU rata-rata | Kesimpulan | Rekomendasi utama |
|---|---|---|
| ≥ 85% | Mesin **ikut** mentok | Tambah/besarkan mesin — menambah worker tidak menolong |
| < 70% | Mesin **belum** mentok | Tambah worker untuk queue yang menumpuk |
| 70–85% | Mendekati batas | Tambah worker sedikit demi sedikit sambil dipantau |

RAM ≥ 90% terpakai (dilihat dari **puncak** selama observasi) atau swap > 64 MB
juga dihitung sebagai mesin mentok — memori biasanya menghambat lebih dulu
daripada CPU. Ambangnya ada di konstanta atas `host.py` (`CPU_SATURATED`,
`CPU_ROOMY`, `MEM_TIGHT_PCT`).

> Kalau worker berjalan di dalam container, jalankan tetap **di VM host**-nya
> (bukan `docker exec` ke dalam container) agar yang terbaca CPU/RAM seluruh
> mesin, bukan hanya satu container.

---

## Cara Manual — Per Langkah

Pakai ini kalau ingin menginspeksi satu aspek saja, atau ingin melihat sendiri
angka mentahnya. Jalankan **berurutan 1 → 6**. Langkah 1–3 adalah inti
jawabannya; 4–6 untuk memastikan tidak salah tafsir.

### Langkah 1 — Potret kondisi sekarang

```bash
./.venv/bin/python snapshot.py
```

Menampilkan seluruh queue (pending/started/failed/finished), 54 worker beserta
status dan queue yang dilayaninya, kesehatan Redis, serta job yang sedang jalan.

**Yang dibaca:**
- Queue mana yang `pending`-nya menumpuk.
- Pada tabel *queue yang dilayani*: kalau `idle = 0` untuk queue tersebut →
  **pool worker-nya jenuh**.
- Bagian Redis: `evicted_keys` dan `rejected_connections` harus **0**. Kalau
  tidak nol, Redis sendiri yang bermasalah.

### Langkah 2 — Tren backlog (naik atau turun?)

```bash
./.venv/bin/python monitor.py --minutes 8 --interval 10
```

Sampling backlog tiap 10 detik selama 8 menit, mencatat ke `results/*.csv`.

**Yang dibaca** — kolom `trend`:
- Dominan **NAIK** → job masuk lebih cepat daripada diproses; **server jenuh**.
- Dominan **TURUN** → beban masuk sudah berhenti, tinggal menguras backlog.
- **FLAT** terus-menerus → tidak ada progres, curigai worker macet/mati.

### Langkah 3 — Throughput & durasi job (angka kapasitasnya)

```bash
./.venv/bin/python throughput.py --minutes 6 --interval 3
```

Ini pengukuran paling penting. Throughput dihitung dari pergantian job ID di
`started_job_registry` — bukan dari delta `finished` (kena TTL) atau delta
`pending` (terkontaminasi job baru masuk).

**Yang dibaca:**
- `THROUGHPUT` — kapasitas nyata dalam job/jam.
- `Slot paralel aktif` — kalau `min = max` dan sama dengan jumlah worker →
  pool terpakai penuh 100%.
- **Bandingkan `THROUGHPUT` dengan `kapasitas teoritis`.** Kalau keduanya nyaris
  sama → pool sudah mentok, tidak ada sisa kapasitas.
- `LAJU MASUK (est.)` — positif berarti beban masuk masih berjalan.
- `WAKTU TUNGGU DI ANTREAN` — berapa lama job terdepan mengantre.

### Langkah 4 — Pastikan job benar diproses, bukan dibuang

```bash
./.venv/bin/python dequeue_fate.py --wait 120
```

Kadang backlog turun jauh lebih cepat daripada jumlah job yang selesai. Script
ini melacak nasib tiap job yang hilang dari antrean.

**Yang dibaca:**
- `status=finished` / `status=started` → benar diproses, angka throughput sahih.
- `HASH HILANG` → job dibuang/dedup tanpa diproses; angka throughput di langkah
  3 jadi menyesatkan dan perlu ditafsir ulang.

### Langkah 5 — Apakah ada kegagalan karena kelebihan beban?

```bash
./.venv/bin/python failures.py
```

**Yang dibaca** — kolom `rentang`:
- Kegagalan **berumur berjam-jam/berhari-hari** → sisa masalah lama, bukan isu
  kapasitas saat ini.
- Kegagalan **baru** (menit-menit terakhir) dan jumlahnya bertambah selama
  langkah 2–3 berjalan → server tumbang karena beban.

### Langkah 6 — Konversi job → satuan bisnis

```bash
./.venv/bin/python payload.py --sample 30 --show 1
```

Membaca isi payload job (zlib + pickle) memakai unpickler toleran, jadi tidak
perlu meng-install backend.

**Yang dibaca:** berapa encounter yang dibawa satu job. Kalau 1 job = 1
encounter, maka *job/jam* dari langkah 3 langsung sama dengan *encounter/jam*.

---

## Cara Menyimpulkan

| Temuan | Artinya |
|---|---|
| `idle = 0` di pool + throughput ≈ kapasitas teoritis | **Pool worker mentok** |
| Backlog NAIK terus | **Laju masuk melebihi kapasitas** |
| Queue menumpuk padahal banyak worker idle di **queue lain** | **Alokasi worker timpang**, bukan mesin yang kurang |
| `evicted_keys` / `rejected_connections` > 0 | **Redis** yang jadi bottleneck |
| Job gagal baru terus bertambah | **Server kewalahan** |

> **Batasan penting.** Dijalankan **dari laptop**, script ini hanya melihat
> Redis — cukup untuk membuktikan *pool worker* mentok, tetapi **bukan** apakah
> CPU/RAM mesinnya mentok. Padahal itu yang menentukan langkah perbaikan:
>
> - CPU host masih longgar → tambah worker, throughput naik hampir linier.
> - CPU host sudah jenuh → tambah worker **tidak menolong**, harus tambah mesin.
>
> Jalankan **di VM server** (lihat bagian di atas) supaya CPU/RAM ikut terukur
> dan kesimpulannya jadi pasti.

---

## Referensi File

| File | Fungsi |
|---|---|
| `config.py` | Baca `.env`, sediakan koneksi Redis |
| `collect.py` | **Pengumpul data — satu sumber kebenaran.** Hanya membaca Redis, tidak mencetak & tidak menyimpulkan |
| `host.py` | Metrik CPU/RAM mesin dari `/proc` (aktif hanya bila dijalankan di VM Linux) |
| `report.py` | Jalankan semua pemeriksaan → simpulkan → tulis Markdown |
| `snapshot.py` | Potret sesaat semua queue + worker |
| `monitor.py` | Sampling backlog berkala → CSV |
| `throughput.py` | Ukur throughput, durasi job, waktu tunggu |
| `dequeue_fate.py` | Lacak job yang keluar antrean: diproses atau dibuang |
| `failures.py` | Ringkas job gagal + umurnya per queue |
| `payload.py` | Bongkar isi payload job (zlib + pickle) |
| `LAPORAN.md` | Laporan terakhir — **dibuat otomatis, jangan diedit manual** |
| `reports/` | Arsip laporan bertanggal (di-ignore git) |
| `results/` | CSV mentah dari `monitor.py` (di-ignore git) |

`monitor.py`, `throughput.py`, `dequeue_fate.py`, dan `payload.py` menerima
`--queue <nama>` untuk menganalisis queue lain, mis. `ocr_agent_prod`:

```bash
./.venv/bin/python throughput.py --queue ocr_agent_prod --minutes 6
```

`snapshot.py` dan `failures.py` tidak perlu flag itu — keduanya memang menyapu
seluruh queue sekaligus.
