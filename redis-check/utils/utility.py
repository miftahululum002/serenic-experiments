import argparse
from datetime import datetime
from constant import TARGET_ORG


def get_org_id() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgid", default=TARGET_ORG)
    args, _ = parser.parse_known_args()
    return args.orgid


def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
