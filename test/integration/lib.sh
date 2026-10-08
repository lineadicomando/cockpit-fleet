# Shared by the integration scripts. They run as root on a machine where the
# cockpit-fleet package is installed (the test VM), never on a development host.

FAILED=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILED=1; }

# expect_ok DESCRIPTION COMMAND...
expect_ok() {
    desc=$1; shift
    if out=$("$@" 2>&1); then pass "$desc"; else fail "$desc: $out"; fi
}

# expect_denied DESCRIPTION COMMAND...: must fail.
expect_denied() {
    desc=$1; shift
    if out=$("$@" 2>&1); then fail "$desc: unexpectedly allowed"; else pass "$desc"; fi
}

as_fleet() { sudo -u cockpit-fleet -- "$@"; }

require_root() {
    [ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 2; }
    [ -x /usr/libexec/cockpit-fleet/fleet-run ] || { echo "cockpit-fleet is not installed" >&2; exit 2; }
}

finish() {
    [ "$FAILED" -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
    exit "$FAILED"
}

# --- helpers for the tests that go through fleetctl ---------------------------

FLEETCTL=/usr/libexec/cockpit-fleet/fleetctl
SECRET=/usr/libexec/cockpit-fleet/fleet-secret
CONFIG=/etc/cockpit-fleet/config.toml
DB=/var/lib/cockpit-fleet/fleet.db

ctl() {  # ctl USER COMMAND [JSON]: what that user gets from fleetctl
    printf '%s' "${3:-}" | sudo -u "$1" sudo -n -u cockpit-fleet "$FLEETCTL" "$2" 2>&1
}
admin() { ctl itadmin "$@"; }
get() {  # get JSON-TEXT PYTHON-EXPRESSION on the parsed answer "a"
    printf '%s' "$1" | python3 -c "import json,sys; a=json.load(sys.stdin); print($2)" 2>/dev/null
}
expect_eq() {  # expect_eq DESCRIPTION GOT EXPECTED
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got: '$2', wanted: '$3')"; fi
}
db() {  # db SQL: first column of the first row, read-only
    python3 -c "import sqlite3,sys; print(sqlite3.connect('file:$DB?mode=ro', uri=True).execute(sys.argv[1]).fetchone()[0])" "$1"
}
wait_status() {  # wait_status RUN_ID SECONDS: poll the database only, never fleetctl
    n=0
    while [ "$n" -lt "$2" ]; do
        s=$(db "SELECT status FROM runs WHERE id = '$1'")
        case "$s" in queued|running) sleep 1; n=$((n + 1)) ;; *) break ;; esac
    done
    echo "$s"
}
set_secret() {  # set_secret CREDENTIAL-JSON FIELD < value
    sudo -u itadmin sudo -n "$SECRET" set "$(get "$1" "a[\"data\"][\"secret_names\"][\"$2\"]")"
}

require_wipe() {
    [ "${IT_WIPE:-}" = yes ] || { echo "set IT_WIPE=yes: this test deletes the database, runs and projects" >&2; exit 2; }
}

# Back to an empty installation: no runs, projects, credentials or test accounts.
wipe() {
    for id in $(db "SELECT group_concat(id, ' ') FROM runs WHERE status IN ('queued','running')" 2>/dev/null); do
        [ "$id" = None ] || systemctl stop "cockpit-fleet-run@$id.service" 2>/dev/null
    done
    for name in $("$SECRET" list | grep '^c-'); do "$SECRET" delete "$name"; done
    for user in itadmin itoperator; do userdel -r "$user" 2>/dev/null; done
    [ -f "$CONFIG.it-backup" ] && mv "$CONFIG.it-backup" "$CONFIG"
    rm -rf /srv/fleet-it /var/lib/cockpit-fleet/runs/* /var/lib/cockpit-fleet/projects/*
    rm -f "$DB" "$DB-wal" "$DB-shm"
    systemctl reset-failed 'cockpit-fleet-run@*' 'cockpit-fleet-finish@*' 2>/dev/null
    return 0
}

# An administrator, an operator, and /srv/fleet-it allowed as a project root.
prepare() {  # prepare [MAX_CONCURRENT]
    useradd -m -G cockpit-fleet-admin itadmin
    useradd -m -G cockpit-fleet-operator itoperator
    cp -p "$CONFIG" "$CONFIG.it-backup"
    printf '[runs]\nmax_concurrent = %s\n[projects]\nlocal_roots = ["/srv/fleet-it"]\n' "${1:-4}" > "$CONFIG"
    mkdir -p /srv/fleet-it
}

# expect_run DESCRIPTION RUN_ID SECONDS STATUS: waits for the run; when it ends
# differently, shows the end of its output, which is what explains it.
expect_run() {
    got=$(wait_status "$2" "$3")
    if [ "$got" = "$4" ]; then
        pass "$1"
    else
        fail "$1 (got: '$got', wanted: '$4')"
        sed 's/\x1b\[[0-9;]*m//g' "/var/lib/cockpit-fleet/runs/$2/stdout.log" 2>/dev/null | tail -n 15 | cut -c1-300 | sed 's/^/      | /'
        db "SELECT error FROM runs WHERE id = '$2'" | sed 's/^/      | error: /'
    fi
}
