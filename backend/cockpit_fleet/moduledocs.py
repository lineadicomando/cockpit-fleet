"""What the editor's completion knows about modules: their names and their
parameters, as ansible-doc reports them for what is installed on the
controller.

ansible-doc runs here, outside the sandbox, so it must only read what root
installed: it gets a closed environment, a throwaway home and no project.
Modules that a project brings in its own collections are not listed.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import paths
from .cli import Context, Params, command
from .errors import BadRequest, NotFound, Unavailable

_NAME_RE = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+){0,4}")
# Collections installed for the whole machine, by a package or by root.
_COLLECTION_ROOTS = (Path("/usr/lib/python3/dist-packages/ansible_collections"),
                     Path("/usr/share/ansible/collections/ansible_collections"))
_DPKG_STATUS = Path("/var/lib/dpkg/status")
_MAX_TEXT = 400


def _ansible_doc(*args: str) -> dict:
    """The JSON ansible-doc prints for these arguments."""
    with tempfile.TemporaryDirectory(prefix="fleet-doc-") as home:
        env = {
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": home,
            "ANSIBLE_HOME": f"{home}/.ansible", "ANSIBLE_LOCAL_TEMP": f"{home}/scratch",
            "ANSIBLE_COLLECTIONS_PATH": str(_COLLECTION_ROOTS[1].parent),
            "ANSIBLE_NOCOLOR": "1",
        }
        try:
            result = subprocess.run(
                ["/usr/bin/ansible-doc", "--type", "module", "--json", *args],
                capture_output=True, text=True, timeout=120, check=False,
                stdin=subprocess.DEVNULL, env=env, cwd="/",
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Unavailable(f"cannot run ansible-doc: {error}") from error
    if result.returncode != 0:
        raise Unavailable(f"ansible-doc failed: {result.stderr.strip()[-300:]}")
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        raise Unavailable("ansible-doc did not answer with JSON") from error
    return data if isinstance(data, dict) else {}


def _installed() -> str:
    """Changes whenever the set of installed modules may have changed."""
    seen = []
    for path in (_DPKG_STATUS, *_COLLECTION_ROOTS):
        entries = [path, *sorted(path.iterdir())] if path.is_dir() else [path]
        for entry in entries:
            try:
                seen.append(f"{entry}:{entry.stat().st_mtime_ns}")
            except OSError:
                seen.append(f"{entry}:-")
    return hashlib.sha256("\n".join(seen).encode()).hexdigest()


def _text(value: object) -> str:
    """The first paragraph of a description, without ansible-doc's markup."""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = re.sub(r"[A-Z]\(([^)]*)\)", r"\1", str(value or ""))
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


@command("module.list", role="admin")
def module_list(ctx: Context, params: Params) -> dict:
    """Every module installed on the controller, with what it does in a line.

    Listing them takes seconds, so the answer is kept until something is
    installed or removed.
    """
    params.done()
    cache = paths.CACHE_DIR / "modules.json"
    key = _installed()
    try:
        kept = json.loads(cache.read_text())
        if kept.get("key") == key:
            return {"modules": kept["modules"]}
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    modules = {name: _text(description)
               for name, description in sorted(_ansible_doc("--list").items())
               if isinstance(name, str) and _NAME_RE.fullmatch(name)}
    paths.CACHE_DIR.mkdir(mode=0o700, exist_ok=True)
    temporary = cache.with_name(f"modules.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"key": key, "modules": modules}))
    temporary.replace(cache)
    return {"modules": modules}


@command("module.doc", role="admin")
def module_doc(ctx: Context, params: Params) -> dict:
    """The parameters of one module."""
    name = params.string("name", max_length=200)
    params.done()
    if not _NAME_RE.fullmatch(name):
        raise BadRequest(f"invalid module name: {name!r}")
    entry = _ansible_doc(name).get(name)
    doc = entry.get("doc") if isinstance(entry, dict) else None
    if not isinstance(doc, dict):
        raise NotFound(f"no such module: {name}")
    options = []
    for option, details in sorted((doc.get("options") or {}).items()):
        if not isinstance(details, dict):
            continue
        choices = details.get("choices")
        options.append({
            "name": option,
            "type": str(details.get("type") or "str"),
            "required": details.get("required") is True,
            "default": details.get("default"),
            "choices": [str(choice) for choice in choices] if isinstance(choices, list) else [],
            "aliases": [str(alias) for alias in details.get("aliases") or []],
            "description": _text(details.get("description")),
        })
    return {"name": name, "description": _text(doc.get("short_description")), "options": options}
