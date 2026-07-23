from config import API_HOST
from utils.api_request import get
from utils.logger import get_logger

log = get_logger("health_check")


def health_check():
    url = f"{API_HOST}/integrations/v2/health_check"
    log.info("Checking health at %s", url)
    response = get(url)
    if response.status_code == 200:
        log.info("Health check passed")
    else:
        log.warning("Health check failed with status %s", response.status_code)
    return response


if __name__ == "__main__":
    health_check()
