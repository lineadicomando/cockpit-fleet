"""The profiles of the assistant: what an agent may call, by class of command.

A profile only restricts: it applies under the role of the person the agent
acts for and under [mcp] max_role, and the administrator of the controller
enables the ones a user may pick ([chat] profiles). fleet-mcp adds the one of
its session to every call, beside "via"; an agent that leaves it out gets
[chat] default_profile, and one that names another gains nothing it could not
already do by hand.

The classes are those of cli.AGENT_CLASSES, and one more: adhoc-diagnostic,
an ad-hoc command of a module in [mcp] adhoc_modules, without become and
with no template in its arguments. It is not a class of a command but of a
call, decided by its parameters.
"""

DIAGNOSTIC = "adhoc-diagnostic"

PROFILES: dict[str, tuple[str, ...]] = {
    "observer": ("read",),
    "assisted": ("read", "run"),
    "diagnostic": ("read", "run", "scan", DIAGNOSTIC),
    "autonomous": ("read", "run"),
    "administrator": ("read", "run", "scan", "adhoc", "config", "code"),
}
# Profiles in which nobody confirms the calls: autonomy.py holds them.
AUTONOMOUS = ("autonomous",)
# Profiles beyond assisted, whose risks a user accepts once before a session
# in them (chat.accept).
RISKY = ("diagnostic", "autonomous", "administrator")
# Profiles only for an agent that acts with the role admin, the caller's
# under [mcp] max_role: for anyone else administrator would be the role's
# powers under a name that promises more.
ADMIN_ONLY = ("administrator",)


def offered(enabled: tuple[str, ...], role: str | None) -> tuple[str, ...]:
    """The profiles enabled that an agent acting with ROLE may be in."""
    return tuple(one for one in enabled if one not in ADMIN_ONLY or role == "admin")


def default(enabled: tuple[str, ...], configured: str, role: str | None) -> str:
    """The profile of an agent that names none: [chat] default_profile, or
    when ROLE may not have it, assisted, or the first it may; "" for none."""
    available = offered(enabled, role)
    if configured in available:
        return configured
    if "assisted" in available:
        return "assisted"
    return available[0] if available else ""

# The modules of an ad-hoc command that only read, when config.toml does not
# say: those of [adhoc] modules among them.
DIAGNOSTIC_MODULES = (
    "ansible.builtin.ping",
    "ansible.builtin.setup",
    "ansible.builtin.service_facts",
    "ansible.builtin.package_facts",
    "ansible.windows.win_ping",
    "ansible.windows.setup",
)


# What makes Ansible read a string as a template, which a lookup in it would
# turn into a command run on the controller.
_TEMPLATE_MARKS = ("{{", "{%", "{#")


def _templated(value: object) -> bool:
    if isinstance(value, str):
        return any(mark in value for mark in _TEMPLATE_MARKS)
    if isinstance(value, dict):
        return any(_templated(key) or _templated(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_templated(item) for item in value)
    return False


def is_diagnostic(params: dict, modules: tuple[str, ...]) -> bool:
    """Whether the parameters of run.adhoc make it a diagnostic command: a
    module that changes nothing, without become, and arguments that are no
    templates."""
    return (params.get("module") in modules and params.get("become", False) is False
            and not _templated(params.get("free_form", ""))
            and not _templated(params.get("args", {})))


def call_class(name: str, kind: str, params: dict, modules: tuple[str, ...]) -> str:
    """The class of one call of the command NAME, whose class is KIND."""
    if name == "run.adhoc" and is_diagnostic(params, modules):
        return DIAGNOSTIC
    return kind


def allows(profile: str, kind: str) -> bool:
    """Whether a profile lets an agent make a call of the class KIND."""
    classes = PROFILES[profile]
    # Whatever ad-hoc command may run, a diagnostic one may too.
    return kind in classes or (kind == DIAGNOSTIC and "adhoc" in classes)


# Classes whose calls a person is asked about every time, in any profile:
# never trusted for a conversation, never made without a question.
ALWAYS_ASKED = ("scan", "adhoc", "config", "code")


def question(profile: str, kind: str, read_only: bool) -> str:
    """How the page and the agent at a terminal ask the person about a call of
    the class KIND in PROFILE: "none" (made with no question), "ask" (asked,
    and "always" may trust the class for the conversation) or "every" (asked
    every time). A floor, not a control: what the profile does not allow is
    refused by the backend whatever the person answers."""
    if read_only or kind == "read":
        return "none"
    if kind in ALWAYS_ASKED:
        return "every"
    if profile == "diagnostic" and kind == DIAGNOSTIC:
        return "none"
    # The backend holds an autonomous agent to its limits instead.
    if profile in AUTONOMOUS and kind == "run":
        return "none"
    return "ask"
