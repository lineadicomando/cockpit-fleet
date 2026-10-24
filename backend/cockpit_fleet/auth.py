"""Who is calling, and what their role allows.

fleetctl runs as the service account through sudo, which sets SUDO_UID to the
real caller and does not let the caller override it. Roles come from the
caller's Unix groups; the highest one wins. A right comes from a group of its
own and adds to whatever role the caller has, or stands alone.
"""

import grp
import os
import pwd
from collections.abc import Callable, Mapping
from dataclasses import dataclass

ROLES = ("viewer", "operator", "admin")

GROUP_ROLES = {
    "cockpit-fleet-viewer": "viewer",
    "cockpit-fleet-operator": "operator",
    "cockpit-fleet-admin": "admin",
}

# Rights apart from the order of the roles: approving a run of a template that
# asks for it is not more than launching it, nor less.
GROUP_RIGHTS = {
    "cockpit-fleet-approver": "approve",
}


@dataclass(frozen=True)
class Caller:
    uid: int
    user: str
    role: str | None
    rights: frozenset[str] = frozenset()


def role_at_least(role: str | None, minimum: str) -> bool:
    return role is not None and ROLES.index(role) >= ROLES.index(minimum)


def allowed(caller: Caller, minimum: str, right: str = "") -> bool:
    """Whether the caller may use a command: by its role, or by the right the
    command also accepts."""
    return role_at_least(caller.role, minimum) or bool(right and right in caller.rights)


def _group_names(user: str, gid: int) -> list[str]:
    names = []
    for group_id in os.getgrouplist(user, gid):
        try:
            names.append(grp.getgrgid(group_id).gr_name)
        except KeyError:
            continue
    return names


def role_from_groups(groups: list[str]) -> str | None:
    roles = [GROUP_ROLES[group] for group in groups if group in GROUP_ROLES]
    return max(roles, key=ROLES.index, default=None)


def rights_from_groups(groups: list[str]) -> frozenset[str]:
    return frozenset(GROUP_RIGHTS[group] for group in groups if group in GROUP_RIGHTS)


def resolve_caller(
    env: Mapping[str, str],
    own_uid: int,
    getpwuid: Callable = pwd.getpwuid,
    group_names: Callable[[str, int], list[str]] = _group_names,
) -> Caller:
    sudo_uid = env.get("SUDO_UID")
    if sudo_uid is None:
        # Not through sudo: the service account itself, e.g. a scheduled job.
        return Caller(own_uid, "system", "admin")
    try:
        uid = int(sudo_uid)
        entry = getpwuid(uid)
    except (ValueError, KeyError):
        return Caller(-1, f"uid {sudo_uid}", None)
    if uid == 0:
        return Caller(0, entry.pw_name, "admin")
    groups = group_names(entry.pw_name, entry.pw_gid)
    return Caller(uid, entry.pw_name, role_from_groups(groups), rights_from_groups(groups))
