import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
ORGANIZATION_ID = os.getenv("ORGANIZATION_ID")
API_HOST = os.getenv("API_HOST")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Filename constants per endpoint
HEALTH_CHECK = "health_check"
PREREQUISITES = "prerequisites"
ENCOUNTERS_NEW = "new_encounters"
ENCOUNTERS_UPDATE = "update_encounters"
ENCOUNTERS_COMPLETED = "completed_encounters"

ENDPOINT_FILENAMES = {
    "health_check": HEALTH_CHECK,
    "prerequisites": PREREQUISITES,
    "encounters/new": ENCOUNTERS_NEW,
    "encounters/update": ENCOUNTERS_UPDATE,
    "encounters/completed": ENCOUNTERS_COMPLETED,
}
