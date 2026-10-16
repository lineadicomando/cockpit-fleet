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
  - It only reads, but for the C(updates) check with C(refresh), which
    refreshes the package metadata. Thresholds are not applied here but on
    the controller, when the results are taken in.
  - The C(cmdb) check returns what the host is made of in C(cmdb), the
    C(updates) check the updates it lacks in C(updates), beside the rest of
    their result.
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
    cmdb:
      description: "For C(cmdb): C(hardware), C(disks), C(interfaces), C(packages), C(services)."
      type: dict
    updates:
      description: "For C(updates): the C(manager) and the C(updates), each with C(security)."
      type: dict
fleet_host:
  description: What the node is, and its clock when the checks ended.
  type: dict
  returned: always
"""

import base64
import calendar
import json
import os
import platform
import shutil
import subprocess
import time
import xml.etree.ElementTree as ElementTree

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


# --- the inventory of the host ---------------------------------------------------

def _run(argv, timeout=600):
    """The output of a command, in the C locale; None if it cannot run."""
    if not shutil.which(argv[0]):
        return None
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        result = subprocess.run([shutil.which(argv[0])] + argv[1:], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                universal_newlines=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result


def _read(path):
    try:
        with open(path) as source:
            return source.read().strip().replace("\x00", "")
    except (OSError, UnicodeDecodeError):
        return ""


def _hardware():
    cpu_model = ""
    try:
        with open("/proc/cpuinfo") as source:
            for line in source:
                key, _, value = line.partition(":")
                if key.strip() in ("model name", "Model", "Hardware", "cpu model") \
                        and value.strip():
                    cpu_model = value.strip()
                    break
    except OSError:
        pass
    memory = None
    try:
        with open("/proc/meminfo") as source:
            for line in source:
                if line.startswith("MemTotal:"):
                    memory = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    dmi = "/sys/class/dmi/id/"
    model = _read(dmi + "product_name") or _read("/proc/device-tree/model")
    virtual = ""
    found = _run(["systemd-detect-virt"], 30)
    if found is not None and found.returncode == 0:
        virtual = found.stdout.strip()
    with open("/proc/uptime") as source:
        boot = time.time() - float(source.read().split()[0])
    return {"vendor": _read(dmi + "sys_vendor"), "model": model,
            # Readable by root only: empty without become.
            "serial": _read(dmi + "product_serial"),
            "bios": _read(dmi + "bios_version"), "virtual": virtual, "cpu_model": cpu_model,
            "cpus": os.cpu_count(), "memory": memory, "boot_time": round(boot)}


_DISKS = ("sd", "vd", "xvd", "hd", "nvme", "mmcblk")


def _disks():
    disks = []
    try:
        names = sorted(os.listdir("/sys/block"))
    except OSError:
        return disks
    for name in names:
        if not name.startswith(_DISKS):
            continue
        try:
            size = int(_read("/sys/block/%s/size" % name) or 0) * 512
        except ValueError:
            size = 0
        disks.append({"name": name, "size": size,
                      "model": _read("/sys/block/%s/device/model" % name)})
    return disks


def _interfaces():
    addresses = {}
    found = _run(["ip", "-j", "address", "show"], 30)
    if found is not None and found.returncode == 0:
        try:
            for link in json.loads(found.stdout):
                addresses[link.get("ifname")] = [
                    "%s/%s" % (entry.get("local"), entry.get("prefixlen"))
                    for entry in link.get("addr_info", []) if entry.get("scope") != "link"]
        except (ValueError, AttributeError, TypeError):
            pass
    interfaces = []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return interfaces
    for name in names:
        if name == "lo":
            continue
        interfaces.append({"name": name, "mac": _read("/sys/class/net/%s/address" % name),
                           "addresses": addresses.get(name, [])})
    return interfaces


def _packages():
    """The installed packages, and the manager that knows them."""
    found = _run(["dpkg-query", "-W", "-f",
                  "${db:Status-Abbrev}\t${Package}\t${Version}\t${Architecture}\n"])
    if found is not None and found.returncode == 0:
        packages = []
        for line in found.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 4 and parts[0].startswith("ii"):
                packages.append({"name": parts[1], "version": parts[2], "arch": parts[3]})
        return "dpkg", packages
    found = _run(["rpm", "-qa", "--qf",
                  "%{NAME}\t%|EPOCH?{%{EPOCH}:}:{}|%{VERSION}-%{RELEASE}\t%{ARCH}\n"])
    if found is not None and found.returncode == 0:
        packages = []
        for line in found.stdout.splitlines():
            parts = line.split("\t")
            # The keys rpm trusts are listed as packages: they are not software.
            if len(parts) == 3 and parts[0] != "gpg-pubkey":
                packages.append({"name": parts[0], "version": parts[1],
                                 "arch": "" if parts[2] == "(none)" else parts[2]})
        return "rpm", packages
    found = _run(["pacman", "-Q"])
    if found is not None and found.returncode == 0:
        return "pacman", [{"name": name, "version": version, "arch": ""}
                          for name, _, version in (line.partition(" ")
                                                   for line in found.stdout.splitlines())]
    return "", None


def _services():
    if not os.path.isdir("/run/systemd/system"):
        return None
    files = _run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"])
    units = _run(["systemctl", "list-units", "--type=service", "--all", "--no-legend",
                  "--no-pager", "--plain"])
    if files is None or units is None or files.returncode != 0 or units.returncode != 0:
        return None
    services = {}
    for line in files.stdout.splitlines():
        parts = line.split()
        # A template is not a service of its own; its instances are listed below.
        if len(parts) >= 2 and "@." not in parts[0]:
            services[parts[0]] = {"name": parts[0], "state": "stopped", "enabled": parts[1]}
    states = {"active": "running", "inactive": "stopped", "failed": "failed"}
    for line in units.stdout.splitlines():
        parts = line.lstrip("● ").split()
        if len(parts) >= 3 and parts[1] != "not-found":
            entry = services.setdefault(parts[0], {"name": parts[0], "enabled": ""})
            entry["state"] = states.get(parts[2], parts[2])
    return sorted(services.values(), key=lambda entry: entry["name"])


def check_cmdb(params):
    hardware = _hardware()
    manager, packages = _packages()
    services = _services()
    data = {"hardware": hardware, "disks": _disks(), "interfaces": _interfaces(),
            "package_manager": manager, "packages": packages, "services": services}
    metrics = [metric("cpus", hardware["cpus"] or 0),
               metric("memory", hardware["memory"] or 0, "B")]
    parts = ["%s CPUs" % hardware["cpus"], "%.1f GiB" % ((hardware["memory"] or 0) / 2.0 ** 30)]
    if packages is not None:
        metrics.append(metric("packages", len(packages)))
        parts.append("%d packages" % len(packages))
    if services is not None:
        metrics.append(metric("services", len(services)))
        parts.append("%d services" % len(services))
    state = OK if packages is not None else UNKNOWN
    if packages is None:
        parts.append("no package manager known")
    return state, ", ".join(parts), metrics, {"package_manager": manager}, {"cmdb": data}


# --- updates ----------------------------------------------------------------------------

def _apt_updates(refresh):
    if refresh:
        found = _run(["apt-get", "update", "-qq"])
        if found is None or found.returncode != 0:
            raise RuntimeError("apt-get update failed: %s" % (found.stderr.strip()[-300:]
                                                             if found else "not run"))
    found = _run(["apt-get", "-s", "-q", "-o", "Debug::NoLocking=true", "dist-upgrade"])
    if found is None or found.returncode != 0:
        raise RuntimeError("apt-get -s dist-upgrade failed: %s"
                           % (found.stderr.strip()[-300:] if found else "not run"))
    updates = []
    for line in found.stdout.splitlines():
        # Inst NAME [CURRENT] (NEW ORIGINS [ARCH]); without [CURRENT] it is a new package.
        if not line.startswith("Inst ") or " [" not in line.split("(")[0]:
            continue
        name = line.split()[1]
        current = line.split("[", 1)[1].split("]", 1)[0]
        rest = line.split("(", 1)[1].rsplit(")", 1)[0] if "(" in line else ""
        words = rest.split()
        available = words[0] if words else ""
        source = " ".join(word for word in words[1:] if not word.startswith("["))
        updates.append({"name": name, "current": current, "available": available,
                        "security": "security" in source.lower(), "source": source})
    return updates


def _nevra_name(text):
    """The name of a package in name-version-release.arch."""
    base = text.rsplit(".", 1)[0]
    parts = base.rsplit("-", 2)
    return parts[0] if len(parts) == 3 else ""


def _dnf_updates(refresh):
    dnf = "dnf" if shutil.which("dnf") else "yum"
    found = _run([dnf, "-q"] + (["--refresh"] if refresh else []) + ["check-update"])
    if found is None or found.returncode not in (0, 100):
        raise RuntimeError("%s check-update failed: %s"
                           % (dnf, found.stderr.strip()[-300:] if found else "not run"))
    installed = {}
    listing = _run(["rpm", "-qa", "--qf", "%{NAME}.%{ARCH}\t%{VERSION}-%{RELEASE}\n"])
    for line in (listing.stdout.splitlines() if listing else []):
        key, _, version = line.partition("\t")
        installed[key] = version
    updates = []
    for line in found.stdout.splitlines():
        # What replaces what follows, and is not an update of its own.
        if line.lower().startswith("obsoleting"):
            break
        parts = line.split()
        # name.arch version repository; anything else is a heading or a note.
        if len(parts) != 3 or "." not in parts[0] or line.startswith(" "):
            continue
        updates.append({"name": parts[0], "current": installed.get(parts[0], ""),
                        "available": parts[1], "security": False, "source": parts[2]})
    if updates:
        security = set()
        advisories = _run([dnf, "-q", "updateinfo", "list", "--security", "--updates"])
        if advisories is None or advisories.returncode != 0:
            advisories = _run([dnf, "-q", "updateinfo", "list", "--security"])
        for line in (advisories.stdout.splitlines() if advisories else []):
            for word in line.split():
                name = _nevra_name(word)
                if name:
                    security.add(name)
        for update in updates:
            update["security"] = update["name"].rsplit(".", 1)[0] in security
    return updates


def _zypper_updates(refresh):
    if refresh:
        found = _run(["zypper", "--non-interactive", "--quiet", "refresh"])
        if found is None or found.returncode != 0:
            raise RuntimeError("zypper refresh failed")
    found = _run(["zypper", "--non-interactive", "--xmlout", "list-updates"])
    if found is None or found.returncode != 0:
        raise RuntimeError("zypper list-updates failed")
    updates = []
    # The output of zypper on this host, not data from elsewhere.
    for node in ElementTree.fromstring(found.stdout).iter("update"):  # noqa: S314
        if node.get("kind", "package") != "package":
            continue
        source = node.find("source")
        updates.append({"name": node.get("name", ""), "current": node.get("edition-old", ""),
                        "available": node.get("edition", ""), "security": False,
                        "source": source.get("alias", "") if source is not None else ""})
    patches = _run(["zypper", "--non-interactive", "--xmlout", "list-patches", "--category",
                    "security"])
    security = 0
    if patches is not None and patches.returncode == 0:
        listing = ElementTree.fromstring(patches.stdout)  # noqa: S314
        security = sum(1 for node in listing.iter("update") if node.get("kind") == "patch")
    return updates, security


def check_updates(params):
    refresh = params.get("refresh") is True
    if refresh and os.geteuid() != 0:
        return UNKNOWN, "refresh needs become", [], {}, {}
    security_patches = None
    if shutil.which("apt-get"):
        manager, updates = "apt", _apt_updates(refresh)
    elif shutil.which("dnf") or shutil.which("yum"):
        manager, updates = "dnf", _dnf_updates(refresh)
    elif shutil.which("zypper"):
        manager = "zypper"
        updates, security_patches = _zypper_updates(refresh)
    else:
        return UNKNOWN, "no package manager known", [], {}, {}
    security = sum(1 for update in updates if update["security"])
    if security_patches is not None:
        security = security_patches
    metrics = [metric("updates", len(updates)), metric("security_updates", security)]
    summary = "%d update%s, %d for security (%s)" % (len(updates), "" if len(updates) == 1 else "s",
                                                   security, manager)
    return OK, summary, metrics, {"manager": manager}, {"updates": {"manager": manager,
                                                                    "updates": updates}}


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
    "cmdb": check_cmdb,
    "updates": check_updates,
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
            state, summary, metrics, details, *extra = implementation(params)
        except Exception as error:  # noqa: BLE001 - one check must not stop the others
            state, summary = UNKNOWN, f"{type(error).__name__}: {error}"
            metrics, details, extra = [], {}, []
        found.update(state=state, summary=summary, metrics=metrics, details=details)
        # What the slow kinds collect beyond a state: cmdb or updates.
        for more in extra:
            found.update(more)
        results.append(found)
    info = host_info()
    info["time"] = time.time()
    module.exit_json(changed=False, fleet_results=results, fleet_host=info)


if __name__ == "__main__":
    main()
