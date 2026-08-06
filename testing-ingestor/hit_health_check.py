from utils.api_interface import api_health_check
from utils.logger import get_logger

log = get_logger("health_check")


def health_check():
    response = api_health_check()
    return response


if __name__ == "__main__":
    health_check()
