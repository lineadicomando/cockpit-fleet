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
    [
        """
        CREATE TABLE credentials (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            kind        TEXT NOT NULL CHECK (kind IN ('machine', 'vault', 'source')),
            username    TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL
        )
        """,
        """
        CREATE TABLE projects (
            id             TEXT PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            kind           TEXT NOT NULL CHECK (kind IN ('local', 'git')),
            path           TEXT NOT NULL DEFAULT '',
            url            TEXT NOT NULL DEFAULT '',
            ref            TEXT NOT NULL DEFAULT '',
            credential_id  TEXT REFERENCES credentials (id),
            revision       TEXT NOT NULL DEFAULT '',
            synced_at      REAL,
            sync_error     TEXT NOT NULL DEFAULT '',
            created_at     REAL NOT NULL
        )
        """,
        """
        CREATE TABLE inventories (
            id                   TEXT PRIMARY KEY,
            name                 TEXT NOT NULL UNIQUE,
            kind                 TEXT NOT NULL CHECK (kind IN ('file', 'managed')),
            project_id           TEXT REFERENCES projects (id),
            path                 TEXT NOT NULL DEFAULT '',
            format               TEXT NOT NULL DEFAULT 'yaml' CHECK (format IN ('yaml', 'ini')),
            content              TEXT NOT NULL DEFAULT '',
            vault_credential_id  TEXT REFERENCES credentials (id),
            hosts                TEXT NOT NULL DEFAULT '[]',
            parsed_at            REAL,
            parse_error          TEXT NOT NULL DEFAULT '',
            created_at           REAL NOT NULL
        )
        """,
        """
        CREATE TABLE host_keys (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            host      TEXT NOT NULL,
            key_type  TEXT NOT NULL,
            key       TEXT NOT NULL,
            added_by  TEXT NOT NULL,
            added_at  REAL NOT NULL,
            UNIQUE (host, key_type)
        )
        """,
        """
        CREATE TABLE templates (
            id                     TEXT PRIMARY KEY,
            name                   TEXT NOT NULL UNIQUE,
            project_id             TEXT NOT NULL REFERENCES projects (id),
            playbook               TEXT NOT NULL,
            inventory_id           TEXT NOT NULL REFERENCES inventories (id),
            machine_credential_id  TEXT REFERENCES credentials (id),
            vault_credential_id    TEXT REFERENCES credentials (id),
            limit_hosts            TEXT NOT NULL DEFAULT '',
            tags                   TEXT NOT NULL DEFAULT '[]',
            skip_tags              TEXT NOT NULL DEFAULT '[]',
            extra_vars             TEXT NOT NULL DEFAULT '{}',
            forks                  INTEGER,
            verbosity              INTEGER NOT NULL DEFAULT 0,
            become                 INTEGER NOT NULL DEFAULT 0,
            created_at             REAL NOT NULL,
            updated_at             REAL NOT NULL
        )
        """,
        """
        CREATE TABLE runs (
            id                TEXT PRIMARY KEY,
            kind              TEXT NOT NULL CHECK (kind IN ('playbook', 'inventory', 'sync')),
            status            TEXT NOT NULL CHECK (status IN
                              ('queued', 'running', 'successful', 'failed', 'canceled', 'error')),
            title             TEXT NOT NULL,
            template_id       TEXT REFERENCES templates (id) ON DELETE SET NULL,
            project_id        TEXT REFERENCES projects (id) ON DELETE SET NULL,
            inventory_id      TEXT REFERENCES inventories (id) ON DELETE SET NULL,
            created_by        TEXT NOT NULL,
            created_at        REAL NOT NULL,
            started_at        REAL,
            finished_at       REAL,
            rc                INTEGER,
            revision          TEXT NOT NULL DEFAULT '',
            options           TEXT NOT NULL DEFAULT '{}',
            stats             TEXT NOT NULL DEFAULT '{}',
            error             TEXT NOT NULL DEFAULT '',
            cancel_requested  INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX runs_created ON runs (created_at)",
        "CREATE INDEX runs_status ON runs (status)",
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
