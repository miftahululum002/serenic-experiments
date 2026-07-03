from dotenv import load_dotenv
import os

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
EKLAIM_BATCH_AGENT = os.getenv("EKLAIM_BATCH_AGENT", "eklaim_batch_agent_prod")
TARGET_ORG = os.getenv("TARGET_ORG", "d2a967c2-f848-46b9-8d02-bd94680d6bf3")
