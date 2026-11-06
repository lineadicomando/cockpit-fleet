// A chat session: the agent, started by fleet-chat as the user of the page,
// and the conversation with it. It lives outside the components, so that it
// goes on while another tab of the plugin is shown; it ends with the page.

import { AUTH_REQUIRED, AcpError, Connection, MCP_SERVER, applyUpdate, fleetTool, permissionOutcome, type Item } from "./acp.ts";

// A tool of fleet-mcp, as mcp.tools describes it.
export interface ToolInfo {
    name: string;
    description: string;
    readOnly: boolean;
    // What it lets an agent do: read, run, adhoc, adhoc-diagnostic, config or code.
    class: string;
    inputSchema: { properties?: Record<string, { description?: string }> };
}

// One layer of the instructions the agent was given, as chat.start composed them.
export interface Layer {
    layer: string;
    source: string;
    text: string;
}

// How the page treats a call: made with no question, asked about with
// "always" for its class in the conversation, or asked about every time.
export type Question = "none" | "ask" | "every";

// The classes whose calls are asked about every time: ad-hoc commands,
// changes of what is defined, files. None of them is ever trusted for a
// conversation, nor made without a question.
const ALWAYS_ASKED = ["adhoc", "config", "code"];

// The questions of a profile, a floor the backend holds again: what a
// profile does not allow is refused there whatever is answered here.
export function question(profile: string, tool: ToolInfo | null): Question {
    if (!tool) return "every";
    if (tool.readOnly || tool.class === "read") return "none";
    if (ALWAYS_ASKED.includes(tool.class)) return "every";
    if (profile === "diagnostic" && tool.class === "adhoc-diagnostic") return "none";
    // The backend holds an autonomous agent to its limits instead.
    if (profile === "autonomous" && tool.class === "run") return "none";
    return "ask";
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
}

export interface Transport {
    send(text: string): void;
    close(): void;
}

// Starts the agent in a profile; onExit says why it ended, or "" when it was closed.
export type Spawn = (agent: string, profile: string, onData: (text: string) => void, onExit: (error: string) => void) => Transport;

// The tools of an agent in a profile.
export type LoadTools = (profile: string) => Promise<ToolInfo[]>;

const IDLE: ChatState = {
    phase: "idle", agent: "", profile: "", layers: [], error: "", needsLogin: false, sessionId: "", items: [], busy: false, loading: false, ask: null, sessions: [],
};

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
    private readonly listeners = new Set<() => void>();
    private readonly spawn: Spawn;
    private readonly loadTools: LoadTools;
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

    constructor(spawn: Spawn, loadTools: LoadTools) {
        this.spawn = spawn;
        this.loadTools = loadTools;
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

    start(agent: string, profile: string): void {
        if (this.state.phase === "starting" || this.state.phase === "ready") return;
        this.state = IDLE;
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
            this.tools = new Map((await this.loadTools(this.state.profile)).map(tool => [tool.name, tool]));
        } catch {
            // A tool not known is asked about every time.
        }
    }

    // Ends the agent. The conversation stays where the agent keeps it.
    stop(): void {
        const transport = this.transport;
        if (!transport) return;
        this.ended(transport, "");
        transport.close();
    }

    private ended(transport: Transport, error: string): void {
        if (transport !== this.transport) return;
        this.transport = null;
        this.connection?.close("the agent ended");
        this.connection = null;
        this.settle(null);
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
            this.initialize().catch(ex => { this.fail(ex); this.stop(); });
        } else if (method === "_fleet/error") {
            this.set({ phase: "ended", error: String(params.message ?? "") });
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
        return {
            cwd: this.setup!.cwd,
            mcpServers: [this.setup!.mcp],
            _meta: {
                disableBuiltInTools: true,
                systemPrompt: { append: this.setup!.instructions },
                claudeCode: { options: { settingSources: [], strictMcpConfig: true } },
            },
        };
    }

    // A new conversation: the agent is told when the first message is sent.
    fresh(): void {
        if (this.state.busy || this.state.loading) return;
        this.always.clear();
        this.set({ sessionId: "", items: [] });
    }

    // Goes on with an earlier conversation, which the agent replays.
    async open(sessionId: string): Promise<void> {
        if (!this.connection || this.state.busy || this.state.loading) return;
        this.always.clear();
        this.set({ sessionId, items: [], loading: true });
        try {
            await this.connection.request("session/load", { ...this.sessionParams(), sessionId });
        } catch (ex) {
            this.fail(ex);
        }
        this.set({ loading: false });
    }

    async send(text: string): Promise<void> {
        const connection = this.connection;
        if (!connection || this.state.phase !== "ready" || this.state.busy || this.state.loading || !text.trim()) return;
        this.set({ busy: true, needsLogin: false, items: [...this.state.items, { kind: "user", text }] });
        try {
            if (!this.state.sessionId) {
                const session = await connection.request<{ sessionId: string }>("session/new", this.sessionParams());
                this.set({ sessionId: session.sessionId });
            }
            await connection.request("session/prompt", { sessionId: this.state.sessionId, prompt: [{ type: "text", text }] });
        } catch (ex) {
            this.fail(ex);
        }
        this.set({ busy: false });
        this.list();
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
        const kind = question(this.state.profile, tool);
        if (kind === "none" || (kind === "ask" && this.always.has(tool!.class))) return permissionOutcome(params.options, true);
        const call = isObject(params.toolCall) ? params.toolCall : {};
        const trusts = kind === "ask" ? tool!.class : "";
        const answer = await new Promise<Answer | null>(resolve => {
            this.waiting.push({ ask: { title: String(call.title ?? ""), tool, input: call.rawInput ?? null, always: !!trusts }, trusts, resolve });
            this.set({ ask: this.waiting[0].ask });
        });
        if (answer === null) return { outcome: { outcome: "cancelled" } };
        return permissionOutcome(params.options, answer !== "refuse");
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
