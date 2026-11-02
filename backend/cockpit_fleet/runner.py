"""Turn a stored job specification into the commands a run unit executes.

A job is one of eight kinds: "playbook" (ansible-playbook), "adhoc" (one
module, as a playbook written for the job), "inventory" (ansible-inventory, to
list hosts and groups), "sync" (fetch a git project), "syntax" (check a
playbook without running it), "unpack" (extract an uploaded archive, which
runs no command: see archive.py), "galaxy" (install the collections a
project asks for) and "check" (a cycle of the checks of the catalog, one
task per host, as a playbook written for the job). Nothing from the frontend reaches a
command line without going through the functions here.
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
    check_inventory_host,
    check_limit,
    check_module,
    check_tag,
    check_user,
    check_vault_id,
    resolve_inside,
)

KINDS = ("playbook", "adhoc", "inventory", "sync", "syntax", "unpack", "galaxy", "check")

# Optional: when it is installed, a syntax job runs it after the check.
LINT = "/usr/bin/ansible-lint"

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
    """An inventory of the project, a file or a directory of sources, or a
    copy of the tree of one written in the interface (all of it, or a file
    or directory of it)."""
    if spec.get("inventory_source") == "managed":
        return resolve_inside(run_dir / "inventory", spec.get("inventory"), "inventory",
                              directory=True)
    return resolve_inside(run_dir / "project", spec.get("inventory"), "inventory",
                          directory=True)


# The plugins that read an inventory source, Ansible's own defaults. Without
# "script" an executable file is read like any other, never run.
_INVENTORY_PLUGINS = ("host_list", "script", "auto", "yaml", "ini", "toml")


def secret_values(credentials_dir: Path | None) -> list[bytes]:
    """What must not be readable in the output of a run: its decrypted
    secrets, longest first.

    A secret of several lines, a private key, is taken line by line, without
    the lines that only frame it. Anything shorter than four bytes is left
    alone: masking it would mostly hide ordinary output. A value is looked
    for as it is and as JSON writes it; any other encoding of it (base64, a
    hash, one character per line) is not recognised.
    """
    found: set[bytes] = set()
    if credentials_dir is not None:
        try:
            files = sorted(credentials_dir.glob("fleet_*"))
        except OSError:
            files = []
        for file in files:
            try:
                data = file.read_bytes()
            except OSError:
                continue
            for line in data.splitlines():
                line = line.strip()
                if len(line) >= 4 and not line.startswith(b"-----"):
                    found.add(line)
                    try:
                        # Ansible prints results as JSON: the same value, escaped.
                        found.add(json.dumps(line.decode(), ensure_ascii=False)[1:-1].encode())
                    except UnicodeDecodeError:
                        pass
    return sorted(found, key=len, reverse=True)


MASK = b"********"


def mask(line: bytes, secrets: list[bytes]) -> bytes:
    for secret in secrets:
        if secret in line:
            line = line.replace(secret, MASK)
    return line


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
        # Always with a label: the path holds the "@" of the unit's name, and
        # alone it would be read as LABEL@FILE. "default" is Ansible's own
        # name for a vault without one.
        label = check_vault_id(vault.get("id") or "default")
        argv += ["--vault-id", f"{label}@{secret}"]
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


def _unsafe_value(value: object, depth: int = 0) -> str:
    if depth > 20:
        raise BadRequest("workflow_vars: too deeply nested")
    if isinstance(value, str):
        return f"!unsafe {_yaml_string(value)}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and math.isfinite(value):
        text = repr(value)
        return text.replace("e", ".0e") if "e" in text and "." not in text else text
    if isinstance(value, list):
        return "[" + ", ".join(_unsafe_value(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_yaml_string(str(key))}: {_unsafe_value(item, depth + 1)}"
                               for key, item in value.items()) + "}"
    raise BadRequest("workflow_vars: a value is not JSON")


def workflow_vars_file(variables: object) -> str:
    """What the steps before this one in a workflow passed on, as a YAML file
    of variables. A playbook wrote them: every string, at every depth, is
    tagged !unsafe, so that none is rendered as a template here."""
    if not isinstance(variables, dict) or len(variables) > 200:
        raise BadRequest("workflow_vars must be an object")
    lines = []
    for name, value in variables.items():
        check_variable(name)
        lines.append(f"{name}: {_unsafe_value(value)}\n")
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


def _playbook_argv(spec: dict, run_dir: Path, runtime_dir: Path,
                   credentials_dir: Path | None, playbook: Path,
                   limit_file: Path | None = None) -> list[str]:
    argv = [
        "/usr/bin/ansible-playbook",
        "--inventory", str(_inventory(spec, run_dir)),
        "--forks", str(check_int(spec.get("forks", 5), "forks", 1, 200)),
    ]

    if limit_file is not None:
        argv += ["--limit", f"@{limit_file}"]
    elif spec.get("limit"):
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
    passed = spec.get("workflow_vars", {})
    if passed:
        # Last: what the steps before passed on wins, as in AWX.
        passed_file = runtime_dir / "workflow_vars.yml"
        passed_file.touch(mode=0o600)
        passed_file.write_text(workflow_vars_file(passed))
        argv += ["--extra-vars", f"@{passed_file}"]

    argv += _credential_args(
        credentials_dir, ("private_key", "connection_password", "become_password"))
    argv += _vault_args(spec, credentials_dir)
    argv.append(str(playbook))
    return argv


def build_argv(
    spec: dict, run_dir: Path, runtime_dir: Path, credentials_dir: Path | None,
    limit_file: Path | None = None,
) -> list[str]:
    """ansible-playbook for a "playbook" job; LIMIT_FILE, when its limits
    were resolved by limit_plan(), lists the hosts it is for."""
    playbook = resolve_inside(run_dir / "project", spec.get("playbook"), "playbook")
    return _playbook_argv(spec, run_dir, runtime_dir, credentials_dir, playbook, limit_file)


def limit_plan(spec: dict, run_dir: Path, runtime_dir: Path,
               credentials_dir: Path | None) -> list[tuple[list[str], Path]]:
    """For a run with several limits (the template's, the caller's), one
    ansible-inventory per limit, with the file where it writes the hosts it
    selects. A host must be in all of them: see limit_hosts()."""
    limits = spec.get("limits")
    if not limits:
        return []
    if not isinstance(limits, list) or len(limits) > 4:
        raise BadRequest("limits must be a list")
    plan = []
    for index, pattern in enumerate(limits):
        output = runtime_dir / f"limit-{index}.json"
        plan.append(([
            "/usr/bin/ansible-inventory",
            "--inventory", str(_inventory(spec, run_dir)),
            "--list", "--limit", check_limit(pattern),
            "--output", str(output),
            *_vault_args(spec, credentials_dir),
        ], output))
    return plan


# A name that a limit file reads as that host and nothing else.
_LITERAL_HOST_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,252}|[0-9A-Fa-f]*:[0-9A-Fa-f:.]+")


def limit_hosts(listings: list[dict]) -> str:
    """The hosts in every listing, as a limit file: one name per line."""
    chosen = None
    groups: set[str] = {"all", "ungrouped"}
    for listing in listings:
        if not isinstance(listing, dict):
            raise BadRequest("ansible-inventory gave no listing")
        hosts = set(listing.get("_meta", {}).get("hostvars", {}))
        for name, group in listing.items():
            if name != "_meta" and isinstance(group, dict):
                groups.add(name)
                hosts.update(group.get("hosts", []))
        chosen = hosts if chosen is None else chosen & hosts
    if not chosen:
        raise BadRequest("no host matches both the template's limit and the one given")
    for host in chosen:
        # A line of the file is a pattern: a group's name would bring the group.
        if not isinstance(host, str) or not _LITERAL_HOST_RE.fullmatch(host) or host in groups:
            raise BadRequest(f"the host {host!r} has a name a limit cannot hold:"
                             " launch with no limit of your own")
    return "".join(f"{host}\n" for host in sorted(chosen))


# An ad-hoc command prints what each host answered, the way "ansible" does.
ADHOC_ENV = {"ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.minimal"}


def adhoc_playbook(spec: dict) -> str:
    """The playbook of an "adhoc" job: one play of one task, on every host
    the limit leaves.

    The arguments are those of a task: a mapping, a line in free form (the
    command of ansible.builtin.command, or key=value pairs), or both. They
    are written by an administrator and stay templates, as in a playbook.
    """
    module = check_module(spec.get("module"))
    free_form = spec.get("free_form", "")
    args = spec.get("args", {})
    if not isinstance(free_form, str) or not isinstance(args, dict):
        raise BadRequest("free_form must be a string and args an object")
    task: dict = {"name": module, module: free_form or args}
    if free_form and args:
        task["args"] = args
    # JSON is YAML: nothing here needs quoting rules of our own.
    return json.dumps([{"name": "Ad-hoc command", "hosts": "all", "gather_facts": False,
                        "tasks": [task]}])


def build_adhoc_argv(
    spec: dict, run_dir: Path, runtime_dir: Path, credentials_dir: Path | None
) -> list[str]:
    """ansible-playbook for an "adhoc" job, on a playbook written here.

    The module and its arguments are in that file, not in the command line.
    A limit is required: it is what chooses the hosts.
    """
    check_limit(spec.get("limit"))
    playbook = runtime_dir / "adhoc.yml"
    playbook.write_text(adhoc_playbook(spec))
    return _playbook_argv(spec, run_dir, runtime_dir, credentials_dir, playbook)


CHECKS_MODULE = "lineadicomando.fleet.checks"
# A cycle every minute must not fill the journal: what a host answers is in
# the events, the output only says which task ran and which host failed.
# The packages and services of a node are lists of thousands: the events of
# a cycle keep them whole.
CHECK_ENV = {"ANSIBLE_STDOUT_CALLBACK": "ansible.builtin.default",
             "ANSIBLE_DISPLAY_OK_HOSTS": "False", "ANSIBLE_DISPLAY_SKIPPED_HOSTS": "False",
             "FLEET_EVENTS_MAX_ITEMS": "50000"}
CHECK_MAX_TIMEOUT = 3600
# ansible-playbook's status when some hosts failed (2) or could not be
# reached (4): for a cycle that is an outcome of the hosts, which their
# states record, not a failure of the cycle.
CHECK_RCS = (0, 2, 4)
_CHECK_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_CHECK_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def unsafe_yaml(value: object, depth: int = 0) -> str:
    """A value as YAML in flow style, every string tagged !unsafe: Ansible
    uses it as it is written, and renders no template in it."""
    if depth > 8:
        raise BadRequest("the plan of a check cycle is too deep")
    if isinstance(value, str):
        return f"!unsafe {_yaml_string(value)}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        text = repr(value)
        # YAML 1.1 reads "1e+16" as a string: a float needs its dot.
        return text.replace("e", ".0e") if "e" in text and "." not in text else text
    if isinstance(value, list):
        return "[" + ", ".join(unsafe_yaml(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_yaml_string(str(key))}: {unsafe_yaml(item, depth + 1)}"
                               for key, item in value.items()) + "}"
    raise BadRequest("the plan of a check cycle holds a value that is not data")


def check_plan(plan: object) -> dict[str, list[dict]]:
    """The checks of a cycle, by host: what fleetctl planned, checked again."""
    if not isinstance(plan, dict) or not 1 <= len(plan) <= 1000:
        raise BadRequest("plan must name between 1 and 1000 hosts")
    out = {}
    for host, entries in plan.items():
        check_inventory_host(host)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise BadRequest("each host of the plan has between 1 and 100 checks")
        out[host] = []
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"id", "check", "params"}
                    or not isinstance(entry["id"], str) or not _CHECK_ID_RE.fullmatch(entry["id"])
                    or not isinstance(entry["check"], str)
                    or not _CHECK_NAME_RE.fullmatch(entry["check"])
                    or not isinstance(entry["params"], dict)):
                raise BadRequest("invalid entry in the plan of a check cycle")
            out[host].append(entry)
    return out


def check_playbook(timeout: int) -> str:
    """The playbook of a "check" job: every check of a host in one task, which
    Ansible answers with checks.py or, for a PowerShell shell, checks.ps1."""
    task = {"name": "Checks", CHECKS_MODULE: {"checks": "{{ fleet_plan[inventory_hostname] }}"},
            "timeout": timeout}
    return json.dumps([{"name": "Checks", "hosts": "all", "gather_facts": False,
                        "strategy": "free", "tasks": [task]}])


def check_env(spec: dict) -> dict[str, str]:
    """What a "check" job adds to the environment: little output, and the
    time limit of each task, for a playbook of the user too. The catalog is
    pipelined: its one task per host goes as one command over the
    connection, instead of a directory made, a file copied, run and removed.
    A playbook of the user keeps what its project says; an inventory that
    sets ansible_pipelining for a host still has the last word, and Ansible
    always pipelines over SSH to a PowerShell shell."""
    timeout = check_int(spec.get("timeout", 60), "timeout", 5, CHECK_MAX_TIMEOUT)
    env = {**CHECK_ENV, "ANSIBLE_TASK_TIMEOUT": str(timeout)}
    if not spec.get("playbook"):
        env["ANSIBLE_PIPELINING"] = "True"
    return env


def build_check_argv(
    spec: dict, run_dir: Path, runtime_dir: Path, credentials_dir: Path | None
) -> list[str]:
    """ansible-playbook for a "check" job: the catalog in one task written
    here, or a playbook of the project that ends with fleet_result. The plan
    is a variables file and the hosts a file given to --limit, so neither is
    in the command line. A compliance check runs its playbook in --check
    --diff: what would change is the drift."""
    plan = check_plan(spec.get("plan"))
    timeout = check_int(spec.get("timeout", 60), "timeout", 5, CHECK_MAX_TIMEOUT)
    if spec.get("playbook"):
        playbook = resolve_inside(run_dir / "project", spec["playbook"], "playbook")
    else:
        playbook = runtime_dir / "checks.yml"
        playbook.write_text(check_playbook(timeout))
    plan_file = runtime_dir / "plan.yml"
    plan_file.write_text(f"fleet_plan: {unsafe_yaml(plan)}\n")
    hosts = runtime_dir / "hosts.limit"
    hosts.write_text("".join(f"{host}\n" for host in plan))
    clean = {key: spec[key] for key in ("inventory", "inventory_source", "forks", "user",
                                         "become", "vaults") if key in spec}
    if spec.get("playbook") and spec.get("compliance") is True:
        clean.update(check=True, diff=True)
    argv = _playbook_argv(clean, run_dir, runtime_dir, credentials_dir, playbook)
    return [*argv[:-1], "--limit", f"@{hosts}", "--extra-vars", f"@{plan_file}", argv[-1]]


def build_inventory_argv(spec: dict, run_dir: Path, credentials_dir: Path | None) -> list[str]:
    """ansible-inventory for an "inventory" job; the listing goes to inventory.json."""
    return [
        "/usr/bin/ansible-inventory",
        "--inventory", str(_inventory(spec, run_dir)),
        "--list", "--export",
        "--output", str(run_dir / "inventory.json"),
        *_credential_args(credentials_dir, ("vault_password",)),
    ]


def build_syntax_argv(spec: dict, run_dir: Path) -> list[str]:
    """ansible-playbook for a "syntax" job: the playbook is read, nothing runs.

    The inventory is a list of one name, so that no file is looked for and
    Ansible does not warn that there is none.
    """
    playbook = resolve_inside(run_dir / "project", spec.get("playbook"), "playbook")
    return ["/usr/bin/ansible-playbook", "--syntax-check", "--inventory", "localhost,",
            str(playbook)]


def build_lint_argv(spec: dict, run_dir: Path) -> list[str]:
    """ansible-lint on the same playbook. Offline: it must not install anything."""
    playbook = resolve_inside(run_dir / "project", spec.get("playbook"), "playbook")
    return [LINT, "--offline", str(playbook)]


def build_env(run_dir: Path, runtime_dir: Path, events: bool = True,
              scripts: bool = False, credentials_dir: Path | None = None) -> dict[str, str]:
    """Environment of ansible-playbook and ansible-inventory.

    scripts lets an executable file of the project be run as an inventory.
    credentials_dir tells the events callback which values to mask."""
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
    env = {
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
        "ANSIBLE_FORCE_COLOR": "1",
        "ANSIBLE_INVENTORY_ENABLED": ",".join(
            plugin for plugin in _INVENTORY_PLUGINS if scripts or plugin != "script"),
        # An inventory nothing could read stops the job, instead of leaving
        # a playbook with no host and a successful run of nothing.
        "ANSIBLE_INVENTORY_UNPARSED_FAILED": "True",
    }
    # What was installed from the project's requirements file, after what the
    # project carries itself.
    installed = run_dir / "collections"
    if installed.is_dir() and not installed.is_symlink():
        env["ANSIBLE_COLLECTIONS_PATH"] += f":{installed}"
    if events:
        env["ANSIBLE_CALLBACKS_ENABLED"] = "lineadicomando.fleet.events"
        env["FLEET_EVENTS_FILE"] = str(run_dir / "events.jsonl")
        if credentials_dir is not None:
            env["FLEET_EVENTS_SECRETS_DIR"] = str(credentials_dir)
    return env


def _ssh_options(run_dir: Path) -> str:
    return (
        f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={run_dir / 'known_hosts'} "
        "-o GlobalKnownHostsFile=/dev/null"
    )


# git as a job runs it: no configuration but the repository's own, no prompt,
# and no transport that could run a local program.
_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "https:ssh",
}


def build_galaxy(spec: dict, run_dir: Path, runtime_dir: Path) -> tuple[list[str], dict[str, str]]:
    """ansible-galaxy for a "galaxy" job, and its environment.

    The collections of the project's requirements file go to run_dir/galaxy.
    What the controller or the project already has is not installed again.
    A collection taken from git is cloned by ansible-galaxy itself, which
    takes no option for git: hooks and local transports are turned off
    through the environment. No credential is offered to any source.
    """
    requirements = resolve_inside(run_dir / "project", spec.get("requirements"), "requirements")
    target = run_dir / "galaxy"
    env = build_env(run_dir, runtime_dir, events=False)
    env["ANSIBLE_COLLECTIONS_PATH"] += f":{target}"
    # Clones and downloads: on disk, not in the runtime directory, which is memory.
    env["ANSIBLE_LOCAL_TEMP"] = str(run_dir / "scratch")
    env.update(_GIT_ENV)
    env.update({
        "GIT_SSH_COMMAND": f"ssh -o BatchMode=yes {_ssh_options(run_dir)}",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
        "GIT_CONFIG_KEY_1": "protocol.file.allow", "GIT_CONFIG_VALUE_1": "never",
    })
    if spec.get("proxy"):
        env["https_proxy"] = str(spec["proxy"])
    argv = ["/usr/bin/ansible-galaxy", "collection", "install",
            "--requirements-file", str(requirements), "--collections-path", str(target)]
    return argv, env


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

    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", **_GIT_ENV}
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


def usage(cgroup_file: Path = Path("/proc/self/cgroup"),
          root: Path = Path("/sys/fs/cgroup")) -> dict[str, int]:
    """What the unit has used so far: the peak of its memory, in bytes, and
    its processor time, in microseconds. fleet-run is the main process of the
    unit, so its own cgroup is the unit's; systemd forgets both once the unit
    has stopped. What cannot be read is left out."""
    found: dict[str, int] = {}
    try:
        lines = cgroup_file.read_text().splitlines()
        path = next(line[3:] for line in lines if line.startswith("0::"))
        group = root / path.lstrip("/")
        if not group.resolve().is_relative_to(root.resolve()):
            return found
    except (OSError, StopIteration):
        return found
    try:
        found["memory_peak"] = int((group / "memory.peak").read_text().strip())
    except (OSError, ValueError):
        pass
    try:
        for line in (group / "cpu.stat").read_text().splitlines():
            name, _, value = line.partition(" ")
            if name == "usage_usec":
                found["cpu_usec"] = int(value)
    except (OSError, ValueError):
        pass
    return found
