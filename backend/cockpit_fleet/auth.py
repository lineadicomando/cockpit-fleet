"""Who is calling, and what their role allows.

fleetctl runs as the service account through sudo, which sets SUDO_UID to the
real caller and does not let the caller override it. Roles come from the
caller's Unix groups; the highest one wins.
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


@dataclass(frozen=True)
class Caller:
    uid: int
    user: str
    role: str | None


def role_at_least(role: str | None, minimum: str) -> bool:
    return role is not None and ROLES.index(role) >= ROLES.index(minimum)


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
    return Caller(uid, entry.pw_name, role_from_groups(group_names(entry.pw_name, entry.pw_gid)))
