import logging
import sys
from app.config import settings


def setup_logging():
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"
    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quiet noisy libs
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("SignalRCoreClient").setLevel(logging.WARNING)  # signalrcore's actual logger name
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
