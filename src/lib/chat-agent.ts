// The one chat session of the page, with the agent on a Cockpit channel.

import cockpit from "cockpit";
import { call } from "./fleet.ts";
import { Chat, type ToolInfo, type Transport } from "./chat.ts";

const FLEET_CHAT = "/usr/libexec/cockpit-fleet/fleet-chat";

interface Failure {
    message?: string;
    exit_status?: number | null;
    problem?: string | null;
}

// The page names the agent and its profile and nothing else: fleet-chat gets
// its command line from the backend, which says whether the profile may be
// used. The channel closes with the page, and the agent with it.
function spawnAgent(agent: string, profile: string, onData: (text: string) => void, onExit: (error: string) => void): Transport {
    const process = cockpit.spawn([FLEET_CHAT, ...(profile ? ["--profile", profile] : []), agent], { err: "message" });
    process.stream(onData);
    process.then(
        () => onExit(""),
        (ex: Failure) => {
            // What the agent wrote last on its standard error says why.
            const said = (ex?.message ?? "").trim().split("\n").slice(-3).join("\n").slice(-400);
            onExit(said || ex?.problem || `exit status ${ex?.exit_status ?? "?"}`);
        },
    );
    return {
        send: text => { process.input(text, true); },
        close: () => process.close(),
    };
}

export const chat = new Chat(spawnAgent, async profile => (await call<{ tools: ToolInfo[] }>("mcp.tools", profile ? { profile } : {})).tools);
