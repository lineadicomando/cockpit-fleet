"""The chat page: which agents it may start, and the start of one.

An agent is a program the administrator of the machine installed and named
in config.toml. It runs as the user who opens the page, started by
fleet-chat, and reaches the backend only through fleet-mcp, so it gets the
role of that user under the ceiling set for agents. Nothing here runs it:
these commands say what may be started and count what is.
"""

import os
from pathlib import Path

from .cli import Bool, Context, Str, Values, command
from .errors import Conflict, NotFound, Unavailable
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


@command("chat.agents", role="viewer", tool=False)
def chat_agents(ctx: Context, params: Values) -> dict:
    """The agents the chat page may start, and whether it may start one now."""
    config = ctx.config
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
        "profiles": list(config.chat_profiles),
        "default_profile": config.chat_default_profile,
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
    terminal the one for a terminal; and the profile of the session, which
    must be among those enabled. Refused when the sessions open are as many
    as allowed."""
    problem = _needs_agents(ctx)
    if problem:
        raise Unavailable(problem)
    agent = next((agent for agent in ctx.config.chat_agents if agent.name == params["agent"]),
                 None)
    if agent is None:
        raise NotFound(f"no such agent: {params['agent']}")
    profile = params["profile"] or ctx.config.chat_default_profile
    if profile not in ctx.config.chat_profiles:
        raise Conflict(f"the profile {profile} is not enabled on this controller"
                       " ([chat] profiles in config.toml)")
    if params["terminal"] and not agent.terminal:
        raise Conflict(f"the agent {agent.name} has no program for a terminal"
                       " (terminal in [chat.agents] of config.toml)")
    # The fleet-chat that is asking is one of them already.
    limit = ctx.config.chat_max_sessions
    if open_sessions() > limit:
        raise Conflict(f"{limit} chat sessions are open already, the most this controller"
                       " allows ([chat] max_sessions in config.toml)")
    return {"agent": agent.name, "profile": profile,
            "command": list(agent.terminal if params["terminal"] else agent.command)}
