"""The chat page: which agents it may start, and the start of one.

An agent is a program the administrator of the machine installed and named
in config.toml. It runs as the user who opens the page, started by
fleet-chat, and reaches the backend only through fleet-mcp, so it gets the
role of that user under the ceiling set for agents. Nothing here runs it:
these commands say what may be started and count what is, and let the
person follow what an agent of theirs launched in a conversation: allow it
more runs, or cancel those not ended.
"""

import os
from pathlib import Path

from . import agents, autonomy, consent, instructions, jobs, profiles, skills, store
from .auth import ROLES
from .cli import Bool, Choice, Context, Int, Obj, Str, Values, command
from .errors import (
    BadRequest,
    Conflict,
    Forbidden,
    NotAccepted,
    NotFound,
    SessionsFull,
    Unavailable,
)
from .paths import LIBEXEC_DIR

LAUNCHER = str(LIBEXEC_DIR / "fleet-chat")
# Where the role lineadicomando.fleet.chat_agent puts the agent, and what an
# administrator of the machine runs, in a terminal, to have it there.
AGENT_DIR = "/opt/cockpit-fleet-agent/"
SETUP = ("sudo env ANSIBLE_COLLECTIONS_PATH=/usr/share/cockpit-fleet/collections"
         " ansible-playbook -i localhost, -c local lineadicomando.fleet.chat_agent")
LOGIN = AGENT_DIR + "bin/claude /login"


def _guide(ctx: Context) -> dict:
    """How far this machine is from a chat with our agent, and the one line
    an administrator pastes in a terminal to go the rest of the way: it
    names the agent only when none is named (the role refuses one named by
    hand), and enables [mcp] only when it is not."""
    config = ctx.config
    command = SETUP
    if not config.chat_agents:
        command += " -e chat_agent_configure=true"
    if not config.mcp_enabled:
        command += " -e chat_agent_enable_mcp=true"
    return {
        "installed": os.access(AGENT_DIR + "bin/claude-agent-acp", os.X_OK),
        "configured": bool(config.chat_agents),
        "mcp_enabled": config.mcp_enabled,
        "command": command,
        "login": LOGIN,
    }


def open_sessions(proc: Path = Path("/proc")) -> int:
    """How many chat sessions are open on this machine, for all users.

    A session is a fleet-chat that is running: it stays for as long as its
    agent does. Counted from the command lines, with no record to keep in
    step: a limit on memory, not a control, since whoever has a shell can
    start the agent by hand.
    """
    count = 0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            words = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        # "python3 -I fleet-chat NAME": the interpreter and its option come first.
        if LAUNCHER.encode() in words[:3]:
            count += 1
    return count


def _needs_agents(ctx: Context) -> str:
    """Why the chat cannot be used, or an empty string."""
    if not ctx.config.chat_agents:
        return "no agent is named in [chat] of config.toml"
    if not ctx.config.mcp_enabled:
        return "the chat needs agents enabled ([mcp] in config.toml)"
    return ""


def _agent_role(ctx: Context) -> str | None:
    """The role an agent of the caller acts with: theirs, under [mcp] max_role."""
    role = ctx.caller.role
    ceiling = ctx.config.mcp_max_role
    if role is not None and ROLES.index(role) > ROLES.index(ceiling):
        role = ceiling
    return role


def _offered(ctx: Context) -> tuple[str, ...]:
    """The profiles enabled that an agent of the caller may be in."""
    return profiles.offered(ctx.config.chat_profiles, _agent_role(ctx))


def _check_unattended(ctx: Context) -> None:
    if not ctx.config.chat_unattended:
        raise Conflict("acting without asking is not enabled on this controller"
                       " ([chat] unattended in config.toml)")
    if not _unattended_offered(ctx):
        raise Forbidden("acting without asking is for an agent in the profile administrator,"
                        " which is for administrators of Fleet")


def _check_offered(ctx: Context, profile: str) -> None:
    if profile not in ctx.config.chat_profiles:
        raise Conflict(f"the profile {profile} is not enabled on this controller"
                       " ([chat] profiles in config.toml)")
    if profile not in _offered(ctx):
        raise Forbidden(f"the profile {profile} of the assistant is for administrators of"
                        " Fleet, and agents act as one only with [mcp] max_role = \"admin\"")


def _unattended_offered(ctx: Context) -> bool:
    """Whether the caller may let an agent act without asking: config.toml
    allows it and one of its profiles is offered to them."""
    return ctx.config.chat_unattended and any(
        profile in _offered(ctx) for profile in profiles.UNATTENDED_PROFILES)


def _accepted(ctx: Context) -> set[str]:
    """The profiles whose risks the caller accepted, and "unattended"."""
    return {row["profile"] for row in ctx.conn.execute(
        "SELECT profile FROM profile_acceptances WHERE user = ?", (ctx.caller.user,))}


@command("chat.agents", role="viewer", tool=False)
def chat_agents(ctx: Context, params: Values) -> dict:
    """The agents the chat page may start, and whether it may start one now;
    the profiles of the assistant enabled, and which of them the caller has
    yet to accept the risks of."""
    config = ctx.config
    accepted = _accepted(ctx)
    agents = []
    for agent in config.chat_agents:
        program = agent.command[0]
        installed = os.access(program, os.X_OK)
        agents.append({
            "name": agent.name, "label": agent.label or agent.name, "login": agent.login,
            "installed": installed, "terminal": bool(agent.terminal),
            # How to install it, when it is the one the package knows how to.
            "setup": SETUP if not installed and program.startswith(AGENT_DIR) else "",
        })
    return {
        "agents": agents,
        "unavailable": _needs_agents(ctx),
        "guide": _guide(ctx),
        "max_role": config.mcp_max_role,
        "profiles": list(_offered(ctx)),
        "default_profile": profiles.default(config.chat_profiles, config.chat_default_profile,
                                            _agent_role(ctx)),
        # Those of them the caller must accept the risks of before a session.
        "to_accept": [profile for profile in _offered(ctx)
                      if profile in profiles.RISKY and profile not in accepted]
        + ([profiles.UNATTENDED] if _unattended_offered(ctx)
           and profiles.UNATTENDED not in accepted else []),
        # Whether the caller may let an agent in one of these act without asking.
        "unattended": list(profiles.UNATTENDED_PROFILES) if _unattended_offered(ctx) else [],
        # What holds an agent in the profile autonomous, to tell the user.
        "autonomous_runs": config.chat_autonomous_runs,
        "autonomous_dry_run": config.chat_autonomous_dry_run,
        "max_sessions": config.chat_max_sessions,
        "open_sessions": open_sessions(),
    }


@command("chat.start", role="viewer", mutating=True, tool=False,
         params={"agent": Str("The name of the agent, as in config.toml.", required=True,
                              max_length=32),
                 "terminal": Bool("Its program for a terminal, for fleet assistant"),
                 "profile": Str("The profile of the assistant for the session; by default"
                                " the one config.toml names.", max_length=32)})
def chat_start(ctx: Context, params: Values) -> dict:
    """The command line of an agent, for fleet-chat, which is about to run it
    as the caller: the one that speaks the protocol with the page, or with
    terminal the one for a terminal; the profile of the session, which
    must be among those enabled and, beyond assisted, accepted by the
    caller (chat.accept); the instructions the agent is given,
    as one text and as the layers it is made of, to show; and the tools
    the profile lets the agent call with no question (profiles.question),
    for its program at a terminal. Refused when the
    sessions open are as many as allowed."""
    problem = _needs_agents(ctx)
    if problem:
        raise Unavailable(problem)
    agent = next((agent for agent in ctx.config.chat_agents if agent.name == params["agent"]),
                 None)
    if agent is None:
        raise NotFound(f"no such agent: {params['agent']}")
    profile = params["profile"] or profiles.default(
        ctx.config.chat_profiles, ctx.config.chat_default_profile, _agent_role(ctx))
    if not profile:
        raise Conflict("none of the profiles of the assistant enabled on this controller is for"
                       " your role ([chat] profiles in config.toml)")
    _check_offered(ctx, profile)
    if profile in profiles.RISKY and profile not in _accepted(ctx):
        raise NotAccepted(f"the risks of the profile {profile} have not been accepted yet")
    if params["terminal"] and not agent.terminal:
        raise Conflict(f"the agent {agent.name} has no program for a terminal"
                       " (terminal in [chat.agents] of config.toml)")
    # The fleet-chat that is asking is one of them already.
    limit = ctx.config.chat_max_sessions
    if open_sessions() > limit:
        raise SessionsFull(f"{limit} chat sessions are open already, the most this controller"
                       " allows ([chat] max_sessions in config.toml)")
    found = instructions.layers(profile, "terminal" if params["terminal"] else "page",
                                skills=skills.index(ctx))
    # What the agent gets is under the ceiling for agents, as fleet-mcp has it.
    role = _agent_role(ctx)
    allowed = [tool["name"] for tool in agents.tools(ctx, role, profile)
               if tool["question"] == "none"]
    return {"agent": agent.name, "profile": profile,
            "command": list(agent.terminal if params["terminal"] else agent.command),
            "instructions": instructions.compose(found), "layers": found,
            "allowed_tools": allowed}


@command("chat.accept", role="viewer", mutating=True, tool=False,
         params={"profile": Choice((*profiles.RISKY, profiles.UNATTENDED),
                                   "A profile of the assistant beyond assisted, or unattended:"
                                   " acting without asking.")})
def chat_accept(ctx: Context, params: Values) -> dict:
    """Record that the caller was told what an agent in a profile beyond
    assisted may do, how the managed nodes can steer it and what goes to the
    provider of its model, and accepted it: once, for every later session in
    that profile. The audit trail keeps who and when. "unattended" is the
    mode in which the agent asks nothing (chat.unattended)."""
    profile = params["profile"]
    if profile == profiles.UNATTENDED:
        _check_unattended(ctx)
    else:
        _check_offered(ctx, profile)
    when = store.now()
    ctx.conn.execute("INSERT OR IGNORE INTO profile_acceptances (user, profile, accepted_at)"
                     " VALUES (?, ?, ?)", (ctx.caller.user, profile, when))
    row = ctx.conn.execute("SELECT accepted_at FROM profile_acceptances"
                           " WHERE user = ? AND profile = ?", (ctx.caller.user, profile)).fetchone()
    return {"profile": profile, "accepted_at": row["accepted_at"]}


# --- what an agent launched in a conversation ----------------------------------------

_SESSION = Str("The session of fleet-mcp of the conversation, as the page gave it",
               required=True, max_length=16)
# Runs not ended: queued, running, or waiting for a person.
_NOT_ENDED = (*jobs.ACTIVE, jobs.WAITING)


def _session(params: Values) -> str:
    session = params["session"]
    if not autonomy.SESSION_RE.fullmatch(session):
        raise BadRequest("session must be the identifier of a session of fleet-mcp")
    return session


def _not_ended(ctx: Context, session: str) -> list:
    marks = ", ".join("?" * len(_NOT_ENDED))
    return ctx.conn.execute(
        f"SELECT id, title, status FROM runs WHERE session = ? AND created_by = ?"  # noqa: S608
        f" AND status IN ({marks}) ORDER BY rowid", (session, ctx.caller.user, *_NOT_ENDED)
    ).fetchall()


# The page tells the agent of a run that has ended: it looks among the last ones.
_ENDED = 20


def _ended(ctx: Context, session: str) -> list:
    marks = ", ".join("?" * len(_NOT_ENDED))
    rows = ctx.conn.execute(
        f"SELECT id, title, status FROM runs WHERE session = ? AND created_by = ?"  # noqa: S608
        f" AND status NOT IN ({marks}) ORDER BY rowid DESC LIMIT ?",
        (session, ctx.caller.user, *_NOT_ENDED, _ENDED)).fetchall()
    return rows[::-1]


@command("chat.session", role="viewer", tool=False, params={"session": _SESSION})
def chat_session(ctx: Context, params: Values) -> dict:
    """What an agent of the caller launched in a session of fleet-mcp: how
    many launches went through, how many it may make in the profile
    autonomous (as the caller chose for it, or [chat] autonomous_runs, and
    those the caller allowed since), its runs not ended yet, and the last
    ones that have, with how they ended."""
    session = _session(params)
    jobs.reconcile(ctx)
    user = ctx.caller.user
    return {"unattended": consent.unattended_session(ctx, session, profiles.UNATTENDED_PROFILES[0]),
            "launched": autonomy.launched(ctx.conn, session, user),
            "allowed": autonomy.allowed(ctx.conn, ctx.config, session, user),
            "granted": autonomy.granted(ctx.conn, session, user),
            "not_ended": [dict(row) for row in _not_ended(ctx, session)],
            "ended": [dict(row) for row in _ended(ctx, session)]}


@command("chat.limit_runs", role="operator", mutating=True, tool=False,
         params={"session": _SESSION,
                 "runs": Int(1, 1000, "How many launches to allow", default=1)})
def chat_limit_runs(ctx: Context, params: Values) -> dict:
    """Set how many launches an agent of the caller acting without asking may
    make in a session of fleet-mcp, in place of [chat] autonomous_runs: what
    the caller chose when the conversation began. Set again, it replaces
    what was set; what was allowed since (chat.grant_runs) adds to it. A
    person's decision, never an agent's, which is refused this command."""
    session = _session(params)
    ctx.conn.execute("INSERT INTO session_limits (session, user, runs, set_at)"
                     " VALUES (?, ?, ?, ?) ON CONFLICT (session, user)"
                     " DO UPDATE SET runs = excluded.runs, set_at = excluded.set_at",
                     (session, ctx.caller.user, params["runs"], store.now()))
    return {"allowed": autonomy.allowed(ctx.conn, ctx.config, session, ctx.caller.user)}


@command("chat.grant_runs", role="operator", mutating=True, tool=False,
         params={"session": _SESSION,
                 "runs": Int(1, 1000, "How many more launches to allow", default=10)})
def chat_grant_runs(ctx: Context, params: Values) -> dict:
    """Allow an agent of the caller acting without asking more launches in a
    session of fleet-mcp than it may make (chat.limit_runs, or [chat]
    autonomous_runs): a person's decision,
    never an agent's, which is refused this command."""
    session = _session(params)
    ctx.conn.execute("INSERT INTO session_grants (session, user, runs, granted_at)"
                     " VALUES (?, ?, ?, ?)", (session, ctx.caller.user, params["runs"],
                                              store.now()))
    return {"allowed": autonomy.allowed(ctx.conn, ctx.config, session, ctx.caller.user)}


@command("chat.cancel_runs", role="operator", mutating=True, tool=False,
         params={"session": _SESSION})
def chat_cancel_runs(ctx: Context, params: Values) -> dict:
    """Cancel every run an agent of the caller launched in a session of
    fleet-mcp that has not ended: queued, running, or waiting for an
    approval. The steps of a workflow run go with it; a check cycle a
    launch made due is not the session's, and is left to end."""
    session = _session(params)
    jobs.reconcile(ctx)
    canceled = []
    for row in _not_ended(ctx, session):
        try:
            jobs.cancel(ctx, row["id"])
        except BadRequest:
            continue  # ended meanwhile
        canceled.append(row["id"])
    return {"canceled": canceled}


@command("chat.consent", role="admin", mutating=True, tool=False,
         params={"session": _SESSION,
                 "command": Choice(consent.CONSENTED, "The command the agent asked to call"),
                 "input": Obj("Its arguments, as the page showed them")})
def chat_consent(ctx: Context, params: Values) -> dict:
    """Record that the caller allowed, in the chat page, a call an agent of
    theirs asked to make in a session of fleet-mcp: made within minutes with
    the same arguments, the call does not wait to be confirmed again. A
    person's decision, never an agent's, which is refused this command."""
    session = _session(params)
    command = params["command"]
    consent.give(ctx.conn, session, ctx.caller.user, command,
                 consent.of_input(command, params["input"]))
    return {}


@command("chat.unattended", role="admin", mutating=True, tool=False,
         params={"session": _SESSION,
                 "on": Bool("Let it act without asking; false asks again")})
def chat_unattended(ctx: Context, params: Values) -> dict:
    """Let an agent of the caller in the profile administrator act without
    asking in a session of fleet-mcp, or ask again: nothing it calls is
    asked about, and its ad-hoc commands start without waiting for a
    confirmation. Only where config.toml allows it ([chat] unattended), once
    the caller accepted its risks (chat.accept). A person's decision, never
    an agent's, which is refused this command."""
    session = _session(params)
    if params["on"]:
        _check_unattended(ctx)
        if profiles.UNATTENDED not in _accepted(ctx):
            raise NotAccepted("the risks of acting without asking have not been accepted yet")
    consent.set_unattended(ctx.conn, session, ctx.caller.user, params["on"])
    return {"unattended": params["on"]}
