"""Fixed locations used by the helpers and the systemd units."""

from pathlib import Path

STATE_DIR = Path("/var/lib/cockpit-fleet")
RUNS_DIR = STATE_DIR / "runs"
PROJECTS_DIR = STATE_DIR / "projects"
# The trees of the inventories written in the interface, each with its repository.
INVENTORIES_DIR = STATE_DIR / "inventories"
# What can be computed again: the list of the installed modules.
CACHE_DIR = STATE_DIR / "cache"

# Encrypted credentials: written by root only, readable by the service user.
SECRETS_DIR = Path("/etc/cockpit-fleet/secrets")
# The secrets of notification channels, apart: cockpit-fleet-notify.service
# has PID 1 decrypt this whole directory, and nothing else.
NOTIFY_SECRETS_DIR = SECRETS_DIR / "notify"
NOTIFY_SECRET_PREFIX = "n-"  # noqa: S105  (a prefix of names, not a secret)

# Secret answers to a survey, encrypted, between the moment they are typed and
# the launch of the run that takes them. On tmpfs: what is never used is gone
# at the next boot, and tmpfiles removes it after an hour.
ANSWERS_DIR = Path("/run/cockpit-fleet/answers")
# The ciphertext of a run being started, copied there by fleet-unit, as root,
# for PID 1 to read: see credstage.py.
CREDS_STAGING_DIR = Path("/run/cockpit-fleet/creds")

COLLECTIONS_DIR = Path("/usr/share/cockpit-fleet/collections")

DATABASE = STATE_DIR / "fleet.db"
# Held by the tick of the scheduler, so that two never launch the same thing.
SCHEDULER_LOCK = STATE_DIR / "scheduler.lock"
# Written when there is something to deliver: cockpit-fleet-notify.path
# starts the service that does it.
NOTIFY_WAKE = STATE_DIR / "notify.wake"
NOTIFY_LOCK = STATE_DIR / "notify.lock"
# Touched when the tick last looked for run directories with no run.
SWEEP_STAMP = STATE_DIR / "sweep.stamp"
CONFIG_FILE = Path("/etc/cockpit-fleet/config.toml")
LIBEXEC_DIR = Path("/usr/libexec/cockpit-fleet")

SERVICE_USER = "cockpit-fleet"
RUN_UNIT = "cockpit-fleet-run@{}.service"
FINISH_UNIT_FILE = Path("/usr/lib/systemd/system/cockpit-fleet-finish@.service")
SCHEDULER_TIMER = "cockpit-fleet-scheduler.timer"
NOTIFY_PATH_UNIT = "cockpit-fleet-notify.path"
RUN_UNIT_FILE = Path("/usr/lib/systemd/system/cockpit-fleet-run@.service")
