# SPDX-License-Identifier: LGPL-2.1-or-later
# It runs on the nodes, with Python 3.6 or later: nothing newer is used.

DOCUMENTATION = r"""
module: checks
short_description: Run the checks of the cockpit-fleet catalog on a node
description:
  - Runs every check it is given in one task and returns their results in the
    result contract of cockpit-fleet, so that a node costs one task however
    many checks it has.
  - The same module exists for Windows, M(lineadicomando.fleet.win_checks),
    with the same parameters and the same results; the action of the same
    name as this module chooses between the two by the shell of the host.
  - It only reads. Thresholds are not applied here but on the controller,
    when the results are taken in.
options:
  checks:
    description:
      - The checks to run, each a mapping with C(id), the name of a C(check)
        of the catalog and its C(params).
    type: list
    elements: dict
    required: true
author:
  - Alessandro Gagliano
"""

EXAMPLES = r"""
- name: Check the disks and two services
  lineadicomando.fleet.checks:
    checks:
      - {id: k1, check: disk, params: {}}
      - {id: k2, check: services, params: {names: [ssh, cron]}}
"""

RETURN = r"""
fleet_results:
  description: One result per check, in the order given.
  type: list
  elements: dict
  returned: always
  contains:
    id: {description: The id the check was given with., type: str}
    check: {description: The name of the check., type: str}
    state: {description: 0 ok, 1 warning, 2 critical, 3 unknown., type: int}
    summary: {description: One line for a person., type: str}
    metrics: {description: "C(name), C(instance), C(value), C(unit), C(min), C(max).", type: list}
    details: {description: Anything else the check found., type: dict}
fleet_host:
  description: What the node is, and its clock when the checks ended.
  type: dict
  returned: always
"""

import base64
import calendar
import os
import platform
import shutil
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3

# Filesystems that hold no data of their own, besides those /proc/filesystems
# marks "nodev"; the root is always looked at, an overlay in a container too.
_PSEUDO = {"autofs", "fuse.gvfsd-fuse", "fuse.portal", "fusectl", "nsfs", "binfmt_misc"}


def metric(name, value, unit="", instance="", low=None, high=None):
    found = {"name": name, "value": value, "unit": unit}
    if instance:
        found["instance"] = instance
    if low is not None:
        found["min"] = low
    if high is not None:
        found["max"] = high
    return found


def _unescape(path):
    # /proc/mounts writes a space as \040, a tab as \011 and so on.
    out, index = [], 0
    while index < len(path):
        if path[index] == "\\" and path[index + 1:index + 4].isdigit():
            out.append(chr(int(path[index + 1:index + 4], 8)))
            index += 4
        else:
            out.append(path[index])
            index += 1
    return "".join(out)


def _nodev():
    found = set()
    try:
        with open("/proc/filesystems") as source:
            for line in source:
                parts = line.split()
                if len(parts) == 2 and parts[0] == "nodev":
                    found.add(parts[1])
    except OSError:
        pass
    return found


def check_disk(params):
    wanted = [str(path) for path in params.get("mounts") or []]
    excluded = set(str(kind) for kind in params.get("exclude_types") or [])
    skip = (_nodev() | _PSEUDO | excluded) - {"zfs", "btrfs"}
    mounts, seen = [], set()
    with open("/proc/self/mounts") as source:
        for line in source:
            parts = line.split()
            if len(parts) < 3:
                continue
            device, path, kind = parts[0], _unescape(parts[1]), parts[2]
            if wanted:
                if path not in wanted:
                    continue
            elif path != "/" and (kind in skip or kind in excluded):
                continue
            # The same filesystem mounted twice (bind mounts) is counted once.
            if (device, kind) in seen and path != "/" and not wanted:
                continue
            seen.add((device, kind))
            mounts.append((path, kind))
    metrics, worst, details = [], None, {}
    for path, kind in sorted(set(mounts)):
        try:
            stats = os.statvfs(path)
        except OSError as error:
            details[path] = str(error)
            continue
        size = stats.f_blocks * stats.f_frsize
        available = stats.f_bavail * stats.f_frsize
        used = size - stats.f_bfree * stats.f_frsize
        if size == 0:
            continue
        # As df counts it: what root keeps for itself is neither used nor free.
        percent = round(100.0 * used / max(used + available, 1), 1)
        metrics.append(metric("used_pct", percent, "%", path, 0, 100))
        metrics.append(metric("free", available, "B", path, 0, size))
        if stats.f_files:
            inodes = round(100.0 * (stats.f_files - stats.f_ffree) / stats.f_files, 1)
            metrics.append(metric("inodes_used_pct", inodes, "%", path, 0, 100))
        details[path] = kind
        if worst is None or percent > worst[1]:
            worst = (path, percent)
    missing = [path for path in wanted if path not in details]
    if missing:
        return UNKNOWN, "not mounted: " + ", ".join(missing), metrics, details
    if worst is None:
        return UNKNOWN, "no filesystem found", metrics, details
    return OK, f"{len(details)} filesystems, fullest {worst[0]} at {worst[1]}%", metrics, details


def _cpu_times():
    with open("/proc/stat") as source:
        fields = [int(value) for value in source.readline().split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields[:8]), idle


def check_cpu(params):
    total, idle = _cpu_times()
    time.sleep(1)
    total2, idle2 = _cpu_times()
    busy = round(100.0 * (1 - (idle2 - idle) / max(total2 - total, 1)), 1)
    count = os.cpu_count() or 1
    metrics = [metric("cpu_pct", busy, "%", "", 0, 100)]
    load = os.getloadavg()
    for minutes, value in zip((1, 5, 15), load):
        metrics.append(metric(f"load{minutes}", round(value, 2)))
    return OK, f"{busy}% busy, load {load[0]:.2f} on {count} CPUs", metrics, {"cpus": count}


def check_memory(params):
    info = {}
    with open("/proc/meminfo") as source:
        for line in source:
            name, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                info[name] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    used = round(100.0 * (total - available) / max(total, 1), 1)
    metrics = [metric("memory_used_pct", used, "%", "", 0, 100),
               metric("memory_available", available, "B", "", 0, total)]
    swap = info.get("SwapTotal", 0)
    summary = f"{used}% of {total // 1048576} MiB used"
    if swap:
        swap_used = round(100.0 * (swap - info.get("SwapFree", 0)) / swap, 1)
        metrics.append(metric("swap_used_pct", swap_used, "%", "", 0, 100))
        summary += f", swap {swap_used}%"
    return OK, summary, metrics, {"total": total, "swap_total": swap}


def check_services(params):
    names = [str(name) for name in params.get("names") or []]
    if not names:
        return UNKNOWN, "no service named", [], {}
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return UNKNOWN, "systemctl not found", [], {}
    output = subprocess.run(
        [systemctl, "show", "--property=Id,LoadState,ActiveState,SubState", "--"] + names,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, check=False).stdout
    states, current = [], {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                states.append(current)
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    details, down = {}, []
    for name, state in zip(names, states):
        if state.get("LoadState") == "not-found":
            details[name] = "not found"
        else:
            details[name] = f"{state.get('ActiveState', '?')} ({state.get('SubState', '?')})"
        if state.get("ActiveState") != "active":
            down.append(name)
    summary = "not running: " + ", ".join(down) if down else f"{len(names)} running"
    return OK, summary, [metric("not_running", len(down), "", "", 0, len(names))], details


def _newest_kernel():
    try:
        entries = [entry for entry in os.scandir("/lib/modules") if entry.is_dir()]
    except OSError:
        return None
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.stat().st_mtime).name


def check_uptime(params):
    with open("/proc/uptime") as source:
        seconds = int(float(source.read().split()[0]))
    required, how = 0, ""
    if os.path.exists("/run/reboot-required") or os.path.exists("/var/run/reboot-required"):
        required, how = 1, "reboot-required"
    elif shutil.which("needs-restarting"):
        result = subprocess.run([shutil.which("needs-restarting"), "-r"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False)
        required, how = (1 if result.returncode == 1 else 0), "needs-restarting"
    else:
        newest = _newest_kernel()
        if newest and newest != platform.release():
            required, how = 1, f"kernel {newest} installed, {platform.release()} running"
        else:
            how = "kernel"
    days = seconds // 86400
    summary = f"up {days} days" + (", reboot required" if required else "")
    return OK, summary, [metric("uptime", seconds, "s"), metric("reboot_required", required,
                                                                "", "", 0, 1)], {"method": how}


# --- certificates: a minimal reader of DER, enough to find notAfter ---------------

def _der(data, offset):
    """The tag, the start of the content and its end, of one DER element."""
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    return tag, offset, offset + length


def not_after(der):
    """When a certificate in DER expires, in seconds since the epoch."""
    _, start, _ = _der(der, 0)             # Certificate
    _, start, _ = _der(der, start)         # TBSCertificate
    position = start
    if der[position] == 0xA0:              # [0] version
        position = _der(der, position)[2]
    for _ in range(3):                     # serial, signature, issuer
        position = _der(der, position)[2]
    _, validity, _ = _der(der, position)
    after = _der(der, validity)[2]
    tag, begin, finish = _der(der, after)
    return calendar.timegm(time.strptime(_ascii_time(tag, der[begin:finish]), "%Y%m%d%H%M%S"))


def _ascii_time(tag, raw):
    text = raw.decode("ascii").rstrip("Z")
    if tag == 0x17:
        text = ("19" if int(text[:2]) >= 50 else "20") + text
    return text[:14]


def _pem_blocks(data):
    blocks, inside, lines = [], False, []
    for line in data.decode("ascii", "replace").splitlines():
        line = line.strip()
        if line == "-----BEGIN CERTIFICATE-----":
            inside, lines = True, []
        elif line == "-----END CERTIFICATE-----" and inside:
            blocks.append(base64.b64decode("".join(lines)))
            inside = False
        elif inside:
            lines.append(line)
    return blocks


_CERT_SUFFIXES = (".pem", ".crt", ".cer")


def check_certificates(params):
    paths = [str(path) for path in params.get("paths") or []]
    if not paths:
        return UNKNOWN, "no path named", [], {}
    files = []
    for path in paths:
        if os.path.isdir(path):
            files += sorted(os.path.join(path, name) for name in os.listdir(path)
                            if name.endswith(_CERT_SUFFIXES))
        else:
            files.append(path)
    now = time.time()
    metrics, details, errors, soonest = [], {}, [], None
    for path in files:
        try:
            with open(path, "rb") as source:
                data = source.read(1024 * 1024)
            blocks = _pem_blocks(data) or [data]
            # A chain: the first certificate is the one served.
            expires = not_after(blocks[0])
        except (OSError, ValueError, IndexError) as error:
            errors.append(path)
            details[path] = f"unreadable: {error}"
            continue
        days = round((expires - now) / 86400, 1)
        metrics.append(metric("days_left", days, "d", path))
        details[path] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(expires))
        if soonest is None or days < soonest[1]:
            soonest = (path, days)
    if errors:
        return UNKNOWN, "cannot read: " + ", ".join(errors), metrics, details
    if soonest is None:
        return UNKNOWN, "no certificate found", metrics, details
    return OK, (f"{len(metrics)} certificates, the first to expire {soonest[0]} in"
                f" {soonest[1]} days"), metrics, details


def check_reachability(params):
    return OK, "reachable", [], {}


def check_clock(params):
    # The offset is computed on the controller, from the time below.
    return OK, "", [], {}


CHECKS = {
    "reachability": check_reachability,
    "disk": check_disk,
    "cpu": check_cpu,
    "memory": check_memory,
    "services": check_services,
    "uptime": check_uptime,
    "certificates": check_certificates,
    "clock": check_clock,
}


def host_info():
    release = {}
    try:
        with open("/etc/os-release") as source:
            for line in source:
                key, _, value = line.strip().partition("=")
                release[key] = value.strip('"')
    except OSError:
        pass
    return {"platform": "linux" if platform.system() == "Linux" else platform.system().lower(),
            "os": release.get("PRETTY_NAME", platform.system()),
            "os_id": release.get("ID", ""), "os_version": release.get("VERSION_ID", ""),
            "kernel": platform.release(), "arch": platform.machine()}


def main():
    module = AnsibleModule(argument_spec={"checks": {"type": "list", "elements": "dict",
                                                     "required": True}},
                           supports_check_mode=True)
    results = []
    for entry in module.params["checks"]:
        name = str(entry.get("check", ""))
        params = entry.get("params") or {}
        found = {"id": str(entry.get("id", "")), "check": name}
        implementation = CHECKS.get(name)
        try:
            if implementation is None:
                raise ValueError("no such check on this platform")
            state, summary, metrics, details = implementation(params)
        except Exception as error:  # noqa: BLE001 - one check must not stop the others
            state, summary = UNKNOWN, f"{type(error).__name__}: {error}"
            metrics, details = [], {}
        found.update(state=state, summary=summary, metrics=metrics, details=details)
        results.append(found)
    info = host_info()
    info["time"] = time.time()
    module.exit_json(changed=False, fleet_results=results, fleet_host=info)


if __name__ == "__main__":
    main()
