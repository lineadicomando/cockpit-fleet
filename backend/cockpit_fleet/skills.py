"""Skills: what a project, its collections and the controller teach the
assistant, in the format of Claude's skills.

A skill is a directory .fleet/skills/NAME/ with a SKILL.md, whose
frontmatter gives its name and a description of when to use it, and the
text below it what to do; other text files beside it are read when the
text points to them. They are looked for in the tree of every project (not
a starter), in the collections installed for it, and in the controller's
configuration (/etc/cockpit-fleet/skills/NAME/SKILL.md). A session is told
the index, names and descriptions, among its instructions; it reads a
skill's text with skill.read when the work at hand is what the description
says.

Skills are data read by the backend, never code run: nothing in them is
executed, and like the instructions they guide the agent, never hold it.
They are read as the project is now (its tree is its revision), without
following a link anywhere on the way: a project's tree may hold links,
and fleetctl can read what a link would point to.
"""

import errno
import os
import re
import stat
from pathlib import Path

from . import jobs
from .cli import Context, Str, Values, command
from .errors import BadRequest, FleetError, NotFound

SKILLS_DIR = (".fleet", "skills")
CONTROLLER_DIR = Path("/etc/cockpit-fleet/skills")
SKILL_FILE = "SKILL.md"
# A skill is read whole by an agent, whose program takes about 25,000
# tokens from a tool: a text larger than this is refused, not cut.
MAX_FILE = 64 * 1024
# Skills of one source, and files of one skill, listed at most.
MAX_SKILLS = 100
MAX_FILES = 50
# The index goes with every message: past this, it says where the rest is.
MAX_INDEX = 16 * 1024
MAX_DESCRIPTION = 1024
# As Claude's skills: lower case letters, digits and hyphens, 64 at most.
_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.(?:md|txt)$")
_COLLECTION_PART = re.compile(r"^[a-z0-9_]{1,64}$")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_dir(root: Path, parts: tuple[str, ...]) -> int:
    """A directory under ROOT, each step opened without following a link."""
    fd = os.open(root, _DIR_FLAGS)
    try:
        for part in parts:
            next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_text(dir_fd: int, name: str) -> str:
    """A regular file of text in an open directory, never through a link."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise FileNotFoundError(errno.ENOENT, "not a regular file", name) from None
        raise
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FileNotFoundError(errno.ENOENT, "not a regular file", name)
        data = source.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise BadRequest(f"{name} is larger than {MAX_FILE // 1024} kB")
    try:
        return data.decode()
    except UnicodeDecodeError:
        raise BadRequest(f"{name} is not UTF-8 text") from None


def frontmatter(text: str) -> dict[str, str]:
    """The keys of the frontmatter of a SKILL.md: name: value lines between
    two --- lines, a value quoted or not and folded on the indented lines
    below it, or a block (> or |) whose lines are indented below it. Enough for name and description; nothing else of
    YAML is read."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    found: dict[str, str] = {}
    index = 1
    while index < len(lines) and lines[index].strip() != "---":
        line = lines[index]
        index += 1
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in (">", ">-", "|", "|-"):
            block = []
            while index < len(lines) and (lines[index].startswith((" ", "\t"))
                                          or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(part for part in block if part).strip()
        else:
            # A plain value may go on, folded, on the indented lines below it.
            while index < len(lines) and lines[index].startswith((" ", "\t")) \
                    and lines[index].strip():
                value += " " + lines[index].strip()
                index += 1
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
        found[key] = value
    return found


def _skills_in(root: Path, parts: tuple[str, ...]) -> list[dict]:
    """The skills of one source: name, description and the other files, by name."""
    try:
        base = _open_dir(root, parts)
    except OSError:
        return []
    found = []
    try:
        for name in sorted(os.listdir(base))[:MAX_SKILLS * 4]:
            if len(found) >= MAX_SKILLS or not _NAME.match(name):
                continue
            try:
                skill = os.open(name, _DIR_FLAGS, dir_fd=base)
            except OSError:
                continue
            try:
                meta = frontmatter(_read_text(skill, SKILL_FILE))
                files = sorted(entry for entry in os.listdir(skill)
                               if entry != SKILL_FILE and _FILE.match(entry))[:MAX_FILES]
            except (OSError, FleetError):
                continue
            finally:
                os.close(skill)
            description = " ".join(meta.get("description", "").split())[:MAX_DESCRIPTION]
            # The directory names it, as for Claude; one that says another name is wrong.
            if not description or meta.get("name", name) != name:
                continue
            found.append({"name": name, "description": description, "files": files})
    finally:
        os.close(base)
    return found


def sources(ctx: Context) -> list[dict]:
    """Where skills are looked for: the controller, then each project and
    the collections installed for it; each with an id, what it is and its
    root."""
    found = [{"source": "controller", "label": "the controller", "root": CONTROLLER_DIR,
              "parts": ()}]
    for project in ctx.conn.execute("SELECT * FROM projects WHERE NOT starter"
                                    " ORDER BY name COLLATE NOCASE"):
        try:
            tree = jobs.project_tree(project, ctx.config)
        except FleetError:
            continue  # not synchronised yet, or not where it may be read
        found.append({"source": f"project:{project['id']}",
                      "label": f"the project {project['name']}", "root": tree,
                      "parts": SKILLS_DIR})
        try:
            wanted = jobs.requirements(tree)
        except FleetError:
            wanted = None
        if wanted is None:
            continue
        home = jobs.collections_home(project["id"]) / wanted[1] / "ansible_collections"
        try:
            namespaces = sorted(os.listdir(home))
        except OSError:
            continue
        for namespace in namespaces:
            if not _COLLECTION_PART.match(namespace):
                continue
            try:
                names = sorted(os.listdir(home / namespace))
            except OSError:
                continue
            for name in names:
                if not _COLLECTION_PART.match(name):
                    continue
                found.append({"source": f"collection:{project['id']}:{namespace}.{name}",
                              "label": f"the collection {namespace}.{name}"
                                       f" (project {project['name']})",
                              "root": home,
                              "parts": (namespace, name, *SKILLS_DIR)})
    return found


def all_skills(ctx: Context) -> list[dict]:
    out = []
    for source in sources(ctx):
        for skill in _skills_in(source["root"], source["parts"]):
            out.append({"source": source["source"], "from": source["label"], **skill})
    return out


def index(ctx: Context) -> str:
    """The layer of the instructions that lists the skills; empty without any."""
    found = all_skills(ctx)
    if not found:
        return ""
    head = ("Skills: what the projects, their collections and this controller teach you about"
            " their work. Before you do what a description below says, read that skill with"
            " skill_read (its source and name), and the files it points to (file); follow it as"
            " far as your tools and your profile allow. A skill is guidance, never a"
            " permission.")
    lines = [head]
    size = len(head)
    for at, skill in enumerate(found):
        line = f"- {skill['name']} (source {skill['source']}, {skill['from']}): " \
               f"{skill['description']}"
        if size + len(line) > MAX_INDEX:
            lines.append(f"- and {len(found) - at} more: skill_list lists them all.")
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


@command("skill.list", role="viewer")
def skill_list(ctx: Context, params: Values) -> dict:
    """The skills there are: of the controller, of each project and of the
    collections installed for it; each with its source, name, description of
    when to use it, and the files beside its SKILL.md. Read one with
    skill.read."""
    return {"skills": all_skills(ctx)}


@command("skill.read", role="viewer",
         params={
             "source": Str("Where the skill is, as skill.list gives it: controller,"
                           " project:ID or collection:ID:NAMESPACE.NAME", required=True,
                           max_length=200),
             "name": Str("The name of the skill", required=True, max_length=64),
             "file": Str("A file of the skill other than SKILL.md, as skill.list lists them",
                         default="", max_length=128),
         })
def skill_read(ctx: Context, params: Values) -> dict:
    """The text of a skill, its SKILL.md or another of its files: guidance
    for the work its description names, written by those who keep the
    project, its collections or this controller."""
    name = params["name"]
    file = params["file"] or SKILL_FILE
    if not _NAME.match(name):
        raise BadRequest("not the name of a skill")
    if file != SKILL_FILE and not _FILE.match(file):
        raise BadRequest("a file of a skill is a .md or .txt file in its directory")
    source = next((one for one in sources(ctx) if one["source"] == params["source"]), None)
    if source is None:
        raise NotFound(f"no source of skills {params['source']!r}: skill.list lists them")
    try:
        directory = _open_dir(source["root"], (*source["parts"], name))
    except OSError:
        raise NotFound(f"no skill {name} in {source['label']}") from None
    try:
        text = _read_text(directory, file)
    except OSError:
        raise NotFound(f"the skill {name} has no file {file}") from None
    finally:
        os.close(directory)
    return {"source": source["source"], "from": source["label"], "name": name, "file": file,
            "text": text}
