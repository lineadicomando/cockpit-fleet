"""Fixed locations used by the helpers and the systemd units."""

from pathlib import Path

STATE_DIR = Path("/var/lib/cockpit-fleet")
RUNS_DIR = STATE_DIR / "runs"
PROJECTS_DIR = STATE_DIR / "projects"

# Encrypted credentials: written by root only, readable by the service user.
SECRETS_DIR = Path("/etc/cockpit-fleet/secrets")

COLLECTIONS_DIR = Path("/usr/share/cockpit-fleet/collections")

RUN_UNIT = "cockpit-fleet-run@{}.service"
