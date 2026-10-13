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
from .cli import Context, Str, Values, command
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


def _plain(text: str) -> str:
    """A line of documentation without ansible-doc's markup: L(text, url)
    becomes "text (url)", the others (C, I, M, O, V...) what they enclose."""
    text = re.sub(r"L\(([^,)]*),\s*([^)]*)\)", r"\1 (\2)", text)
    return re.sub(r"[A-Z]\(([^)]*)\)", r"\1", text)


def _text(value: object) -> str:
    """The first paragraph of a description, without ansible-doc's markup."""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = _plain(str(value or ""))
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def _paragraphs(value: object, limit: int = 40) -> list[str]:
    """Every paragraph of a description, without ansible-doc's markup."""
    if value is None or value == "":
        return []
    items = value if isinstance(value, list) else [value]
    return [_plain(str(item))[:4000] for item in items[:limit]]


def _documented(name: str) -> dict:
    """What ansible-doc knows of one module: "doc", "examples", "return"."""
    if not _NAME_RE.fullmatch(name):
        raise BadRequest(f"invalid module name: {name!r}")
    entry = _ansible_doc(name).get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("doc"), dict):
        raise NotFound(f"no such module: {name}")
    return entry


def _option(name: str, details: dict, full: bool, depth: int = 0) -> dict:
    choices = details.get("choices")
    option = {
        "name": name,
        "type": str(details.get("type") or "str"),
        "required": details.get("required") is True,
        "default": details.get("default"),
        "choices": [str(choice) for choice in choices] if isinstance(choices, list) else [],
        "aliases": [str(alias) for alias in details.get("aliases") or []],
        "description": _text(details.get("description")),
    }
    if full:
        option["text"] = _paragraphs(details.get("description"))
        nested = details.get("suboptions")
        option["suboptions"] = _options(nested, True, depth + 1) if depth < 3 else []
    return option


def _options(options: object, full: bool, depth: int = 0) -> list[dict]:
    if not isinstance(options, dict):
        return []
    return [_option(str(name), details, full, depth)
            for name, details in sorted(options.items()) if isinstance(details, dict)]


def _returns(values: object, depth: int = 0) -> list[dict]:
    if not isinstance(values, dict):
        return []
    return [{
        "name": str(name),
        "type": str(details.get("type") or ""),
        "returned": str(details.get("returned") or ""),
        "text": _paragraphs(details.get("description")),
        "sample": details.get("sample"),
        "contains": _returns(details.get("contains"), depth + 1) if depth < 3 else [],
    } for name, details in sorted(values.items()) if isinstance(details, dict)]


@command("module.list", role="admin", tool=False)
def module_list(ctx: Context, params: Values) -> dict:
    """Every module installed on the controller, with what it does in a line.

    Listing them takes seconds, so the answer is kept until something is
    installed or removed.
    """
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


@command("module.doc", role="admin",
         params={
             "name": Str(required=True, max_length=200),
         })
def module_doc(ctx: Context, params: Values) -> dict:
    """The parameters of one module, for the editor's completion."""
    name = params["name"]
    doc = _documented(name)["doc"]
    return {"name": name, "description": _text(doc.get("short_description")),
            "options": _options(doc.get("options"), False)}


@command("module.help", role="admin",
         params={
             "name": Str(required=True, max_length=200),
         })
def module_help(ctx: Context, params: Values) -> dict:
    """The whole documentation of one module: what it does, its parameters,
    its examples and what it returns."""
    name = params["name"]
    entry = _documented(name)
    doc = entry["doc"]
    examples = entry.get("examples")
    return {
        "name": name,
        "collection": str(doc.get("collection") or ""),
        "version_added": str(doc.get("version_added") or ""),
        "description": _text(doc.get("short_description")),
        "text": _paragraphs(doc.get("description")),
        "requirements": _paragraphs(doc.get("requirements")),
        "notes": _paragraphs(doc.get("notes")),
        "options": _options(doc.get("options"), True),
        "examples": examples.strip("\n")[:64 * 1024] if isinstance(examples, str) else "",
        "returns": _returns(entry.get("return")),
    }
