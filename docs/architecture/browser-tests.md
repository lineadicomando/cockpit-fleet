# Tests in a browser

A scenario (`test/browser/scenarios/NAME.mjs`) is a sequence of steps in
the interface, each with what its page must show (`decisions.md`, "Tests
in a browser"). It is quick and stops at the first step that fails, with
`failure.png`.

```
make browser-up SETUP=checks                 # the controller in a container, data of setup/checks.sh
make browser-test SCENARIO=checks-cycles     # into it-results/browser/NAME/
make browser-down
BROWSER_LAB=deb13 test/browser/browser.sh setup|test NAME   # on a VM of the lab instead
```

| Scenario | Setup | Where | Shows |
|---|---|---|---|
| `checks-cycles` | `checks` | container | overview, checks, "Check now", a cycle's page, recent cycles, cycles among runs |
| `network-scan` | `network` | `deb13`, `win11` up | a part of the lab's subnet scanned, MAC written into an inventory, its hosts probed |
| `labs-launch` | `labs` | `deb13`, `win11` up | one template on two labs, Windows Update to its end, Wake-on-LAN, a schedule, the workflow |

`labs-launch` runs Windows Update on `win11` for real (about 1 h 15 min):
take a snapshot of it first and go back to it before running it again. Its
setup takes the project ansible-win_edulab from GitHub and needs the
secret `it-win-key` on the controller, as `windows.sh` does. A workflow
that names an inventory starts at once when launched from its list: the
setup's has none, so the page asks.

## The controller

`test/browser/browser.sh` does the work. In a container: the image of
`ct-test` with Cockpit's shell (`cockpit-system`), up until `down`, on
`127.0.0.1:9093` (`BROWSER_PORT`); plain HTTP allowed there (through
podman's port a connection comes from no local address, which Cockpit
sends to HTTPS); the units a container cannot start masked, so that
Cockpit shows no failed service; a throwaway administrator `tester`. On a
VM (`BROWSER_LAB`, the package deployed): the setup is copied there and
run through `lab.sh root`; `tester` is made by the first setup with a
random password kept in `$XDG_RUNTIME_DIR/cockpit-fleet-browser/`, never
on a command line; the browser goes through `lab.sh tunnel`, opened for
the time it takes, on `127.0.0.1:9091` (`BROWSER_PORT`).

`browser.sh with COMMAND...` runs another tool against the same
controller: `BROWSER_URL`, `BROWSER_USER` and `BROWSER_PASSWORD` in its
environment, the tunnel open while it runs.

`setup/NAME.sh` runs as root on the controller, wipes, and makes what an
account would through `fleetctl` as `tester`. `backfill.py` (setup
`checks`) writes two weeks of past into the database, as the service's
account: values of every series of metrics, problems that began and ended,
runs of each template with their output deleted. It is the only thing of
the tests that writes the database behind `fleetctl`, so it checks the
columns it writes and fails on a schema it does not know.

## The browser

A container of its own (`browser.Containerfile`: Playwright's image pinned
by digest), headless, on the host's network; the password through its
environment. Labels are written in English and translated through
`po/LANG.po` (`s.t()`), so a scenario holds for every language; the cookie
`CockpitLang` sets Cockpit's. PatternFly's tables have the role `grid`,
their cells `gridcell`. A test waits for what lasts: a cycle is over in
seconds, so "Checking" may never be seen, while the notice of its start
stays. `s.refresh()` reloads the plugin's frame, for what a page does not
poll for; `BROWSER_STEPS=4-6` runs some steps only, each of which must then
start from a page of its own.
