"""Turn a stored job specification into the commands a run unit executes.

A job is one of three kinds: "playbook" (ansible-playbook), "inventory"
(ansible-inventory, to list hosts and groups) and "sync" (fetch a git
project). Nothing from the frontend reaches a command line without going
through the functions here.
"""

import configparser
import json
import math
import re
from pathlib import Path

from .errors import BadRequest
from .paths import COLLECTIONS_DIR, LIBEXEC_DIR
from .survey import check_variable
from .validators import (
    check_git_ref,
    check_git_url,
    check_int,
    check_limit,
    check_tag,
    check_user,
    check_vault_id,
    resolve_inside,
)

KINDS = ("playbook", "inventory", "sync")

# Credential purpose -> ansible-playbook option that takes a file.
CREDENTIAL_OPTIONS = {
    "private_key": "--private-key",
    "connection_password": "--connection-password-file",
    "become_password": "--become-password-file",
    "vault_password": "--vault-password-file",
}

_VAULT_FILE_RE = re.compile(r"vault_password_[0-9]{1,3}")
_SURVEY_FILE_RE = re.compile(r"survey_[0-9]{1,2}")

# Packaged helper that prints a secret from the file named in its environment.
# The run's scratch directory is mounted noexec, so it cannot be written there.
ASKPASS = str(LIBEXEC_DIR / "fleet-askpass")

# What a project's own ansible.cfg may set. Anything that points Ansible at
# files, plugins or commands outside the project, or that weakens host key
# checking, is left out; forks, inventory and credentials come from the job.
_CONFIG_ALLOWED = {
    "defaults": {
        "roles_path", "collections_path", "gathering", "timeout", "remote_user",
        "interpreter_python", "display_skipped_hosts", "deprecation_warnings",
        "any_errors_fatal", "error_on_undefined_vars", "jinja2_native",
    },
    "ssh_connection": {"pipelining"},
    "privilege_escalation": {"become", "become_method", "become_user"},
}
_CONFIG_PATHS = {"roles_path", "collections_path"}


def _project_paths(project: Path, value: str) -> list[str]:
    """Colon-separated paths, kept only when they stay inside the project."""
    root = project.resolve()
    kept = []
    for part in value.split(":"):
        part = part.strip()
        if not part:
            continue
        path = (root / part).resolve()
        if path.is_relative_to(root):
            kept.append(str(path))
    return kept


def generate_config(project: Path) -> tuple[str, list[str]]:
    """The ansible.cfg of a run, and the collection paths the project asks for."""
    source = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        source.read_string((project / "ansible.cfg").read_text())
    except (OSError, configparser.Error, UnicodeDecodeError):
        source = configparser.ConfigParser(interpolation=None)

    output = configparser.ConfigParser(interpolation=None)
    output.add_section("defaults")
    collections: list[str] = []
    for section, allowed in _CONFIG_ALLOWED.items():
        if not source.has_section(section):
            continue
        for key, value in source.items(section):
            if key not in allowed:
                continue
            if key in _CONFIG_PATHS:
                paths = _project_paths(project, value)
                if key == "collections_path":
                    # The environment variable wins over the file: returned instead.
                    collections = paths
                    continue
                value = ":".join(paths)
                if not value:
                    continue
            if not output.has_section(section):
                output.add_section(section)
            output.set(section, key, value)

    lines = []
    for section in output.sections():
        lines.append(f"[{section}]")
        lines += [f"{key} = {value}" for key, value in output.items(section)]
        lines.append("")
    return "\n".join(lines), collections


def _tags(spec: dict, key: str) -> str | None:
    tags = spec.get(key)
    if tags is None or tags == []:
        return None
    if not isinstance(tags, list) or len(tags) > 50:
        raise BadRequest(f"{key} must be a list")
    return ",".join(check_tag(tag) for tag in tags)


def _inventory(spec: dict, run_dir: Path) -> Path:
    """An inventory file of the project, or the one written for this run."""
    if spec.get("inventory_source") == "managed":
        return resolve_inside(run_dir / "inventory", spec.get("inventory"), "inventory")
    return resolve_inside(run_dir / "project", spec.get("inventory"), "inventory")


def needs_agent(credentials_dir: Path | None, prefix: str = "") -> bool:
    """A private key with a passphrase: it has to be loaded into an ssh-agent."""
    return (
        credentials_dir is not None
        and (credentials_dir / f"fleet_{prefix}private_key").is_file()
        and (credentials_dir / f"fleet_{prefix}key_passphrase").is_file()
    )


def agent_plan(
    credentials_dir: Path, runtime_dir: Path, prefix: str = ""
) -> tuple[list[str], list[str], dict[str, str], str]:
    """How to load a passphrase-protected key: the agent command, the ssh-add
    command, the environment for ssh-add, and the agent socket.

    ssh-add gets the passphrase from a helper that reads it from its file, so
    it is in no command line and in no environment.
    """
    socket = str(runtime_dir / "agent.sock")
    env = {
        "PATH": "/usr/bin:/bin",
        "SSH_AUTH_SOCK": socket,
        "SSH_ASKPASS": ASKPASS,
        "SSH_ASKPASS_REQUIRE": "force",
        "FLEET_ASKPASS_FILE": str(credentials_dir / f"fleet_{prefix}key_passphrase"),
    }
    return (
        ["/usr/bin/ssh-agent", "-D", "-a", socket],
        ["/usr/bin/ssh-add", "-q", str(credentials_dir / f"fleet_{prefix}private_key")],
        env,
        socket,
    )


def _credential_args(credentials_dir: Path | None, purposes: tuple[str, ...]) -> list[str]:
    argv = []
    if credentials_dir is not None:
        for purpose in purposes:
            if purpose == "private_key" and needs_agent(credentials_dir):
                continue  # offered by the agent instead
            credential = credentials_dir / f"fleet_{purpose}"
            if credential.is_file():
                argv += [CREDENTIAL_OPTIONS[purpose], str(credential)]
    return argv


def _vault_args(spec: dict, credentials_dir: Path | None) -> list[str]:
    """One --vault-id per vault credential of the template, in its order."""
    vaults = spec.get("vaults") or []
    if not isinstance(vaults, list) or len(vaults) > 20:
        raise BadRequest("vaults must be a list")
    argv = []
    for vault in vaults:
        if not isinstance(vault, dict) or not _VAULT_FILE_RE.fullmatch(str(vault.get("file", ""))):
            raise BadRequest("invalid vault entry")
        if credentials_dir is None:
            continue
        secret = credentials_dir / f"fleet_{vault['file']}"
        if not secret.is_file():
            continue
        label = vault.get("id") or ""
        argv += ["--vault-id", f"{check_vault_id(label)}@{secret}" if label else str(secret)]
    return argv


def _yaml_string(value: str) -> str:
    """A double-quoted YAML scalar, with everything but plain ASCII escaped."""
    out = []
    for ch in value:
        code = ord(ch)
        if ch in '"\\':
            out.append("\\" + ch)
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code <= 0xFF:
            out.append(f"\\x{code:02X}")
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04X}")
        else:
            out.append(f"\\U{code:08X}")
    return '"' + "".join(out) + '"'


def survey_vars_file(variables: object) -> str:
    """The answers to a survey, as a YAML file of variables.

    Ansible renders a variable that holds "{{ ... }}" as a template wherever
    it is used, lookups included. These values come from whoever launched the
    run, so every string is tagged !unsafe: it is used as it is written.
    """
    if not isinstance(variables, dict) or len(variables) > 100:
        raise BadRequest("survey_vars must be an object")
    lines = []
    for name, value in variables.items():
        check_variable(name)
        if isinstance(value, str):
            text = f"!unsafe {_yaml_string(value)}"
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (int, float)) and math.isfinite(value):
            text = repr(value)
            if "e" in text and "." not in text:
                # YAML 1.1 reads "1e+16" as a string: a float needs its dot.
                text = text.replace("e", ".0e")
        else:
            raise BadRequest(f"survey_vars: invalid value for {name}")
        lines.append(f"{name}: {text}\n")
    return "".join(lines)


def survey_secrets(secrets: object, credentials_dir: Path | None) -> dict[str, str]:
    """The secret answers of a run, by variable, read from its credentials.

    A missing one stops the run: a playbook must not go on without a variable
    it was promised, or with the fixed value the answer was meant to replace.
    """
    if not isinstance(secrets, dict) or len(secrets) > 30:
        raise BadRequest("survey_secrets must be an object")
    values = {}
    for name, file in secrets.items():
        if not isinstance(file, str) or not _SURVEY_FILE_RE.fullmatch(file):
            raise BadRequest("invalid survey secret entry")
        try:
            if credentials_dir is None:
                raise FileNotFoundError
            values[name] = (credentials_dir / f"fleet_{file}").read_text()
        except (OSError, UnicodeDecodeError):
            raise BadRequest(f"the secret answer for {name} is missing or unreadable") from None
    return values


def build_argv(
    spec: dict, run_dir: Path, runtime_dir: Path, credentials_dir: Path | None
) -> list[str]:
    """ansible-playbook for a "playbook" job."""
    project = run_dir / "project"
    argv = [
        "/usr/bin/ansible-playbook",
        "--inventory", str(_inventory(spec, run_dir)),
        "--forks", str(check_int(spec.get("forks", 5), "forks", 1, 200)),
    ]

    if spec.get("limit"):
        argv += ["--limit", check_limit(spec["limit"])]
    if spec.get("user"):
        argv += ["--user", check_user(spec["user"])]
    for key, option in (("tags", "--tags"), ("skip_tags", "--skip-tags")):
        value = _tags(spec, key)
        if value:
            argv += [option, value]

    for key in ("check", "diff", "become"):
        if spec.get(key) is True:
            argv.append(f"--{key}")
    verbosity = check_int(spec.get("verbosity", 0), "verbosity", 0, 4)
    if verbosity:
        argv.append("-" + "v" * verbosity)

    extra_vars = spec.get("extra_vars")
    if extra_vars:
        if not isinstance(extra_vars, dict):
            raise BadRequest("extra_vars must be an object")
        vars_file = runtime_dir / "extra_vars.json"
        vars_file.write_text(json.dumps(extra_vars))
        argv += ["--extra-vars", f"@{vars_file}"]
    answers = spec.get("survey_vars", {})
    secrets = survey_secrets(spec.get("survey_secrets", {}), credentials_dir)
    if answers or secrets:
        if not isinstance(answers, dict):
            raise BadRequest("survey_vars must be an object")
        # After the template's variables: an answer wins over a fixed value.
        # Secret answers are in clear in this file: it is in the run's runtime
        # directory, on tmpfs, private to the unit and removed with it.
        survey_file = runtime_dir / "survey_vars.yml"
        survey_file.touch(mode=0o600)
        survey_file.write_text(survey_vars_file({**answers, **secrets}))
        argv += ["--extra-vars", f"@{survey_file}"]

    argv += _credential_args(
        credentials_dir, ("private_key", "connection_password", "become_password"))
    argv += _vault_args(spec, credentials_dir)
    argv.append(str(resolve_inside(project, spec.get("playbook"), "playbook")))
    return argv


def build_inventory_argv(spec: dict, run_dir: Path, credentials_dir: Path | None) -> list[str]:
    """ansible-inventory for an "inventory" job; the listing goes to inventory.json."""
    return [
        "/usr/bin/ansible-inventory",
        "--inventory", str(_inventory(spec, run_dir)),
        "--list", "--export",
        "--output", str(run_dir / "inventory.json"),
        *_credential_args(credentials_dir, ("vault_password",)),
    ]


def build_env(run_dir: Path, runtime_dir: Path) -> dict[str, str]:
    """Environment of ansible-playbook and ansible-inventory."""
    project = run_dir / "project"
    home = runtime_dir / "home"
    home.mkdir(mode=0o700, exist_ok=True)
    control = runtime_dir / "cp"
    control.mkdir(mode=0o700, exist_ok=True)
    # Ansible only accepts a .cfg file here. Setting it also keeps the project's
    # own ansible.cfg from being loaded as it is.
    text, collections = generate_config(project)
    config = runtime_dir / "ansible.cfg"
    config.write_text(text)
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
        "ANSIBLE_SSH_ARGS": "-o ControlMaster=auto -o ControlPersist=60s " + _ssh_options(run_dir),
        "ANSIBLE_HOST_KEY_CHECKING": "True",
        "ANSIBLE_COLLECTIONS_PATH": ":".join([str(COLLECTIONS_DIR), str(project), *collections]),
        "ANSIBLE_CALLBACKS_ENABLED": "lineadicomando.fleet.events",
        "ANSIBLE_FORCE_COLOR": "1",
        "FLEET_EVENTS_FILE": str(run_dir / "events.jsonl"),
    }


def _ssh_options(run_dir: Path) -> str:
    return (
        f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={run_dir / 'known_hosts'} "
        "-o GlobalKnownHostsFile=/dev/null"
    )


def build_sync(
    spec: dict, run_dir: Path, runtime_dir: Path, credentials_dir: Path | None
) -> tuple[list[list[str]], dict[str, str]]:
    """The git commands of a "sync" job and their environment.

    A fresh shallow fetch of one reference into run_dir/checkout. Hooks are
    off, submodules are not followed, and only https and ssh are allowed.
    """
    url = check_git_url(spec.get("url"))
    ref = check_git_ref(spec.get("ref"))
    checkout = str(run_dir / "checkout")
    home = runtime_dir / "home"
    home.mkdir(mode=0o700, exist_ok=True)

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https:ssh",
    }
    ssh = f"ssh -o BatchMode=yes {_ssh_options(run_dir)}"
    if credentials_dir is not None:
        key = credentials_dir / "fleet_git_private_key"
        token = credentials_dir / "fleet_git_token"
        if key.is_file() and not needs_agent(credentials_dir, "git_"):
            ssh += f" -o IdentitiesOnly=yes -i {key}"
        if token.is_file():
            # git asks the helper for the user name and the password: the token
            # is read from its file and never appears in argv or env.
            env["GIT_ASKPASS"] = ASKPASS
            env["FLEET_ASKPASS_USER"] = check_user(spec.get("user") or "git")
            env["FLEET_ASKPASS_FILE"] = str(token)
    env["GIT_SSH_COMMAND"] = ssh
    if spec.get("proxy"):
        env["https_proxy"] = str(spec["proxy"])

    git = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never"]
    commands = [
        [*git, "init", "--quiet", checkout],
        [*git, "-C", checkout, "fetch", "--quiet", "--depth", "1", "--no-tags",
         "--no-recurse-submodules", "--", url, ref],
        [*git, "-C", checkout, "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    ]
    return commands, env
