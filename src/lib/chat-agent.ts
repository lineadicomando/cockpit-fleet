// The one chat session of the page, with the agent on a Cockpit channel.

import cockpit from "cockpit";
import i18n from "../i18n.ts";
import { call, frames } from "./fleet.ts";
import { Chat, type ToolInfo, type Transport } from "./chat.ts";
import type { ChatSessionRun, ChatSessionRuns } from "./types.ts";

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
        send: text => { for (const piece of frames(text)) process.input(piece, true); },
        close: () => process.close(),
    };
}

// What the page tells the agent, in the person's place, of its runs that have ended.
export function tellEnded(ended: ChatSessionRun[]): string {
    const t = i18n.t.bind(i18n);
    const statuses: Record<string, string> = {
        successful: t("Successful"), failed: t("Failed"), canceled: t("Canceled"),
        error: t("Error"), rejected: t("Rejected"), expired: t("Expired"),
    };
    const lines = ended.map(run => `- ${run.title || run.id} (${run.id}): ${statuses[run.status] ?? run.status}`);
    return [t("chat_runs_ended", { count: ended.length }), ...lines, t("Read how they went with run_result and tell me.")].join("\n");
}

export const chat = new Chat(
    spawnAgent,
    async profile => (await call<{ tools: ToolInfo[] }>("mcp.tools", profile ? { profile } : {})).tools,
    async (session, runs) => { await call("chat.limit_runs", { session, runs }); },
    { load: session => call<ChatSessionRuns>("chat.session", { session }), tell: tellEnded },
    async (session, command, input) => { await call("chat.consent", { session, command, input }); },
);
