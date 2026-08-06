from utils.api_request import get, post
from utils.logger import get_logger
from utils.helper import get_timestamp, get_api_version

log = get_logger("api_interface")


def api_health_check():
    url = f"integrations/{get_api_version()}/health_check"
    log.info("Hit health check endpoint at %s", url)
    response = get(url)
    if response.status_code == 200:
        log.info("Health check passed")
    else:
        log.warning("Health check failed with status %s", response.status_code)
    return response


def api_new_encounter(payload):
    url = f"integrations/{get_api_version()}/encounters/new"
    final_payload = {
        "timestamp": get_timestamp(),
        "newEncounters": payload,
    }

    log.info("Posting new encounter to %s", url)
    response = post(url, payload=final_payload)
    if response.status_code == 200:
        log.info("New encounter posted successfully")
    else:
        log.warning("New encounter failed with status %s", response.status_code)

    return response


def api_update_encounter(payload):
    url = f"integrations/{get_api_version()}/encounters/update"
    now = get_timestamp()
    final_payload = {
        "start_timestamp": now,
        "end_timestamp": now,
        "updates": payload,
    }

    log.info("Posting update encounter to %s", url)
    response = post(url, payload=final_payload)
    if response.status_code == 200:
        log.info("Update encounter posted successfully")
    else:
        log.warning("Update encounter failed with status %s", response.status_code)

    return response


def api_prerequisites(payload):
    url = f"integrations/{get_api_version()}/prerequisites"
    payload["timestamp"] = get_timestamp()

    log.info("Posting prerequisites to %s", url)
    response = post(url, payload=payload)

    if response.status_code == 200:
        log.info("Prerequisites posted successfully")
    else:
        log.warning("Prerequisites failed with status %s", response.status_code)

    return response
