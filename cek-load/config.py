import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_USER = os.getenv("REDIS_USER") or None
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

DATA_PARSING_AGENT = os.getenv(
    "AGENT_QUEUE_DATA_PARSING", "integration_data_parsing_agent_prod"
)
AGENT_QUEUE_EKLAIM_BATCH = os.getenv(
    "AGENT_QUEUE_EKLAIM_BATCH", "eklaim_batch_agent_prod"
)

# Metrik CPU/RAM. Kosong = baca mesin tempat script ini jalan (kasus normal:
# dijalankan langsung di VM worker). Diisi = baca /proc VM lain lewat SSH,
# supaya CPU/RAM tetap terukur walau report.py dijalankan dari laptop.
HOST_SSH = os.getenv("HOST_SSH") or ""
HOST_SSH_CMD = os.getenv("HOST_SSH_CMD") or ""


def get_redis():
    import redis

    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        username=REDIS_USER,
        password=REDIS_PASSWORD,
        decode_responses=False,
    )
