#!/bin/sh
# The version of a build, from git: at a release tag vX.Y.Z it is X.Y.Z,
# between two releases it says how far from the last one the tree is.
#
#   version.sh describe   what git describe says ("v0.1.0-3-gabc1234-dirty")
#   version.sh plain      "0.1.0-3-gabc1234-dirty", what fleet shows
#   version.sh deb        "0.1.0+3.gabc1234.dirty", for dpkg
#   version.sh rpm        "0.1.0^3.gabc1234.dirty", for rpm
#
# Both package forms sort above X.Y.Z and below the next release. With no
# tag (a shallow clone) the count is missing ("0.1.0+gabc1234"); with no git
# at all the version is the one in backend/cockpit_fleet/__init__.py.
# FLEET_DESCRIBE, when set, stands for git describe: the package builds pass
# it into their containers, which have no git.
set -eu

cd "$(dirname "$0")/.."
release=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' backend/cockpit_fleet/__init__.py)

describe() {
    if [ -n "${FLEET_DESCRIBE:-}" ]; then
        printf '%s\n' "$FLEET_DESCRIBE"
        return
    fi
    # Only this repository's tags: not those of one it was unpacked in.
    top=$(git rev-parse --show-toplevel 2>/dev/null || true)
    if [ -z "$top" ] || [ "$(cd "$top" && pwd -P)" != "$(pwd -P)" ]; then
        printf 'v%s\n' "$release"
        return
    fi
    git describe --tags --match 'v[0-9]*' --dirty 2>/dev/null && return
    dirty=
    [ -z "$(git status --porcelain --untracked-files=no)" ] || dirty=-dirty
    printf 'v%s-g%s%s\n' "$release" "$(git rev-parse --short HEAD)" "$dirty"
}

form=${1:-plain}
raw=$(describe)
version=${raw#v}
case "$form" in
    describe) printf '%s\n' "$raw"; exit 0 ;;
    plain) printf '%s\n' "$version"; exit 0 ;;
    deb) mark=+ ;;
    rpm) mark=^ ;;
    *) echo "usage: $0 describe|plain|deb|rpm" >&2; exit 2 ;;
esac

base=${version%%-*}
case "$base" in
    *[!0-9.]* | "" | .* | *.) echo "version.sh: \"$raw\" does not begin with vX.Y.Z" >&2; exit 1 ;;
esac
rest=
[ "$version" = "$base" ] || rest=$(printf '%s' "${version#*-}" | tr - .)
if [ -n "$rest" ]; then
    printf '%s%s%s\n' "$base" "$mark" "$rest"
else
    printf '%s\n' "$base"
fi
