"""Structured logging setup for CrossPlay."""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the application logger.

    Format: ``2026-03-15 14:32:01 INFO  [module] message``
    """
    logger = logging.getLogger("crossplay")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def get_logger(module: str) -> logging.Logger:
    """Return a child logger for *module* (e.g. ``get_logger('poller')``)."""
    return logging.getLogger(f"crossplay.{module}")
