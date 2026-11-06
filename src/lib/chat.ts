// A chat session: the agent, started by fleet-chat as the user of the page,
// and the conversation with it. It lives outside the components, so that it
// goes on while another tab of the plugin is shown; it ends with the page.

import { AUTH_REQUIRED, AcpError, Connection, MCP_SERVER, applyUpdate, fleetTool, permissionOutcome, type Item } from "./acp.ts";

// A tool of fleet-mcp, as mcp.tools describes it.
export interface ToolInfo {
    name: string;
    description: string;
    readOnly: boolean;
    inputSchema: { properties?: Record<string, { description?: string }> };
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
}

export type Answer = "once" | "always" | "refuse";

export interface ChatState {
    phase: "idle" | "starting" | "ready" | "ended";
    agent: string;
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

// Starts the agent; onExit says why it ended, or "" when it was closed.
export type Spawn = (agent: string, onData: (text: string) => void, onExit: (error: string) => void) => Transport;

const IDLE: ChatState = {
    phase: "idle", agent: "", error: "", needsLogin: false, sessionId: "", items: [], busy: false, loading: false, ask: null, sessions: [],
};

interface Waiting {
    ask: Ask;
    name: string;
    resolve: (answer: Answer | null) => void;
}

function isObject(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export class Chat {
    private state: ChatState = IDLE;
    private readonly listeners = new Set<() => void>();
    private readonly spawn: Spawn;
    private readonly loadTools: () => Promise<ToolInfo[]>;
    private transport: Transport | null = null;
    private connection: Connection | null = null;
    private tools = new Map<string, ToolInfo>();
    // What the agent is given with every session: where, and its only MCP server.
    // From fleet-chat: where the agent runs, its server, and the instructions
    // fleetctl composed for it, added to the agent's own.
    private setup: { cwd: string; mcp: unknown; instructions: string } | null = null;
    private canList = false;
    private always = new Set<string>();
    private waiting: Waiting[] = [];

    constructor(spawn: Spawn, loadTools: () => Promise<ToolInfo[]>) {
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

    start(agent: string): void {
        if (this.state.phase === "starting" || this.state.phase === "ready") return;
        this.state = IDLE;
        this.set({ phase: "starting", agent });
        this.always.clear();
        this.setup = null;
        const transport = this.spawn(agent, text => this.connection?.feed(text), error => this.ended(transport, error));
        this.transport = transport;
        this.connection = new Connection(
            line => transport.send(line),
            (method, params) => this.notified(method, params),
            (method, params) => this.requested(method, params),
        );
        this.loadTools().then(tools => { this.tools = new Map(tools.map(tool => [tool.name, tool])); }, () => undefined);
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
        if (name && !this.tools.has(name)) {
            // A template made after the chat began is a tool the page has not seen.
            try {
                this.tools = new Map((await this.loadTools()).map(tool => [tool.name, tool]));
            } catch {
                // Asked about as a tool that is not known.
            }
        }
        const tool = this.tools.get(name) ?? null;
        if (tool && (tool.readOnly || this.always.has(name))) return permissionOutcome(params.options, true);
        const call = isObject(params.toolCall) ? params.toolCall : {};
        const answer = await new Promise<Answer | null>(resolve => {
            this.waiting.push({ ask: { title: String(call.title ?? ""), tool, input: call.rawInput ?? null }, name, resolve });
            this.set({ ask: this.waiting[0].ask });
        });
        if (answer === null) return { outcome: { outcome: "cancelled" } };
        return permissionOutcome(params.options, answer !== "refuse");
    }

    // The person's answer to the call being asked about.
    answer(answer: Answer): void {
        const waiting = this.waiting.shift();
        if (!waiting) return;
        if (answer === "always" && waiting.ask.tool) this.always.add(waiting.name);
        waiting.resolve(answer);
        // The same tool, asked for again meanwhile, has its answer already.
        while (this.waiting[0] && this.waiting[0].ask.tool && this.always.has(this.waiting[0].name)) {
            this.waiting.shift()!.resolve("once");
        }
        this.set({ ask: this.waiting[0]?.ask ?? null });
    }

    private settle(answer: null): void {
        for (const waiting of this.waiting.splice(0)) waiting.resolve(answer);
        if (this.state.ask) this.set({ ask: null });
    }
}
