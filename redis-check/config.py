from redis import Redis
from constant import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD

redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)
