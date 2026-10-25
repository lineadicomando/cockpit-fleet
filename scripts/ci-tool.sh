#!/bin/sh
# A tool of CI at the version the workflow pins, installed once in
# /data/tools (persistent on the runners), one directory per version. Prints
# the directory to add to PATH.
#
#   ci-tool.sh node VERSION    Node.js from nodejs.org, checked against its SHASUMS256.txt
#   ci-tool.sh ruff VERSION    ruff from PyPI, in a virtual environment
set -eu

tool=${1:-} version=${2:-}
[ -n "$tool" ] && [ -n "$version" ] || { echo "usage: $0 node|ruff VERSION" >&2; exit 2; }
root=${CI_TOOLS:-/data/tools}

case "$tool" in
    node)
        dir="$root/node/$version"
        if [ ! -x "$dir/bin/node" ]; then
            case "$(uname -m)" in
                x86_64) arch=linux-x64 ;;
                aarch64) arch=linux-arm64 ;;
                *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
            esac
            file="node-v$version-$arch.tar.xz"
            url="https://nodejs.org/dist/v$version"
            tmp=$(mktemp -d)
            trap 'rm -rf "$tmp"' EXIT
            curl -fsSL -o "$tmp/$file" "$url/$file"
            curl -fsSL -o "$tmp/SHASUMS256.txt" "$url/SHASUMS256.txt"
            (cd "$tmp" && grep " $file\$" SHASUMS256.txt | sha256sum -c - >&2)
            mkdir -p "$dir"
            tar -xJf "$tmp/$file" -C "$dir" --strip-components=1
        fi
        echo "$dir/bin"
        ;;
    ruff)
        dir="$root/ruff/$version"
        if [ ! -x "$dir/bin/ruff" ]; then
            python3 -m venv "$dir"
            "$dir/bin/pip" install --quiet "ruff==$version" >&2
        fi
        echo "$dir/bin"
        ;;
    *) echo "unknown tool: $tool" >&2; exit 2 ;;
esac
