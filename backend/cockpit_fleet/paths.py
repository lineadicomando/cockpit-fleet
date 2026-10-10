"""Fixed locations used by the helpers and the systemd units."""

from pathlib import Path

STATE_DIR = Path("/var/lib/cockpit-fleet")
RUNS_DIR = STATE_DIR / "runs"
PROJECTS_DIR = STATE_DIR / "projects"
# What can be computed again: the list of the installed modules.
CACHE_DIR = STATE_DIR / "cache"

# Encrypted credentials: written by root only, readable by the service user.
SECRETS_DIR = Path("/etc/cockpit-fleet/secrets")

# Secret answers to a survey, encrypted, between the moment they are typed and
# the launch of the run that takes them. On tmpfs: what is never used is gone
# at the next boot, and tmpfiles removes it after an hour.
ANSWERS_DIR = Path("/run/cockpit-fleet/answers")

COLLECTIONS_DIR = Path("/usr/share/cockpit-fleet/collections")

DATABASE = STATE_DIR / "fleet.db"
CONFIG_FILE = Path("/etc/cockpit-fleet/config.toml")
LIBEXEC_DIR = Path("/usr/libexec/cockpit-fleet")

SERVICE_USER = "cockpit-fleet"
RUN_UNIT = "cockpit-fleet-run@{}.service"
FINISH_UNIT_FILE = Path("/usr/lib/systemd/system/cockpit-fleet-finish@.service")
RUN_UNIT_FILE = Path("/usr/lib/systemd/system/cockpit-fleet-run@.service")
