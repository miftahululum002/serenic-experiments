import pickle
import zlib
from redis import Redis
from rq import Queue

# 1. Koneksi ke Redis lokal (asumsi menggunakan SSH Tunneling)
redis_conn = Redis(host="localhost", port=6379)
queue_name = "eklaim_batch_agent_prod"
queue = Queue(queue_name, connection=redis_conn)

print(f"=== Membuka Payload Queue: {queue_name} ===")
print(f"Total Job: {len(queue.jobs)}\n" + "-" * 50)

for i, job in enumerate(queue.jobs, start=1):
    print(f"[{i}] Job ID: {job.id}")

    try:
        # Coba cara normal terlebih dahulu
        print(f"    Fungsi : {job.func_name}")
        print(f"    Args   : {job.args}")
        print(f"    Kwargs : {job.kwargs}")
    except Exception:
        # Jika gagal karena ModuleNotFoundError, kita paksa bypass unpickle class-nya
        # Kita ambil raw string data dari Redis langsung
        raw_data = redis_conn.hget(f"rq:job:{job.id}", "data")

        print(
            "    Fungsi : [Gagal decode secara utuh karena kekurangan module 'serenic_mlkit']"
        )
        print("    Raw Extract (Mencari teks yang terbaca):")

        if raw_data:
            try:
                # Coba dekompresi zlib jika di-kompresi
                decompressed = zlib.decompress(raw_data)
                # Ambil karakter yang bisa dibaca saja (printable)
                clean_text = "".join(
                    chr(b) for b in decompressed if 32 <= b << 127 or b == 10
                )
                print(f"        {clean_text[:500]}")  # tampilkan 500 karakter pertama
            except Exception:
                # Jika tidak di-kompresi zlib, lumat langsung data mentahnya
                clean_text = "".join(
                    chr(b) for b in raw_data if 32 <= b << 127 or b == 10
                )
                print(f"        {clean_text[:500]}")

    print("-" * 50)
