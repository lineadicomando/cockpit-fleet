"""The SQLite database: connection settings and schema migrations."""

import sqlite3
from pathlib import Path

from .errors import Unavailable

# One entry per schema version, each a list of statements. Append only:
# a released entry is never edited.
MIGRATIONS: list[list[str]] = [
    [
        """
        CREATE TABLE audit (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       REAL NOT NULL,
            uid      INTEGER NOT NULL,
            user     TEXT NOT NULL,
            role     TEXT,
            command  TEXT NOT NULL,
            params   TEXT NOT NULL,
            outcome  TEXT NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
            detail   TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX audit_ts ON audit (ts)",
    ],
]


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    if schema_version(conn) > len(MIGRATIONS):
        raise Unavailable("the database was created by a newer version of cockpit-fleet")
    while True:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Read again under the lock: another process may have migrated.
            version = schema_version(conn)
            if version >= len(MIGRATIONS):
                conn.execute("COMMIT")
                return
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version + 1}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def connect(path: Path) -> sqlite3.Connection:
    try:
        # Autocommit: transactions are opened explicitly where they are needed.
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        migrate(conn)
    except sqlite3.Error as error:
        raise Unavailable(f"cannot open the database: {error}") from error
    return conn
