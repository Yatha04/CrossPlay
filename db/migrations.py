"""Idempotent schema creation and future migrations."""

import sqlite3

from db.models import ALL_TABLES, INDEXES
from utils.logging import get_logger

log = get_logger("migrations")


def run_migrations(db_path: str) -> None:
    """Create all tables and indexes if they don't already exist."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for ddl in ALL_TABLES:
            cur.execute(ddl)
        for idx in INDEXES:
            cur.execute(idx)
        conn.commit()
        log.info("Database migrations complete: %s", db_path)
    finally:
        conn.close()
