import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
if SERVICE_ROOT not in sys.path:
    sys.path.append(SERVICE_ROOT)

from shared.utils.logger import get_logger
from shared.utils.metrics import MetricsRegistry
from src.config import config
from src.consumer import MatchingConsumer
from src.db import MatchesDB, UsersDB
from src.matcher import Matcher

logger = get_logger("matching-service")


def main() -> None:
    logger.info("Starting matching service")
    users_db = UsersDB(config.users_db_path)
    matches_db = MatchesDB(config.matches_db_path)
    matcher = Matcher(users_db=users_db, config=config)
    metrics = MetricsRegistry()
    consumer = MatchingConsumer(
        config=config,
        matcher=matcher,
        matches_db=matches_db,
        metrics=metrics,
    )
    logger.info("Matching service using SQLite storage at %s", config.sqlite_db_path)
    consumer.run_forever()


if __name__ == "__main__":
    main()
