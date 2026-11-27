# Tests: history

Archive: what was tried, measured and found while building the tests; not
needed to work on them.

## Containers in UTC

The containers run in UTC, which the test machines did not, and that found
a bug: `systemd-analyze calendar` prints a time "(in UTC)" only when the
local zone has an offset, and the schedules read only that line, so on a
controller in UTC no schedule was ever due.

## Known failures, as they came

- Since 1 November `cmdb.sh` fails on Fedora in a container (podman's
  netavark: "invalid namespace path" when it starts its nodes); it passes
  on the other two and on machines.
- Once on 26 November `checks.sh` failed on AlmaLinux in a container on a
  Fedora 44 host: podman in the container could not build the image of
  its nodes, so no node existed and every check was unknown. It passed
  there later the same day, and on the VM `alma10`. On 27 November it
  failed so in two runs in a row, for every script with nodes (`cmdb`,
  `multihost`, `checks`, `store`), while Debian passed them.

## The lab's VMs

A controller starts from the distribution's cloud image, with cloud-init
only for the name, the time zone and the admin account; the packages come
from a script run over SSH, where its errors show. The Windows node is
installed by Windows Setup with an answer file, on a SATA disk and an
e1000e NIC, which need no driver from outside the ISO.

## What the negative tests found

Nesting deep enough to stop the JSON parser, and objects of the wrong type
in the links of a workflow or in the host keys of an import, gave an
internal error; a secret typed in clear was refused but recorded with the
call; `hostkey.add` took a pattern that trusted its key for every host;
and `SUDO_UID` was read by `int()`.
