"""Drafts: what agents wrote in managed projects and inventories, waiting
for a person to review it and take it into main.

A draft is the branch drafts/SESSION of the repository of a project or
inventory, made by files.py when an agent of that session of fleet-mcp first
writes there; no run reads it, since templates, schedules and checks copy
the project's own tree, which is main. Here a person lists drafts, looks at
what one changes and at the syntax checks of it, takes all or some of its
files into main as one commit of theirs, and discards it. Taking and
discarding are refused to agents: a program does not approve its own code.

A draft is taken file by file, not merged line by line: a file goes into
main as the draft has it, provided main still has the file as the draft
found it, or already has it as the draft does. A file changed on both sides
is a conflict, for a person to settle by editing the draft or main.
"""

import os
from pathlib import Path, PurePosixPath

from . import files, store
from .cli import Context, Id, Str, Strs, Values, command
from .errors import BadRequest, Conflict, NotFound
from .validators import check_file_path

MAX_PATHS = files.MAX_LISTED


def _entries(container_id: str, revision: str | None) -> dict[str, str]:
    """The files of a commit, path to object id; none for no commit."""
    if revision is None:
        return {}
    listing = files.git(container_id, "ls-tree", "-r", "-z", "--full-tree", revision) or ""
    found = {}
    for entry in listing.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            found[path] = parts[2]
    return found


def _under(entries: dict[str, str], directory: str) -> bool:
    return any(path.startswith(directory + "/") for path in entries)


def pending(draft: files.Draft, tip: str) -> list[dict]:
    """What the draft at TIP changes that main does not have yet, path by
    path: added, changed or deleted, and whether main changed it too since
    the draft started (a conflict). A directory the draft keeps empty is one
    entry, added, while main has nothing in it."""
    container_id = draft.container_id
    base = files.draft_base(draft, tip)
    ours, theirs = _entries(container_id, tip), _entries(container_id, files.head(container_id))
    start = _entries(container_id, base)
    found = []
    for path in sorted(set(ours) | set(start)):
        if ours.get(path) == start.get(path) or theirs.get(path) == ours.get(path):
            continue
        status = "added" if path not in start else "deleted" if path not in ours else "changed"
        name = PurePosixPath(path)
        if name.name == files.KEEP:
            directory = str(name.parent)
            if status == "added" and directory != "." and not _under(theirs, directory):
                found.append({"path": directory, "status": "added", "directory": True,
                              "conflict": False})
            continue
        found.append({"path": path, "status": status, "directory": False,
                      "conflict": theirs.get(path) != start.get(path)})
    return found


def _draft(ctx: Context, params: Values) -> tuple[dict, files.Draft, str]:
    """The project or inventory, its draft that the parameters name, and the
    last commit of the draft."""
    row = files.managed(ctx, params["id"])
    draft = files.Draft(row["id"], files.check_draft(params["draft"]))
    tip = files.draft_tip(draft)
    if tip is None:
        raise NotFound(f"no such draft: {draft.session}")
    return row, draft, tip


def _checks(ctx: Context, row, draft: files.Draft, tip: str) -> list[dict]:
    """The last syntax check of each file of the draft that had one."""
    column = "inventory_id" if files.is_inventory(row["id"]) else "project_id"
    rows = ctx.conn.execute(
        f"SELECT id, status, revision, options FROM runs WHERE kind = 'syntax'"  # noqa: S608
        f" AND {column} = ? ORDER BY created_at DESC, rowid DESC LIMIT 1000", (row["id"],))
    seen: dict[str, dict] = {}
    for run in rows:
        options = store.loads(run["options"], {})
        if options.get("draft") != draft.session:
            continue
        path = str(options.get("path", ""))
        if path not in seen:
            seen[path] = {"run_id": run["id"], "path": path, "status": run["status"],
                          "revision": run["revision"], "current": run["revision"] == tip,
                          "lint": options.get("lint")}
    return sorted(seen.values(), key=lambda check: check["path"])


def _author(container_id: str, tip: str) -> tuple[str, int]:
    """Who the draft's last commit is by, and when it was made."""
    line = files.git(container_id, "log", "-1", "--format=%an%x1f%at", tip) or ""
    author, _, time = line.strip().partition("\x1f")
    return author, int(time or 0)


@command("draft.list", role="admin",
         params={
             "id": Id("A managed project or inventory; every one if not given", required=False),
         })
def draft_list(ctx: Context, params: Values) -> dict:
    """The drafts agents left in managed projects and inventories, waiting
    for a person to merge or discard them: whose session, when last written,
    how many files they change and how many of those main changed too."""
    if params["id"]:
        rows = [files.managed(ctx, params["id"])]
    else:
        rows = [*ctx.conn.execute("SELECT * FROM projects WHERE kind = 'managed' ORDER BY name"),
                *ctx.conn.execute("SELECT * FROM inventories WHERE kind = 'managed'"
                                  " ORDER BY name")]
    found = []
    for row in rows:
        for session in files.drafts_of(row["id"]):
            draft = files.Draft(row["id"], session)
            tip = files.draft_tip(draft)
            if tip is None:
                continue
            changes = pending(draft, tip)
            author, time = _author(row["id"], tip)
            found.append({"id": row["id"], "name": row["name"],
                          "kind": "inventory" if files.is_inventory(row["id"]) else "project",
                          "draft": session, "revision": tip, "author": author, "time": time,
                          "changes": len(changes),
                          "conflicts": sum(1 for change in changes if change["conflict"])})
    found.sort(key=lambda entry: -entry["time"])
    return {"drafts": found}


@command("draft.diff", role="admin",
         params={
             "id": Id(),
             "draft": Str("The draft, by its session", required=True, max_length=16),
         })
def draft_diff(ctx: Context, params: Values) -> dict:
    """What a draft would change in main, file by file and as a unified diff
    from where the draft started, with the last syntax check of each file the
    draft checked. revision is what draft.merge must quote."""
    row, draft, tip = _draft(ctx, params)
    changes = pending(draft, tip)
    base = files.draft_base(draft, tip)
    if base is None:
        base = files.git(row["id"], "hash-object", "-t", "tree", "--stdin").strip()
    shown = [change["path"] for change in changes if not change["directory"]]
    text = ""
    if shown:
        text = files.git(row["id"], "diff", "--no-color", "--no-ext-diff", "--no-textconv",
                         "--no-renames", base, tip, "--", *shown) or ""
    author, time = _author(row["id"], tip)
    return {"draft": draft.session, "revision": tip, "author": author, "time": time,
            "changes": changes, "text": text[:files.MAX_DIFF], "cut": len(text) > files.MAX_DIFF,
            "checks": _checks(ctx, row, draft, tip)}


def _tidy(ours: dict[str, str], removed: list[str]):
    """What takes out of main the directories its deletions leave empty,
    unless the draft keeps them."""

    def tidy(tree: Path) -> None:
        for path in removed:
            parent = PurePosixPath(path).parent
            while str(parent) != ".":
                target = tree / parent
                if _under(ours, str(parent)) or not target.is_dir() or target.is_symlink() \
                        or any(os.scandir(target)):
                    break
                target.rmdir()
                parent = parent.parent

    return tidy


@command("draft.merge", role="admin", mutating=True, agent="code", tool=False,
         params={
             "id": Id(),
             "draft": Str("The draft, by its session", required=True, max_length=16),
             "revision": Str("The last commit of the draft, as draft.diff gave it: what was"
                             " reviewed", required=True, max_length=64),
             "paths": Strs("The files and directories of the draft to take; all if empty",
                           max_items=MAX_PATHS),
             "message": Str(default="", max_length=200),
         })
def draft_merge(ctx: Context, params: Values) -> dict:
    """Take a draft, or some of its files, into main as one commit by the
    caller, checked as any change of main is; the draft is removed once main
    has all of it. Refused if the draft changed since it was reviewed, or if
    main changed one of the files too."""
    row, draft, tip = _draft(ctx, params)
    if params["revision"] != tip:
        raise Conflict("the draft changed since it was reviewed: look at it again")
    container_id = row["id"]
    # What main is at when the draft is compared with it: change() refuses
    # to go on if main moved meanwhile, a deletion having no digest to quote.
    main = files.head(container_id) or ""
    changes = pending(draft, tip)
    if params["paths"]:
        asked = {str(check_file_path(path, "paths")) for path in params["paths"]}
        unknown = asked - {change["path"] for change in changes}
        if unknown:
            raise BadRequest(f"not a change of the draft: {', '.join(sorted(unknown))[:500]}")
        changes = [change for change in changes if change["path"] in asked]
    if not changes:
        raise Conflict("main already has everything in this draft")
    conflicts = [change["path"] for change in changes if change["conflict"]]
    if conflicts:
        raise Conflict("changed in main too since the draft started: "
                       + ", ".join(conflicts)[:500])
    ours = _entries(container_id, tip)
    theirs = _entries(container_id, main or None)
    operations, removed, size = [], [], 0
    for change in changes:
        path = check_file_path(change["path"])
        if change["directory"]:
            operations.append({"op": "mkdir", "path": path})
        elif change["status"] == "deleted":
            if change["path"] in theirs:
                operations.append({"op": "delete", "path": path})
                removed.append(change["path"])
        else:
            current = theirs.get(change["path"])
            data = files.blob(container_id, ours[change["path"]])
            size += len(data)
            if size > files.MAX_BUNDLE:
                raise Conflict(f"more than {files.MAX_BUNDLE // 1024 // 1024} MB to take at"
                               " once: take the draft in parts")
            operations.append({"op": "write", "path": path, "data": data,
                               "expected": files.digest(files.blob(container_id, current))
                               if current else ""})
    message = params["message"].strip()
    message = store.check_name(message, "message") if message else (
        f"Take {len(changes)} change{'s' if len(changes) != 1 else ''} from a draft"
        f" of {_author(container_id, tip)[0]}")
    done = files.change(ctx, row, operations, message[:200], main,
                        tidy=_tidy(ours, removed) if removed else None)
    left = pending(draft, tip)
    if not left:
        with files.locked(container_id):
            if files.draft_tip(draft) == tip:
                files.drop(draft)
    return {**done, "left": len(left), "discarded": not left}


@command("draft.discard", role="admin", mutating=True, agent="code", tool=False,
         params={
             "id": Id(),
             "draft": Str("The draft, by its session", required=True, max_length=16),
         })
def draft_discard(ctx: Context, params: Values) -> dict:
    """Throw a draft away, with whatever main does not have of it."""
    row, draft, _tip = _draft(ctx, params)
    with files.locked(row["id"]):
        files.drop(draft)
    return {"draft": draft.session}

