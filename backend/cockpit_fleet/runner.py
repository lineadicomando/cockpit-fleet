"""Turn a stored run specification into an ansible-playbook invocation.

Nothing from the frontend reaches argv without going through build_argv.
"""

import json
from pathlib import Path

from .errors import BadRequest
from .paths import COLLECTIONS_DIR
from .validators import check_int, check_limit, check_tag, check_user, resolve_inside

# Credential purpose -> ansible-playbook option that takes a file.
CREDENTIAL_OPTIONS = {
    "private_key": "--private-key",
    "connection_password": "--connection-password-file",
    "become_password": "--become-password-file",
    "vault_password": "--vault-password-file",
}


def _tags(spec: dict, key: str) -> str | None:
    tags = spec.get(key)
    if tags is None:
        return None
    if not isinstance(tags, list) or not tags or len(tags) > 50:
        raise BadRequest(f"{key} must be a non-empty list")
    return ",".join(check_tag(tag) for tag in tags)


def build_argv(
    spec: dict, run_dir: Path, runtime_dir: Path, credentials_dir: Path | None
) -> list[str]:
    project = run_dir / "project"
    argv = [
        "/usr/bin/ansible-playbook",
        "--inventory", str(resolve_inside(project, spec.get("inventory"), "inventory")),
        "--forks", str(check_int(spec.get("forks", 5), "forks", 1, 200)),
    ]

    if spec.get("limit") is not None:
        argv += ["--limit", check_limit(spec["limit"])]
    if spec.get("user") is not None:
        argv += ["--user", check_user(spec["user"])]
    for key, option in (("tags", "--tags"), ("skip_tags", "--skip-tags")):
        value = _tags(spec, key)
        if value:
            argv += [option, value]

    if spec.get("check") is True:
        argv.append("--check")
    if spec.get("diff") is True:
        argv.append("--diff")
    verbosity = check_int(spec.get("verbosity", 0), "verbosity", 0, 4)
    if verbosity:
        argv.append("-" + "v" * verbosity)

    extra_vars = spec.get("extra_vars")
    if extra_vars is not None:
        if not isinstance(extra_vars, dict):
            raise BadRequest("extra_vars must be an object")
        vars_file = runtime_dir / "extra_vars.json"
        vars_file.write_text(json.dumps(extra_vars))
        argv += ["--extra-vars", f"@{vars_file}"]

    if credentials_dir is not None:
        for purpose, option in CREDENTIAL_OPTIONS.items():
            credential = credentials_dir / f"fleet_{purpose}"
            if credential.is_file():
                argv += [option, str(credential)]

    argv.append(str(resolve_inside(project, spec.get("playbook"), "playbook")))
    return argv


def build_env(run_dir: Path, runtime_dir: Path) -> dict[str, str]:
    home = runtime_dir / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    control = runtime_dir / "cp"
    control.mkdir(mode=0o700, exist_ok=True)
    known_hosts = run_dir / "known_hosts"
    # Ansible only accepts a .cfg file here. Setting it also keeps an
    # ansible.cfg shipped inside the project from being picked up.
    config = runtime_dir / "ansible.cfg"
    config.write_text("[defaults]\n")
    ssh_args = (
        "-o ControlMaster=auto -o ControlPersist=60s "
        f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} "
        "-o GlobalKnownHostsFile=/dev/null"
    )
    # A clean environment: nothing is inherited from the unit beyond these.
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "ANSIBLE_HOME": str(home / ".ansible"),
        "ANSIBLE_CONFIG": str(config),
        "ANSIBLE_LOCAL_TEMP": str(runtime_dir / "tmp"),
        "ANSIBLE_SSH_CONTROL_PATH_DIR": str(control),
        "ANSIBLE_SSH_ARGS": ssh_args,
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_COLLECTIONS_PATH": f"{COLLECTIONS_DIR}:{run_dir / 'project'}",
        "ANSIBLE_CALLBACKS_ENABLED": "lineadicomando.fleet.events",
        "ANSIBLE_FORCE_COLOR": "1",
        "FLEET_EVENTS_FILE": str(run_dir / "events.jsonl"),
    }
