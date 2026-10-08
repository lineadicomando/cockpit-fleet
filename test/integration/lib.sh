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
