"""The profiles of the assistant: what an agent may call, by class of command.

A profile only restricts: it applies under the role of the person the agent
acts for and under [mcp] max_role, and the administrator of the controller
enables the ones a user may pick ([chat] profiles). fleet-mcp adds the one of
its session to every call, beside "via"; an agent that leaves it out gets
[chat] default_profile, and one that names another gains nothing it could not
already do by hand.

The classes are those of cli.AGENT_CLASSES, and one more: adhoc-diagnostic,
an ad-hoc command of a module in [mcp] adhoc_modules, without become. It is
not a class of a command but of a call, decided by its parameters.
"""

DIAGNOSTIC = "adhoc-diagnostic"

PROFILES: dict[str, tuple[str, ...]] = {
    "observer": ("read",),
    "assisted": ("read", "run"),
    "diagnostic": ("read", "run", DIAGNOSTIC),
    "autonomous": ("read", "run"),
    "administrator": ("read", "run", "adhoc", "config", "code"),
}
# Profiles in which nobody confirms the calls: autonomy.py holds them.
AUTONOMOUS = ("autonomous",)
# Profiles beyond assisted, whose risks a user accepts once before a session
# in them (chat.accept).
RISKY = ("diagnostic", "autonomous", "administrator")

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


def is_diagnostic(params: dict, modules: tuple[str, ...]) -> bool:
    """Whether the parameters of run.adhoc make it a diagnostic command."""
    return params.get("module") in modules and params.get("become", False) is False


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
ALWAYS_ASKED = ("adhoc", "config", "code")


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
