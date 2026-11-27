// A chat session: the agent, started by fleet-chat as the user of the page,
// and the conversation with it. It lives outside the components, so that it
// goes on while another tab of the plugin is shown; it ends with the page.

import type { ChatSessionRun, ChatSessionRuns } from "./types.ts";
import { AUTH_REQUIRED, AcpError, Connection, MCP_SERVER, NOTICE_MARK, applyUpdate, fleetTool, namedByAgent, permissionOutcome, type Item } from "./acp.ts";

// A tool of fleet-mcp, as mcp.tools describes it.
export interface ToolInfo {
    name: string;
    // The command of fleetctl it calls.
    command?: string;
    description: string;
    readOnly: boolean;
    // What it lets an agent do: read, run, scan, adhoc, adhoc-diagnostic, config or code.
    class: string;
    // How the person is asked about a call of it in the profile of the session.
    question: Question;
    inputSchema: { properties?: Record<string, { description?: string }> };
}

// One layer of the instructions the agent was given, as chat.start composed them.
export interface Layer {
    layer: string;
    source: string;
    text: string;
}

// How the page treats a call, as the backend decides it for the profile of
// the session (profiles.question): made with no question, asked about with
// "always" for its class in the conversation, or asked about every time. A
// floor the backend holds again: what a profile does not allow is refused
// there whatever is answered here.
export type Question = "none" | "ask" | "every";

// A call of a tool the page does not know is asked about every time.
export function question(tool: ToolInfo | null): Question {
    return tool && ["none", "ask", "every"].includes(tool.question) ? tool.question : "every";
}

export interface SessionInfo {
    sessionId: string;
    title: string;
    updatedAt: string;
}

// A call the agent wants to make, waiting for the person's answer.
export interface Ask {
    title: string;
    // The tool of ours it is, when it is one we know.
    tool: ToolInfo | null;
    input: unknown;
    // Whether "always" may be answered, for the class of the tool.
    always: boolean;
}

export type Answer = "once" | "always" | "refuse";

export interface ChatState {
    phase: "idle" | "starting" | "ready" | "ended";
    agent: string;
    // The profile of the assistant of the session, as the backend said it.
    profile: string;
    // The instructions the agent was given, layer by layer.
    layers: Layer[];
    // Why the agent ended, when it was not asked to.
    error: string;
    needsLogin: boolean;
    sessionId: string;
    items: Item[];
    // The agent is answering a prompt, or replaying an earlier conversation.
    busy: boolean;
    loading: boolean;
    ask: Ask | null;
    sessions: SessionInfo[];
    // A message prepared elsewhere in the interface, for the composer to take
    // and the user to edit and send; and the profile suggested with it, for a
    // session not started yet.
    draft: string;
    suggested: string;
    // The profile the session is being restarted in, keeping its conversation.
    changing: string;
    // The session of fleet-mcp of the conversation: drawn here and given to
    // it, to follow what the agent launched (chat.session), allow it more
    // runs and cancel them. A conversation opened again gets a new one.
    fleetSession: string;
    // The runs an agent in the profile autonomous may launch in each
    // conversation, as chosen when the session started; 0 for config.toml's.
    runs: number;
    // The person let the agent act without asking (chat.unattended): set for
    // the session of fleet-mcp of each conversation, until turned off or the
    // profile changes.
    unattended: boolean;
    // What the agent launched in the conversation (chat.session), and why it
    // could not be asked.
    sessionRuns: ChatSessionRuns | null;
    runsError: string;
}

export interface Transport {
    send(text: string): void;
    close(): void;
}

// Starts the agent in a profile; onExit says why it ended, or "" when it was closed.
export type Spawn = (agent: string, profile: string, onData: (text: string) => void, onExit: (error: string) => void) => Transport;

// The tools of an agent in a profile, and in a session of fleet-mcp the
// person let it act without asking in, when one is named.
export type LoadTools = (profile: string, session: string) => Promise<ToolInfo[]>;

// Lets the agent act without asking in a session of fleet-mcp, or asks again.
export type SetUnattended = (session: string, on: boolean) => Promise<void>;

// Sets the runs an agent acting without asking may launch in a session of fleet-mcp.
export type LimitRuns = (session: string, runs: number) => Promise<void>;

// Records in the backend that the person allowed a call when asked, so that
// the command does not wait to be confirmed again (chat.consent).
export type Consent = (session: string, command: string, input: unknown) => Promise<void>;

// The commands whose call by an agent waits for its person in the backend,
// unless the person allowed it here first.
export const CONSENTED = ["run.adhoc"];

// Follows what the agent launched in a session of fleet-mcp, and words the
// message that tells the agent which of its runs have ended.
export interface Watch {
    load: (session: string) => Promise<ChatSessionRuns>;
    tell: (ended: ChatSessionRun[]) => string;
}

// How often the runs of the conversation are asked for while the agent
// answers or they have not ended.
export const WATCH_MS = 5000;

const IDLE: ChatState = {
    phase: "idle", agent: "", profile: "", layers: [], error: "", needsLogin: false, sessionId: "", items: [], busy: false, loading: false, ask: null, sessions: [], draft: "", suggested: "", changing: "", fleetSession: "", runs: 0, unattended: false, sessionRuns: null, runsError: "",
};

// What fleet-mcp takes as the identifier of a session: 16 hexadecimal digits.
export function drawSession(): string {
    const bytes = crypto.getRandomValues(new Uint8Array(8));
    return Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
}

interface Waiting {
    ask: Ask;
    // The class "always" would trust, empty when it may not be answered.
    trusts: string;
    resolve: (answer: Answer | null) => void;
}

function isObject(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export class Chat {
    private state: ChatState = IDLE;
    // What is written in the composer and not sent, kept here so that it
    // outlives the page of the chat when another tab is opened. Not in the
    // state: a keystroke must not draw the conversation again.
    typed = "";
    private readonly listeners = new Set<() => void>();
    private readonly spawn: Spawn;
    private readonly loadTools: LoadTools;
    private readonly limitRuns: LimitRuns;
    private transport: Transport | null = null;
    private connection: Connection | null = null;
    private tools = new Map<string, ToolInfo>();
    // What the agent is given with every session: where, and its only MCP server.
    // From fleet-chat: where the agent runs, its server, and the instructions
    // fleetctl composed for it, added to the agent's own.
    private setup: { cwd: string; mcp: unknown; instructions: string } | null = null;
    private canList = false;
    // The classes of tools trusted for the conversation.
    private always = new Set<string>();
    private waiting: Waiting[] = [];
    // A change of profile under way: the conversation to open again in it,
    // whether the agent that ends next is one the change is waiting for, and
    // how many times its start found the sessions full.
    private change: { profile: string; sessionId: string; expected: boolean; full: number } | null = null;
    private readonly watch: Watch | null;
    private readonly consent: Consent | null;
    private readonly setUnattended: SetUnattended | null;
    // The runs of the conversation seen not ended once the agent was idle: the
    // agent is told when they end, unless the person stopped them; those
    // ended and not told yet, while the agent is answering.
    private watched = new Set<string>();
    private silenced = new Set<string>();
    private toTell: ChatSessionRun[] = [];
    private watchTimer: ReturnType<typeof setTimeout> | undefined;
    private watching = false;
    private watchAgain = false;

    constructor(spawn: Spawn, loadTools: LoadTools, limitRuns: LimitRuns = async () => undefined, watch: Watch | null = null,
                consent: Consent | null = null, setUnattended: SetUnattended | null = null) {
        this.spawn = spawn;
        this.loadTools = loadTools;
        this.limitRuns = limitRuns;
        this.watch = watch;
        this.consent = consent;
        this.setUnattended = setUnattended;
    }

    subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    };

    getState = (): ChatState => this.state;

    private set(change: Partial<ChatState>): void {
        this.state = { ...this.state, ...change };
        for (const listener of this.listeners) listener();
    }

    start(agent: string, profile: string, runs = 0): void {
        if (this.state.phase === "starting" || this.state.phase === "ready") return;
        // A draft waits for the session it was prepared for.
        this.state = { ...IDLE, draft: this.state.draft, runs };
        this.change = null;
        this.unwatch();
        this.launch(agent, profile);
    }

    private launch(agent: string, profile: string): void {
        this.set({ phase: "starting", agent, profile });
        this.always.clear();
        this.tools = new Map();
        this.setup = null;
        const transport = this.spawn(agent, profile, text => this.connection?.feed(text), error => this.ended(transport, error));
        this.transport = transport;
        this.connection = new Connection(
            line => transport.send(line),
            (method, params) => this.notified(method, params),
            (method, params) => this.requested(method, params),
        );
    }

    private async reloadTools(): Promise<void> {
        try {
            const session = this.state.unattended ? this.state.fleetSession : "";
            this.tools = new Map((await this.loadTools(this.state.profile, session)).map(tool => [tool.name, tool]));
        } catch {
            // A tool not known is asked about every time.
        }
    }

    // A message for the composer, never sent by itself: in the conversation
    // open, if there is one, or the first of the session the user starts.
    prepare(text: string, suggested = ""): void {
        this.set({ draft: text, suggested });
    }

    // The draft, taken by the composer.
    takeDraft(): string {
        const text = this.state.draft;
        if (text) this.set({ draft: "" });
        return text;
    }

    // Goes on with the conversation in another profile: the agent is ended,
    // started again by fleet-chat in that profile (chat.start says whether it
    // may be, and tells the agent its instructions), and told to open the
    // conversation again. What was trusted for it is trusted no more.
    changeProfile(profile: string, runs = this.state.runs): void {
        const transport = this.transport;
        if (!transport || this.state.phase !== "ready" || this.state.busy || this.state.loading || this.state.ask
            || this.change || !profile || profile === this.state.profile) return;
        this.change = { profile, sessionId: this.state.sessionId, expected: true, full: 0 };
        // Asked again in the next profile, as "always" is.
        this.set({ changing: profile, runs, unattended: false });
        // The next starts once this one has gone, not to be counted with it.
        transport.close();
    }

    // Ends the agent. The conversation stays where the agent keeps it.
    stop(): void {
        this.change = null;
        const transport = this.transport;
        if (!transport) {
            if (this.state.changing) this.set({ phase: this.state.items.length ? "ended" : "idle", changing: "" });
            return;
        }
        this.ended(transport, "");
        transport.close();
    }

    private ended(transport: Transport, error: string): void {
        if (transport !== this.transport) return;
        this.transport = null;
        this.connection?.close("the agent ended");
        this.connection = null;
        this.settle(null);
        const change = this.change;
        if (change?.expected) {
            change.expected = false;
            this.set({ phase: "starting", busy: false, loading: false, ask: null, sessions: [] });
            // Sessions full: the one that ended may not have gone yet.
            setTimeout(() => { if (this.change === change) this.launch(this.state.agent, change.profile); }, change.full ? 1000 : 0);
            return;
        }
        this.change = null;
        if (this.state.changing) this.set({ changing: "" });
        if (this.state.phase === "ended") return;
        this.set({ phase: error || this.state.items.length ? "ended" : "idle", error: this.state.error || error, busy: false, loading: false, ask: null });
    }

    private fail(ex: unknown): void {
        if (ex instanceof AcpError && ex.code === AUTH_REQUIRED) {
            this.set({ needsLogin: true });
            return;
        }
        // An answer that never came because the agent ended is said by ended().
        if (ex instanceof AcpError && ex.code === 0) return;
        const text = ex instanceof Error ? ex.message : String(ex);
        this.set({ items: [...this.state.items, { kind: "error", text }] });
    }

    private notified(method: string, params: Record<string, unknown>): void {
        if (method === "_fleet/ready") {
            this.setup = {
                cwd: String(params.cwd ?? "/"),
                mcp: { ...(isObject(params.mcp) ? params.mcp : {}), name: MCP_SERVER },
                instructions: typeof params.instructions === "string" ? params.instructions : "",
            };
            const layers = (Array.isArray(params.layers) ? params.layers : []).filter(isObject).map(one => ({
                layer: String(one.layer ?? ""), source: String(one.source ?? ""), text: String(one.text ?? ""),
            }));
            // The profile the backend settled on: the default one when none was named.
            this.set({ profile: typeof params.profile === "string" && params.profile ? params.profile : this.state.profile, layers });
            this.reloadTools();
            this.initialize().then(() => this.changed()).catch(ex => { this.fail(ex); this.stop(); });
        } else if (method === "_fleet/error") {
            if (this.change && params.code === "sessions_full" && this.change.full < 5) {
                this.change.full += 1;
                this.change.expected = true;
                return;
            }
            this.change = null;
            this.set({ phase: "ended", changing: "", error: String(params.message ?? "") });
        } else if (method === "_auth/status_update") {
            const status = isObject(params.authStatus) ? params.authStatus : {};
            if (status.kind === "none") this.set({ needsLogin: true });
        } else if (method === "session/update" && params.sessionId === this.state.sessionId) {
            const items = applyUpdate(this.state.items, params.update);
            if (items !== this.state.items) this.set({ items });
        }
    }

    private async initialize(): Promise<void> {
        const answer = await this.connection!.request<Record<string, unknown>>("initialize", {
            protocolVersion: 1,
            // Neither files nor a terminal: the agent works through fleet-mcp only.
            clientCapabilities: { fs: { readTextFile: false, writeTextFile: false }, terminal: false },
            clientInfo: { name: "cockpit-fleet", title: "Cockpit Fleet" },
        });
        const capabilities = isObject(answer?.agentCapabilities) ? answer.agentCapabilities : {};
        this.canList = isObject(capabilities.sessionCapabilities) && "list" in capabilities.sessionCapabilities;
        this.set({ phase: "ready" });
        await this.list();
    }

    // The end of a change of profile: the conversation opened again, and said
    // to have gone on in the new profile.
    private async changed(): Promise<void> {
        const change = this.change;
        if (!change) return;
        this.change = null;
        if (change.sessionId) await this.open(change.sessionId);
        this.set({ changing: "", items: [...this.state.items, { kind: "profile", profile: this.state.profile }] });
    }

    private async list(): Promise<void> {
        if (!this.canList || !this.connection || !this.setup) return;
        try {
            const answer = await this.connection.request<{ sessions?: unknown }>("session/list", { cwd: this.setup.cwd });
            const sessions = (Array.isArray(answer?.sessions) ? answer.sessions : []).filter(isObject).map(one => ({
                sessionId: String(one.sessionId ?? ""),
                title: typeof one.title === "string" ? one.title : "",
                updatedAt: typeof one.updatedAt === "string" ? one.updatedAt : "",
            }));
            sessions.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
            this.set({ sessions });
        } catch {
            // The list is a convenience: the chat works without it.
        }
    }

    // What every session is started with: fleet-mcp as the only server, no
    // tool of the agent's own, and none of the user's settings for it, which
    // could bring other servers or rules that allow a call without asking.
    private sessionParams(): object {
        const mcp = this.setup!.mcp as { args?: unknown };
        const args = Array.isArray(mcp.args) ? mcp.args : [];
        return {
            cwd: this.setup!.cwd,
            mcpServers: [{ ...mcp, args: [...args, "--session", this.state.fleetSession] }],
            _meta: {
                disableBuiltInTools: true,
                systemPrompt: { append: this.setup!.instructions },
                claudeCode: { options: { settingSources: [], strictMcpConfig: true } },
            },
        };
    }

    // The runs chosen for the session, set for the conversation's session of
    // fleet-mcp before the agent can launch anything in it.
    private async limit(): Promise<void> {
        if (this.state.profile === "autonomous" && this.state.runs > 0) {
            await this.limitRuns(this.state.fleetSession, this.state.runs);
        }
        if (this.state.unattended && this.setUnattended) {
            await this.setUnattended(this.state.fleetSession, true);
            await this.reloadTools();
        }
    }

    // Lets the agent act without asking, or asks again, from now on in this
    // conversation and in the next ones of the session. Off at once in the
    // page, whatever the backend answers; on only once the backend has it.
    async unattend(on: boolean): Promise<void> {
        if (!this.setUnattended || on === this.state.unattended) return;
        const session = this.state.fleetSession;
        if (!on) {
            this.set({ unattended: false });
            await this.reloadTools();
            if (session) await this.setUnattended(session, false);
            return;
        }
        if (session) await this.setUnattended(session, true);
        this.set({ unattended: true });
        await this.reloadTools();
    }

    // A new conversation: the agent is told when the first message is sent.
    fresh(): void {
        if (this.state.busy || this.state.loading) return;
        this.always.clear();
        this.unwatch();
        this.set({ sessionId: "", items: [], fleetSession: "", sessionRuns: null, runsError: "" });
    }

    // Goes on with an earlier conversation, which the agent replays.
    async open(sessionId: string): Promise<void> {
        if (!this.connection || this.state.busy || this.state.loading) return;
        this.always.clear();
        this.unwatch();
        this.set({ sessionId, items: [], loading: true, fleetSession: drawSession(), sessionRuns: null, runsError: "" });
        try {
            await this.limit();
            await this.connection.request("session/load", { ...this.sessionParams(), sessionId });
        } catch (ex) {
            this.fail(ex);
        }
        this.set({ loading: false });
        void this.refreshRuns();
    }

    async send(text: string): Promise<void> {
        if (!text.trim()) return;
        await this.prompt(text, "user");
    }

    private async prompt(text: string, kind: "user" | "notice"): Promise<void> {
        const connection = this.connection;
        if (!connection || this.state.phase !== "ready" || this.state.busy || this.state.loading) return;
        this.set({ busy: true, needsLogin: false, items: [...this.state.items, { kind, text }] });
        try {
            if (!this.state.sessionId) {
                this.set({ fleetSession: drawSession() });
                await this.limit();
                const session = await connection.request<{ sessionId: string }>("session/new", this.sessionParams());
                this.set({ sessionId: session.sessionId });
            }
            void this.refreshRuns();
            await connection.request("session/prompt", { sessionId: this.state.sessionId, prompt: [{ type: "text", text }] });
        } catch (ex) {
            this.fail(ex);
        }
        this.set({ busy: false });
        this.list();
        void this.refreshRuns();
    }

    // Asks what the agent launched in the conversation; runs it was idle
    // through that have ended are told to it, in a message of the page, once
    // it is idle again. Asked again every WATCH_MS while it answers or runs
    // have not ended, whichever tab of the plugin is open.
    async refreshRuns(): Promise<void> {
        const session = this.state.fleetSession;
        if (!this.watch || !session) return;
        if (this.watching) {
            this.watchAgain = true;
            return;
        }
        this.watching = true;
        clearTimeout(this.watchTimer);
        const idle = !this.state.busy;
        try {
            const runs = await this.watch.load(session);
            if (session === this.state.fleetSession) {
                for (const run of runs.ended) {
                    if (this.watched.delete(run.id) && !this.silenced.has(run.id)) this.toTell.push(run);
                }
                if (idle) for (const run of runs.not_ended) this.watched.add(run.id);
                this.set({ sessionRuns: runs, runsError: "" });
            }
        } catch (ex) {
            if (session === this.state.fleetSession) this.set({ runsError: ex instanceof Error ? ex.message : String(ex) });
        }
        this.watching = false;
        if (this.watchAgain) {
            this.watchAgain = false;
            return this.refreshRuns();
        }
        if (session === this.state.fleetSession && (this.state.busy || (this.state.sessionRuns?.not_ended.length ?? 0) > 0)) {
            this.watchTimer = setTimeout(() => void this.refreshRuns(), WATCH_MS);
        }
        await this.tell();
    }

    // The person stops the runs of the conversation: the agent, stopped too,
    // is not told of them.
    silenceRuns(): void {
        for (const run of this.state.sessionRuns?.not_ended ?? []) this.silenced.add(run.id);
        for (const id of this.watched) this.silenced.add(id);
        this.toTell = [];
    }

    private async tell(): Promise<void> {
        const watch = this.watch;
        const state = this.state;
        if (!watch || !this.toTell.length || state.phase !== "ready" || state.busy || state.loading || state.ask
            || this.change || !state.sessionId) return;
        const ended = this.toTell;
        this.toTell = [];
        await this.prompt(NOTICE_MARK + watch.tell(ended), "notice");
    }

    private unwatch(): void {
        clearTimeout(this.watchTimer);
        this.watched = new Set();
        this.silenced = new Set();
        this.toTell = [];
    }

    // Stops the answer being given; the calls waiting for a yes get none.
    cancel(): void {
        if (!this.connection || !this.state.busy || !this.state.sessionId) return;
        this.connection.notify("session/cancel", { sessionId: this.state.sessionId });
        this.settle(null);
    }

    private async requested(method: string, params: Record<string, unknown>): Promise<unknown> {
        if (method !== "session/request_permission") throw new AcpError(-32601, `method not found: ${method}`);
        const name = fleetTool(params.toolCall);
        // A template made after the chat began is a tool the page has not seen.
        if (name && !this.tools.has(name)) await this.reloadTools();
        const tool = this.tools.get(name) ?? null;
        let kind = question(tool);
        // Known only by its title: a call worded as one of ours is asked about,
        // since what it really is was not said.
        if (kind === "none" && !namedByAgent(params.toolCall)) kind = "ask";
        if (kind === "none" || (kind === "ask" && this.always.has(tool!.class))) return permissionOutcome(params.options, true);
        const call = isObject(params.toolCall) ? params.toolCall : {};
        const trusts = kind === "ask" ? tool!.class : "";
        const answer = await new Promise<Answer | null>(resolve => {
            this.waiting.push({ ask: { title: String(call.title ?? ""), tool, input: call.rawInput ?? null, always: !!trusts }, trusts, resolve });
            this.set({ ask: this.waiting[0].ask });
        });
        if (answer === null) return { outcome: { outcome: "cancelled" } };
        if (answer !== "refuse") await this.consented(tool, call.rawInput);
        return permissionOutcome(params.options, answer !== "refuse");
    }

    // A call the backend would hold for the person to confirm again: the
    // answer just given is that confirmation. Without it, the call waits.
    private async consented(tool: ToolInfo | null, input: unknown): Promise<void> {
        const session = this.state.fleetSession;
        if (!this.consent || !session || !tool?.command || !CONSENTED.includes(tool.command) || !isObject(input)) return;
        try {
            await this.consent(session, tool.command, input);
        } catch {
            // The run waits to be confirmed, as without the page.
        }
    }

    // The person's answer to the call being asked about.
    answer(answer: Answer): void {
        const waiting = this.waiting.shift();
        if (!waiting) return;
        if (answer === "always" && waiting.trusts) this.always.add(waiting.trusts);
        waiting.resolve(answer);
        // A tool of the same class, asked for again meanwhile, has its answer already.
        while (this.waiting[0] && this.waiting[0].trusts && this.always.has(this.waiting[0].trusts)) {
            this.waiting.shift()!.resolve("once");
        }
        this.set({ ask: this.waiting[0]?.ask ?? null });
    }

    private settle(answer: null): void {
        for (const waiting of this.waiting.splice(0)) waiting.resolve(answer);
        if (this.state.ask) this.set({ ask: null });
    }
}
