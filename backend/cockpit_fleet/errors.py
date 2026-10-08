"""Errors that reach the caller as {"ok": false, "error": {"code", "message"}}.

The codes are part of the interface with the frontend: add, never rename.
"""


class FleetError(Exception):
    code = "internal"


class BadRequest(FleetError):
    code = "bad_request"


class Forbidden(FleetError):
    code = "forbidden"


class NotFound(FleetError):
    code = "not_found"


class Conflict(FleetError):
    """The request is valid but clashes with the current state."""

    code = "conflict"


class Unavailable(FleetError):
    """Something the command needs is missing or broken on this machine."""

    code = "unavailable"
