# App Sync

CLI Python untuk login satu organisasi lalu menjalankan sinkronisasi untuk setiap `encounterId` di CSV.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config/organization.example.csv config/organization.csv
cp .env.example .env
```

Isi `config/organization.csv` dengan kredensial organisasi. File CSV encounter yang digunakan mengikuti contoh [`data/example.csv`](data/example.csv), dengan kolom `encounter_id`.
Nilai default batch dan delay dibaca dari `.env`:

```env
BATCH_SIZE=20
BATCH_DELAY_SECONDS=5
```

File `.env.example` hanya template; aplikasi membaca `.env`.

## Menjalankan

Jalankan sinkronisasi:

```bash
python3 app.py --orgid="32ab03d5-3c0c-4fbd-96ed-63ca054203fd"
```

`app.py` menerima `--orgid` dan `--file`, mencari organisasi pada `config/organization.csv`, login menggunakan `email` dan `password`, lalu memproses semua baris `encounter_id` dari CSV yang diberikan.

Host request diambil dari kolom `api_url` pada organisasi terpilih. Contohnya, jika `api_url` adalah `https://api.serenic.ai`, endpoint yang dipanggil adalah `https://api.serenic.ai/app/v1/api/auth/login`, endpoint refresh, dan endpoint sinkronisasi.

Contoh:

```bash
python3 app.py \
  --orgid="32ab03d5-3c0c-4fbd-96ed-63ca054203fd" \
  --file="/Users/miftahululum002/projects/serenic/experiments/app-hospital-synchronize/data/example.csv"
```

Format CSV:

```csv
encounter_id
2610030284
2610030268
2610030265
```

Payload disimpan sebelum request ke `payload/{orgid}/{YYYYMMDD}/`, sedangkan response disimpan ke `response/{orgid}/{YYYYMMDD}/`. Nama file payload dan response sama:

```text
{encounter_id}_{timestamp}_{uuid}.json
```

Contoh:

```text
payload/32ab03d5-3c0c-4fbd-96ed-63ca054203fd/20261003/2610030362_20261003130316_550e8400-e29b-41d4-a716-446655440000.json
response/32ab03d5-3c0c-4fbd-96ed-63ca054203fd/20261003/2610030362_20261003130316_550e8400-e29b-41d4-a716-446655440000.json
```

Secara default login mengirim `email` dan `password`, lalu mengambil access token dari `data.accessToken` dan refresh token dari `data.token`, sesuai contoh `example/response-login.json`. Saat access token JWT mendekati `exp`, client memanggil `/app/v1/api/auth/refresh-token` dengan payload `{"refreshToken": "<refresh-token>"}`. Jika sinkronisasi mendapat HTTP 401, refresh dilakukan sekali lalu request diulang. Sinkronisasi mengirim header `Authorization: Bearer <access-token>`.

```bash
python -m app_sync --token-path data.accessToken
```

Jika lokasi refresh token berbeda, gunakan `--refresh-token-path`; endpoint refresh dapat diubah dengan `--refresh-path`.

Field login tambahan dapat dikirim sebagai JSON, misalnya:

```bash
python -m app_sync --login-extra-json '{"organizationId":"..."}'
```

TLS tetap diverifikasi secara default. Opsi `--insecure` hanya untuk debugging pada environment yang memang memerlukannya.
