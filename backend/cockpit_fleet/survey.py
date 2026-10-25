"""The survey of a job template: the questions asked at launch.

An administrator defines the fields; whoever launches the template answers
them, and each answer becomes a variable of the run. This is the only way an
operator can give variables, so everything about an answer is checked here.

The answer to a secret field never passes through here: it goes to
fleet-secret, which encrypts it and returns a ticket, and the ticket is what
the launch carries.
"""

import json
import math
import re
import unicodedata

from .errors import BadRequest, Conflict, FleetError, hiding

TYPES = ("text", "number", "choice", "boolean", "secret")
MAX_FIELDS = 30
MAX_CHOICES = 50
MAX_TEXT = 4096
_DEFAULT_MAX_TEXT = 1024

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# What fleet-secret calls a secret answer it has stored.
TICKET_RE = re.compile(r"a-[0-9a-f]{32}")
_KEYS = {
    "text": {"min_length", "max_length"},
    "number": {"min", "max"},
    "choice": {"choices"},
    "boolean": set(),
    "secret": set(),
}
_COMMON_KEYS = {"name", "label", "help", "type", "required", "default"}


def check_variable(value: object) -> str:
    """A variable name a survey may set.

    Names that Ansible reads itself (ansible_user, ansible_ssh_common_args and
    the like) change how a run connects: they stay with the administrator, in
    the template's own variables.
    """
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise BadRequest(f"invalid variable name: {value!r}")
    if value.lower().startswith("ansible_"):
        raise BadRequest(f"a survey cannot set a variable reserved to Ansible: {value}")
    return value


def _is_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _line(value: object, what: str, maximum: int, minimum: int = 0) -> str:
    """One line of printable text."""
    if (not isinstance(value, str) or not minimum <= len(value) <= maximum
            or any(unicodedata.category(ch)[0] == "C" for ch in value)):
        raise BadRequest(f"{what} must be {minimum} to {maximum} printable characters")
    return value


def _length(field: dict, key: str, default: int) -> int:
    value = field.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_TEXT:
        raise BadRequest(f"{field['name']}: {key} must be an integer between 0 and {MAX_TEXT}")
    return value


def _check_field(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise BadRequest("a survey field must be an object")
    name = check_variable(raw.get("name"))
    kind = raw.get("type")
    if kind not in TYPES:
        raise BadRequest(f"{name}: type must be one of: {', '.join(TYPES)}")
    unknown = set(raw) - _COMMON_KEYS - _KEYS[kind]
    if unknown:
        raise BadRequest(f"{name}: not valid for a {kind} field: {', '.join(sorted(unknown))}")
    required = raw.get("required", False)
    if not isinstance(required, bool):
        raise BadRequest(f"{name}: required must be true or false")

    field = {
        "name": name,
        "label": _line(raw.get("label", ""), f"{name}: label", 100).strip(),
        "help": _line(raw.get("help", ""), f"{name}: help", 500).strip(),
        "type": kind,
        "required": required,
    }
    if kind == "text":
        field["min_length"] = _length({**raw, "name": name}, "min_length", 0)
        field["max_length"] = _length({**raw, "name": name}, "max_length", _DEFAULT_MAX_TEXT)
        if field["min_length"] > field["max_length"]:
            raise BadRequest(f"{name}: min_length is greater than max_length")
    elif kind == "number":
        for key in ("min", "max"):
            if raw.get(key) is not None:
                if not _is_number(raw[key]):
                    raise BadRequest(f"{name}: {key} must be a number")
                field[key] = raw[key]
        if "min" in field and "max" in field and field["min"] > field["max"]:
            raise BadRequest(f"{name}: min is greater than max")
    elif kind == "choice":
        choices = raw.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_CHOICES:
            raise BadRequest(f"{name}: choices must be a list of 1 to {MAX_CHOICES} values")
        field["choices"] = [_line(choice, f"{name}: a choice", 200, 1) for choice in choices]
        if len(set(field["choices"])) != len(choices):
            raise BadRequest(f"{name}: a choice is listed twice")

    default = raw.get("default")
    if default is not None and default != "":
        if kind == "secret":
            raise hiding(BadRequest(f"{name}: a secret field has no default, it would be"
                                    " stored in clear"), default)
        field["default"] = _check_value(field, default, "default")
    return field


def check_survey(value: object) -> list[dict]:
    """The definition of a survey, normalised, or BadRequest."""
    if not isinstance(value, list) or len(value) > MAX_FIELDS:
        raise BadRequest(f"survey must be a list of at most {MAX_FIELDS} fields")
    fields = [_check_field(raw) for raw in value]
    names = [field["name"] for field in fields]
    if len(set(names)) != len(names):
        raise BadRequest("two survey fields set the same variable")
    return fields


def _check_value(field: dict, value: object, what: str = "answer") -> object:
    name, kind = field["name"], field["type"]
    if kind == "text":
        low, high = field["min_length"], field["max_length"]
        return _line(value, f"{name}: the {what}", high, low)
    if kind == "number":
        if not _is_number(value):
            raise BadRequest(f"{name}: the {what} must be a number")
        if "min" in field and value < field["min"]:
            raise BadRequest(f"{name}: the {what} must be at least {field['min']}")
        if "max" in field and value > field["max"]:
            raise BadRequest(f"{name}: the {what} must be at most {field['max']}")
        return value
    if kind == "choice":
        if value not in field["choices"]:
            raise BadRequest(f"{name}: the {what} must be one of the choices")
        return value
    if kind == "secret":
        if not isinstance(value, str) or not TICKET_RE.fullmatch(value):
            raise BadRequest(f"{name}: a secret answer is given through fleet-secret")
        return value
    if not isinstance(value, bool):
        raise BadRequest(f"{name}: the {what} must be true or false")
    return value


def stored_answers(template, answers: dict, use: str, holder: str) -> dict:
    """Answers kept to launch a template later, by a schedule or as a step of
    a workflow, checked against the template's survey.

    A secret is never stored: a template that cannot do without one cannot be
    used so, and a secret it may do without is left unanswered.
    """
    fields = json.loads(template["survey"] or "[]")
    try:
        return _stored_answers(template, fields, answers, use, holder)
    except FleetError as error:
        raise hiding(error, *_secret_answers(fields, answers)) from None


def _secret_answers(fields: list[dict], answers: dict) -> list[object]:
    """What was given for the secret fields: never to be recorded."""
    return [answers.get(field["name"]) for field in fields if field["type"] == "secret"]


def _stored_answers(template, fields: list[dict], answers: dict, use: str, holder: str) -> dict:
    for field in fields:
        if field["type"] == "secret" and field["required"]:
            raise Conflict(f"{template['name']} asks for a secret at launch"
                           f" ({field['name']}): it cannot be {use}")
        if field["type"] == "secret" and answers.get(field["name"]) not in (None, ""):
            raise BadRequest(f"{field['name']}: a secret answer cannot be stored with {holder}")
    answer(fields, answers)
    if len(json.dumps(answers)) > 64 * 1024:
        raise BadRequest("answers is too large")
    return answers


def answer(fields: list[dict], answers: dict) -> tuple[dict, dict]:
    """The variables of a run, from the answers given at launch, and the
    tickets of its secret answers, by variable.

    A field left empty takes its default; without one it sets no variable,
    unless it is required. An answer to a question that was not asked is an
    error, not something to ignore.
    """
    try:
        return _answer(fields, answers)
    except FleetError as error:
        raise hiding(error, *_secret_answers(fields, answers)) from None


def _answer(fields: list[dict], answers: dict) -> tuple[dict, dict]:
    unknown = set(answers) - {field["name"] for field in fields}
    if unknown:
        raise BadRequest(f"not a field of this template's survey: {', '.join(sorted(unknown))}")
    variables: dict = {}
    tickets: dict = {}
    for field in fields:
        value = answers.get(field["name"])
        if value is None or value == "":
            if "default" in field:
                variables[field["name"]] = field["default"]
            elif field["required"]:
                raise BadRequest(f"{field['name']}: an answer is required")
            continue
        target = tickets if field["type"] == "secret" else variables
        target[field["name"]] = _check_value(field, value)
    return variables, tickets
