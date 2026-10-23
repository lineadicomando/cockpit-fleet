#!/usr/bin/env python3
"""Fills a template: every @NAME@ becomes the environment variable NAME.

    render.py [--xml] TEMPLATE > OUTPUT

--xml escapes the values for an XML document. A placeholder with no variable
is an error, so a missing setting never ends up as an empty password.
"""
import os
import re
import sys
from xml.sax.saxutils import escape


def main() -> None:
    args = sys.argv[1:]
    xml = bool(args) and args[0] == "--xml"
    if xml:
        args = args[1:]
    if len(args) != 1:
        sys.exit(__doc__)
    with open(args[0], encoding="utf-8") as f:
        text = f.read()

    def value(m: re.Match) -> str:
        name = m.group(1)
        if name not in os.environ:
            sys.exit(f"{args[0]}: {name} is not set")
        v = os.environ[name]
        return escape(v, {'"': "&quot;", "'": "&apos;"}) if xml else v

    sys.stdout.write(re.sub(r"@([A-Z][A-Z0-9_]*)@", value, text))


if __name__ == "__main__":
    main()
