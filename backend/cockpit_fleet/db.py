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
    [
        # Several vaults per template, each with the identifier Ansible matches
        # against the label of the encrypted data.
        "ALTER TABLE credentials ADD COLUMN vault_id TEXT NOT NULL DEFAULT ''",
        """
        CREATE TABLE template_vaults (
            template_id    TEXT NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
            credential_id  TEXT NOT NULL REFERENCES credentials (id),
            position       INTEGER NOT NULL,
            PRIMARY KEY (template_id, credential_id)
        )
        """,
        """
        INSERT INTO template_vaults (template_id, credential_id, position)
        SELECT id, vault_credential_id, 0 FROM templates WHERE vault_credential_id IS NOT NULL
        """,
        # The column cannot be dropped (it carries a foreign key); it is no longer used.
        "UPDATE templates SET vault_credential_id = NULL",
    ],
    [
        # The questions a template asks at launch; the answers become variables.
        "ALTER TABLE templates ADD COLUMN survey TEXT NOT NULL DEFAULT '[]'",
    ],
    [
        # Projects edited in the interface and jobs that check a playbook: two
        # more kinds. A CHECK cannot be altered, so both tables are rebuilt,
        # this time without the list of kinds: the commands validate it, and
        # the next kind will not need another rebuild.
        """
        CREATE TABLE projects_new (
            id             TEXT PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            kind           TEXT NOT NULL,
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
        INSERT INTO projects_new
        SELECT id, name, kind, path, url, ref, credential_id, revision, synced_at, sync_error,
               created_at
        FROM projects
        """,
        "DROP TABLE projects",
        "ALTER TABLE projects_new RENAME TO projects",
        """
        CREATE TABLE runs_new (
            id                TEXT PRIMARY KEY,
            kind              TEXT NOT NULL,
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
        # The row number is the cursor of the list of runs: it is kept.
        """
        INSERT INTO runs_new (rowid, id, kind, status, title, template_id, project_id,
                              inventory_id, created_by, created_at, started_at, finished_at, rc,
                              revision, options, stats, error, cancel_requested)
        SELECT rowid, id, kind, status, title, template_id, project_id, inventory_id, created_by,
               created_at, started_at, finished_at, rc, revision, options, stats, error,
               cancel_requested
        FROM runs
        """,
        "DROP TABLE runs",
        "ALTER TABLE runs_new RENAME TO runs",
        "CREATE INDEX runs_created ON runs (created_at)",
        "CREATE INDEX runs_status ON runs (status)",
    ],
    [
        # The collections a project asks for in its requirements file: when
        # they were last installed, and why the last attempt failed.
        "ALTER TABLE projects ADD COLUMN collections_at REAL",
        "ALTER TABLE projects ADD COLUMN collections_error TEXT NOT NULL DEFAULT ''",
    ],
    [
        # What a call came through when it was not the interface: an agent.
        "ALTER TABLE audit ADD COLUMN via TEXT NOT NULL DEFAULT ''",
    ],
    [
        # Schedules: a template launched at the times of an OnCalendar
        # expression, with the answers to its survey stored here.
        """
        CREATE TABLE schedules (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL UNIQUE,
            template_id   TEXT NOT NULL REFERENCES templates (id),
            calendar      TEXT NOT NULL,
            timezone      TEXT NOT NULL DEFAULT '',
            enabled       INTEGER NOT NULL DEFAULT 1,
            limit_hosts   TEXT NOT NULL DEFAULT '',
            check_mode    INTEGER NOT NULL DEFAULT 0,
            diff          INTEGER NOT NULL DEFAULT 0,
            answers       TEXT NOT NULL DEFAULT '{}',
            overlap       TEXT NOT NULL DEFAULT 'skip',
            catch_up      INTEGER NOT NULL DEFAULT 0,
            priority      INTEGER NOT NULL DEFAULT 0,
            random_delay  INTEGER NOT NULL DEFAULT 0,
            next_at       REAL,
            last_at       REAL,
            last_outcome  TEXT NOT NULL DEFAULT '',
            last_error    TEXT NOT NULL DEFAULT '',
            last_run_id   TEXT REFERENCES runs (id) ON DELETE SET NULL,
            created_by    TEXT NOT NULL,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL
        )
        """,
        "CREATE INDEX schedules_next ON schedules (enabled, next_at)",
        "ALTER TABLE runs ADD COLUMN schedule_id TEXT REFERENCES schedules (id) ON DELETE SET NULL",
        "ALTER TABLE runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX runs_schedule ON runs (schedule_id)",
    ],
    [
        # Notifications: where they go, which runs they are about, and the
        # outbox that cockpit-fleet-notify.service delivers from.
        """
        CREATE TABLE channels (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL UNIQUE,
            kind          TEXT NOT NULL,
            settings      TEXT NOT NULL DEFAULT '{}',
            language      TEXT NOT NULL DEFAULT 'en',
            enabled       INTEGER NOT NULL DEFAULT 1,
            group_window  INTEGER NOT NULL DEFAULT 0,
            max_per_hour  INTEGER NOT NULL DEFAULT 20,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL
        )
        """,
        """
        CREATE TABLE notify_rules (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            channel_id  TEXT NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
            events      TEXT NOT NULL DEFAULT '[]',
            scope       TEXT NOT NULL DEFAULT 'all',
            scope_id    TEXT,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  REAL NOT NULL
        )
        """,
        """
        CREATE TABLE notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id  TEXT NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
            event       TEXT NOT NULL,
            run_id      TEXT REFERENCES runs (id) ON DELETE SET NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            state       TEXT NOT NULL DEFAULT 'pending',
            attempts    INTEGER NOT NULL DEFAULT 0,
            next_at     REAL NOT NULL,
            message     INTEGER,
            error       TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            sent_at     REAL
        )
        """,
        "CREATE INDEX notifications_due ON notifications (state, channel_id, next_at)",
        "CREATE INDEX notifications_created ON notifications (created_at)",
    ],
    [
        # Checks: the hosts of the inventories, the checks applied to their
        # groups, the state of each check on each host and its changes, and
        # the windows in which a host is in maintenance.
        """
        CREATE TABLE hosts (
            id            TEXT PRIMARY KEY,
            inventory_id  TEXT NOT NULL REFERENCES inventories (id) ON DELETE CASCADE,
            name          TEXT NOT NULL,
            address       TEXT NOT NULL DEFAULT '',
            groups        TEXT NOT NULL DEFAULT '[]',
            platform      TEXT NOT NULL DEFAULT '',
            os            TEXT NOT NULL DEFAULT '',
            facts         TEXT NOT NULL DEFAULT '{}',
            facts_at      REAL,
            reachable     INTEGER,
            created_at    REAL NOT NULL,
            UNIQUE (inventory_id, name)
        )
        """,
        """
        CREATE TABLE checks (
            id                     TEXT PRIMARY KEY,
            name                   TEXT NOT NULL UNIQUE,
            kind                   TEXT NOT NULL,
            inventory_id           TEXT NOT NULL REFERENCES inventories (id),
            host_group             TEXT NOT NULL DEFAULT 'all',
            interval               INTEGER NOT NULL,
            retry_interval         INTEGER NOT NULL,
            max_attempts           INTEGER NOT NULL DEFAULT 3,
            params                 TEXT NOT NULL DEFAULT '{}',
            thresholds             TEXT NOT NULL DEFAULT '{}',
            machine_credential_id  TEXT REFERENCES credentials (id),
            become                 INTEGER NOT NULL DEFAULT 0,
            project_id             TEXT REFERENCES projects (id),
            playbook               TEXT NOT NULL DEFAULT '',
            enabled                INTEGER NOT NULL DEFAULT 1,
            created_by             TEXT NOT NULL,
            created_at             REAL NOT NULL,
            updated_at             REAL NOT NULL
        )
        """,
        """
        CREATE TABLE check_states (
            host_id         TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            check_id        TEXT NOT NULL REFERENCES checks (id) ON DELETE CASCADE,
            state           INTEGER,
            hard_state      INTEGER NOT NULL DEFAULT 0,
            state_type      TEXT NOT NULL DEFAULT 'hard',
            attempt         INTEGER NOT NULL DEFAULT 0,
            summary         TEXT NOT NULL DEFAULT '',
            metrics         TEXT NOT NULL DEFAULT '[]',
            details         TEXT NOT NULL DEFAULT '{}',
            checked_at      REAL,
            changed_at      REAL,
            next_at         REAL,
            run_id          TEXT,
            history         TEXT NOT NULL DEFAULT '[]',
            flapping        INTEGER NOT NULL DEFAULT 0,
            ack_by          TEXT NOT NULL DEFAULT '',
            ack_at          REAL,
            ack_comment     TEXT NOT NULL DEFAULT '',
            notified_state  INTEGER NOT NULL DEFAULT 0,
            notified_at     REAL,
            PRIMARY KEY (host_id, check_id)
        )
        """,
        "CREATE INDEX check_states_due ON check_states (next_at)",
        "CREATE INDEX check_states_check ON check_states (check_id)",
        """
        CREATE TABLE state_changes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id     TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            check_id    TEXT NOT NULL REFERENCES checks (id) ON DELETE CASCADE,
            ts          REAL NOT NULL,
            state       INTEGER NOT NULL,
            state_type  TEXT NOT NULL,
            summary     TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX state_changes_of ON state_changes (host_id, check_id, ts)",
        "CREATE INDEX state_changes_ts ON state_changes (ts)",
        """
        CREATE TABLE maintenances (
            id            TEXT PRIMARY KEY,
            inventory_id  TEXT NOT NULL REFERENCES inventories (id) ON DELETE CASCADE,
            host_group    TEXT NOT NULL DEFAULT '',
            host_id       TEXT REFERENCES hosts (id) ON DELETE CASCADE,
            starts_at     REAL NOT NULL,
            ends_at       REAL NOT NULL,
            comment       TEXT NOT NULL DEFAULT '',
            created_by    TEXT NOT NULL,
            created_at    REAL NOT NULL
        )
        """,
        "CREATE INDEX maintenances_ends ON maintenances (ends_at)",
        # Rules about checks: a group of an inventory as their scope.
        "ALTER TABLE notify_rules ADD COLUMN scope_group TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE notifications ADD COLUMN host_id TEXT"
        " REFERENCES hosts (id) ON DELETE SET NULL",
        "ALTER TABLE notifications ADD COLUMN check_id TEXT"
        " REFERENCES checks (id) ON DELETE SET NULL",
    ],
    [
        # The inventory of hosts: the last snapshot of each, its history as
        # changes only, the updates it lacks, and the drift of compliance checks.
        """
        CREATE TABLE cmdb_hosts (
            host_id          TEXT PRIMARY KEY REFERENCES hosts (id) ON DELETE CASCADE,
            collected_at     REAL,
            vendor           TEXT NOT NULL DEFAULT '',
            model            TEXT NOT NULL DEFAULT '',
            serial           TEXT NOT NULL DEFAULT '',
            bios             TEXT NOT NULL DEFAULT '',
            virtual          TEXT NOT NULL DEFAULT '',
            cpu_model        TEXT NOT NULL DEFAULT '',
            cpus             INTEGER,
            memory           INTEGER,
            boot_at          REAL,
            disks            TEXT NOT NULL DEFAULT '[]',
            interfaces       TEXT NOT NULL DEFAULT '[]',
            package_manager  TEXT NOT NULL DEFAULT '',
            updates_at       REAL,
            update_manager   TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE cmdb_packages (
            host_id  TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            name     TEXT NOT NULL,
            arch     TEXT NOT NULL DEFAULT '',
            version  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (host_id, name, arch)
        )
        """,
        "CREATE INDEX cmdb_packages_name ON cmdb_packages (name)",
        """
        CREATE TABLE cmdb_services (
            host_id  TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            name     TEXT NOT NULL,
            state    TEXT NOT NULL DEFAULT '',
            enabled  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (host_id, name)
        )
        """,
        """
        CREATE TABLE cmdb_updates (
            host_id    TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            name       TEXT NOT NULL,
            current    TEXT NOT NULL DEFAULT '',
            available  TEXT NOT NULL DEFAULT '',
            security   INTEGER NOT NULL DEFAULT 0,
            source     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (host_id, name)
        )
        """,
        """
        CREATE TABLE cmdb_changes (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id  TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            ts       REAL NOT NULL,
            area     TEXT NOT NULL,
            name     TEXT NOT NULL,
            before   TEXT NOT NULL DEFAULT '',
            after    TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX cmdb_changes_of ON cmdb_changes (host_id, id)",
        "CREATE INDEX cmdb_changes_ts ON cmdb_changes (ts)",
        """
        CREATE TABLE drift (
            host_id   TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            check_id  TEXT NOT NULL REFERENCES checks (id) ON DELETE CASCADE,
            ts        REAL NOT NULL,
            changes   TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (host_id, check_id)
        )
        """,
    ],
    [
        # Time series: one row per metric of a check on a host, and its
        # values as narrow rows keyed by the series and the time, so that a
        # value costs a few bytes. The hourly and daily aggregates are kept
        # up to date as values arrive; each table has its own retention.
        """
        CREATE TABLE metric_series (
            id        INTEGER PRIMARY KEY,
            host_id   TEXT NOT NULL REFERENCES hosts (id) ON DELETE CASCADE,
            check_id  TEXT NOT NULL REFERENCES checks (id) ON DELETE CASCADE,
            name      TEXT NOT NULL,
            instance  TEXT NOT NULL DEFAULT '',
            unit      TEXT NOT NULL DEFAULT '',
            last_at   REAL NOT NULL,
            UNIQUE (host_id, check_id, name, instance)
        )
        """,
        "CREATE INDEX metric_series_check ON metric_series (check_id)",
        """
        CREATE TABLE metric_points (
            series_id  INTEGER NOT NULL REFERENCES metric_series (id) ON DELETE CASCADE,
            ts         INTEGER NOT NULL,
            value      REAL NOT NULL,
            PRIMARY KEY (series_id, ts)
        ) WITHOUT ROWID
        """,
        """
        CREATE TABLE metric_hourly (
            series_id  INTEGER NOT NULL REFERENCES metric_series (id) ON DELETE CASCADE,
            ts         INTEGER NOT NULL,
            n          INTEGER NOT NULL,
            low        REAL NOT NULL,
            high       REAL NOT NULL,
            total      REAL NOT NULL,
            PRIMARY KEY (series_id, ts)
        ) WITHOUT ROWID
        """,
        """
        CREATE TABLE metric_daily (
            series_id  INTEGER NOT NULL REFERENCES metric_series (id) ON DELETE CASCADE,
            ts         INTEGER NOT NULL,
            n          INTEGER NOT NULL,
            low        REAL NOT NULL,
            high       REAL NOT NULL,
            total      REAL NOT NULL,
            PRIMARY KEY (series_id, ts)
        ) WITHOUT ROWID
        """,
    ],
    [
        # The output of a run is deleted after [runs] keep_days; its row stays,
        # with its outcome, and says when.
        "ALTER TABLE runs ADD COLUMN pruned_at REAL",
        "CREATE INDEX runs_unpruned ON runs (finished_at) WHERE pruned_at IS NULL",
    ],
    [
        # What each check cycle took, kept after the cycle itself is deleted:
        # the planner sizes the next cycles of the same group on it. The run
        # is gone after a day, so run_id refers to nothing.
        """
        CREATE TABLE cycles (
            run_id        TEXT PRIMARY KEY,
            inventory_id  TEXT NOT NULL REFERENCES inventories (id) ON DELETE CASCADE,
            grp           TEXT NOT NULL DEFAULT '',
            slow          INTEGER NOT NULL DEFAULT 0,
            status        TEXT NOT NULL,
            started_at    REAL NOT NULL,
            finished_at   REAL NOT NULL,
            hosts         INTEGER NOT NULL,
            checks        INTEGER NOT NULL,
            forks         INTEGER NOT NULL,
            memory_peak   INTEGER,
            cpu_usec      INTEGER
        )
        """,
        "CREATE INDEX cycles_of ON cycles (inventory_id, grp, finished_at)",
        "CREATE INDEX cycles_finished ON cycles (finished_at)",
    ],
    [
        # A state still held by a cycle when it is due again is skipped, not
        # doubled: how many of its due times were, for the cycle that holds
        # it; how many the cycle held past, kept with what it took; and the
        # groups whose cycles fall behind, told about once.
        "ALTER TABLE check_states ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE cycles ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE cycle_lag (
            inventory_id  TEXT NOT NULL REFERENCES inventories (id) ON DELETE CASCADE,
            grp           TEXT NOT NULL DEFAULT '',
            streak        INTEGER NOT NULL DEFAULT 0,
            since         REAL NOT NULL,
            skipped_at    REAL NOT NULL,
            told          INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (inventory_id, grp)
        )
        """,
    ],
    [
        # Approvals: a template may ask that each of its runs be approved by
        # someone else before it starts. Such a run waits, prepared, as
        # pending_approval, and ends as rejected or expired if not approved;
        # the status has to be rebuilt to admit them. Who asked and who
        # decided are in a table of their own, one row per run that waited.
        "ALTER TABLE templates ADD COLUMN needs_approval INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE runs_new (
            id                TEXT PRIMARY KEY,
            kind              TEXT NOT NULL,
            status            TEXT NOT NULL CHECK (status IN
                              ('pending_approval', 'queued', 'running', 'successful', 'failed',
                               'canceled', 'error', 'rejected', 'expired')),
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
            cancel_requested  INTEGER NOT NULL DEFAULT 0,
            schedule_id       TEXT REFERENCES schedules (id) ON DELETE SET NULL,
            priority          INTEGER NOT NULL DEFAULT 0,
            pruned_at         REAL
        )
        """,
        """
        INSERT INTO runs_new (rowid, id, kind, status, title, template_id, project_id,
                              inventory_id, created_by, created_at, started_at, finished_at, rc,
                              revision, options, stats, error, cancel_requested, schedule_id,
                              priority, pruned_at)
        SELECT rowid, id, kind, status, title, template_id, project_id, inventory_id, created_by,
               created_at, started_at, finished_at, rc, revision, options, stats, error,
               cancel_requested, schedule_id, priority, pruned_at
        FROM runs
        """,
        "DROP TABLE runs",
        "ALTER TABLE runs_new RENAME TO runs",
        "CREATE INDEX runs_created ON runs (created_at)",
        "CREATE INDEX runs_status ON runs (status)",
        "CREATE INDEX runs_schedule ON runs (schedule_id)",
        "CREATE INDEX runs_unpruned ON runs (finished_at) WHERE pruned_at IS NULL",
        """
        CREATE TABLE approvals (
            run_id        TEXT PRIMARY KEY REFERENCES runs (id) ON DELETE CASCADE,
            requested_by  TEXT NOT NULL,
            requested_at  REAL NOT NULL,
            expires_at    REAL NOT NULL,
            decision      TEXT NOT NULL DEFAULT ''
                          CHECK (decision IN ('', 'approved', 'rejected', 'expired')),
            decided_by    TEXT NOT NULL DEFAULT '',
            decided_at    REAL,
            comment       TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX approvals_open ON approvals (expires_at) WHERE decision = ''",
    ],
    [
        # Workflows: a graph of templates. Its steps and links are kept as
        # JSON, checked by the commands; which templates it uses is a table,
        # so that a template in use cannot be deleted. A run of a workflow is
        # a run of kind workflow; its steps are runs that refer to it, by the
        # step of the graph they are. What a playbook passed on with
        # set_stats is kept with its run.
        """
        CREATE TABLE workflows (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            description  TEXT NOT NULL DEFAULT '',
            nodes        TEXT NOT NULL DEFAULT '[]',
            edges        TEXT NOT NULL DEFAULT '[]',
            created_at   REAL NOT NULL,
            updated_at   REAL NOT NULL
        )
        """,
        """
        CREATE TABLE workflow_templates (
            workflow_id  TEXT NOT NULL REFERENCES workflows (id) ON DELETE CASCADE,
            template_id  TEXT NOT NULL REFERENCES templates (id),
            PRIMARY KEY (workflow_id, template_id)
        )
        """,
        "CREATE INDEX workflow_templates_of ON workflow_templates (template_id)",
        "ALTER TABLE runs ADD COLUMN workflow_id TEXT REFERENCES workflows (id) ON DELETE SET NULL",
        "ALTER TABLE runs ADD COLUMN workflow_run_id TEXT REFERENCES runs (id) ON DELETE SET NULL",
        "ALTER TABLE runs ADD COLUMN workflow_node TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE runs ADD COLUMN artifacts TEXT NOT NULL DEFAULT '{}'",
        "CREATE INDEX runs_workflow ON runs (workflow_run_id) WHERE workflow_run_id IS NOT NULL",
        "CREATE INDEX runs_of_workflow ON runs (workflow_id) WHERE workflow_id IS NOT NULL",
    ],
    [
        # Check cycles that could not be prepared, by inventory and group, so
        # that a cause that lasts is told once and its end told too.
        """
        CREATE TABLE cycle_faults (
            inventory_id  TEXT NOT NULL REFERENCES inventories (id) ON DELETE CASCADE,
            grp           TEXT NOT NULL DEFAULT '',
            reason        TEXT NOT NULL DEFAULT '',
            since         REAL NOT NULL,
            PRIMARY KEY (inventory_id, grp)
        )
        """,
    ],
    [
        # The last run of each template, which template.list shows.
        "CREATE INDEX runs_template ON runs (template_id) WHERE template_id IS NOT NULL",
    ],
]


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    if schema_version(conn) > len(MIGRATIONS):
        raise Unavailable("the database was created by a newer version of cockpit-fleet")
    # A migration may rebuild a table that others refer to: with foreign keys
    # on, dropping the old one would apply ON DELETE to their rows. This has
    # no effect inside a transaction, so it is set here; connect() turns the
    # checks on again.
    conn.execute("PRAGMA foreign_keys = OFF")
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
    except sqlite3.Error as error:
        raise Unavailable(f"cannot open the database: {error}") from error
    try:
        conn.row_factory = sqlite3.Row
        if schema_version(conn) == 0:
            # Only before the first table: free pages can then be given back
            # to the file system a few at a time (maintenance.vacuum).
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        migrate(conn)
        conn.execute("PRAGMA foreign_keys = ON")
    except BaseException as error:
        conn.close()
        if isinstance(error, sqlite3.Error):
            raise Unavailable(f"cannot open the database: {error}") from error
        raise
    return conn
