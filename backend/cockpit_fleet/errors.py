"""Errors that reach the caller as {"ok": false, "error": {"code", "message"}}.

The codes are part of the interface with the frontend: add, never rename.
"""


class FleetError(Exception):
    code = "internal"
    # Values the call carried that its record must not: see hiding().
    secrets: tuple[str, ...] = ()


def strings_in(value: object) -> list[str]:
    """Every string inside a JSON value."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings_in(item)]
    if isinstance(value, list):
        return [text for item in value for text in strings_in(item)]
    return []


def hiding(error: FleetError, *values: object) -> FleetError:
    """ERROR, for a call that carried VALUES where a secret was expected:
    they are masked wherever the audit trail would show them."""
    error.secrets = (*error.secrets, *(text for value in values for text in strings_in(value)))
    return error


class BadRequest(FleetError):
    code = "bad_request"


class Forbidden(FleetError):
    code = "forbidden"


class NotFound(FleetError):
    code = "not_found"


class Conflict(FleetError):
    """The request is valid but clashes with the current state."""

    code = "conflict"


class NotAccepted(Conflict):
    """A profile of the assistant whose risks the caller has not accepted."""

    code = "not_accepted"


class Unavailable(FleetError):
    """Something the command needs is missing or broken on this machine."""

    code = "unavailable"
