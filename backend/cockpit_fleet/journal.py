"""Structured records to the systemd journal, over its native socket.

Best effort: a machine without the socket (a test run) just gets nothing.
"""

import socket

SOCKET = "/run/systemd/journal/socket"


def _field(name: str, value: str) -> bytes:
    data = value.encode()
    if b"\n" in data:
        return name.encode() + b"\n" + len(data).to_bytes(8, "little") + data + b"\n"
    return name.encode() + b"=" + data + b"\n"


def encode(message: str, priority: int, fields: dict[str, str]) -> bytes:
    record = {"MESSAGE": message, "PRIORITY": str(priority), "SYSLOG_IDENTIFIER": "cockpit-fleet"}
    record.update(fields)
    return b"".join(_field(name, str(value)) for name, value in record.items())


def send(message: str, priority: int = 6, **fields: str) -> None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(encode(message, priority, fields), SOCKET)
    except OSError:
        pass
