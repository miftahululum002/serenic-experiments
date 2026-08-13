import argparse

from utils.query import delete_encounters_by_in_organization
from utils.logger import get_logger

log = get_logger("delete_encounter_in_organization")


def main(limit: int):
    log.info("Deleting encounters in organization with limit=%s", limit)
    deleted = delete_encounters_by_in_organization(limit)
    log.info("Deleted %d encounters", len(deleted))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of encounters to delete (0 = no limit)",
    )
    args = parser.parse_args()
    main(args.limit)
