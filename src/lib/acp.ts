// The Agent Client Protocol as the chat page speaks it: JSON-RPC 2.0, one
// message per line, on the standard input and output of the agent. Nothing
// here knows about Cockpit: bytes come in through feed() and go out through
// the function given to the connection.

export class AcpError extends Error {
    readonly code: number;

    constructor(code: number, message: string) {
        super(message);
        this.name = "AcpError";
        this.code = code;
    }
}

// What the agent answers to a prompt when it has no login to its model.
export const AUTH_REQUIRED = -32000;

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

export class Connection {
    private buffer = "";
    private nextId = 1;
    private readonly pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
    private readonly send: (line: string) => void;
    private readonly onNotification: (method: string, params: Json) => void;
    private readonly onRequest: (method: string, params: Json) => Promise<unknown>;

    constructor(
        send: (line: string) => void,
        onNotification: (method: string, params: Json) => void,
        onRequest: (method: string, params: Json) => Promise<unknown>,
    ) {
        this.send = send;
        this.onNotification = onNotification;
        this.onRequest = onRequest;
    }

    // Output arrives in pieces that end anywhere: a line is handled when whole.
    feed(chunk: string): void {
        this.buffer += chunk;
        const lines = this.buffer.split("\n");
        this.buffer = lines.pop() ?? "";
        for (const line of lines) {
            if (line.trim()) this.handle(line);
        }
    }

    request<T>(method: string, params: object): Promise<T> {
        const id = this.nextId++;
        return new Promise<T>((resolve, reject) => {
            this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject });
            this.write({ jsonrpc: "2.0", id, method, params });
        });
    }

    notify(method: string, params: object): void {
        this.write({ jsonrpc: "2.0", method, params });
    }

    // The agent is gone: whoever waits for an answer gets none.
    close(reason: string): void {
        for (const waiting of this.pending.values()) waiting.reject(new AcpError(0, reason));
        this.pending.clear();
    }

    private write(message: object): void {
        this.send(JSON.stringify(message) + "\n");
    }

    private handle(line: string): void {
        let message: unknown;
        try {
            message = JSON.parse(line);
        } catch {
            return;
        }
        if (!isObject(message)) return;
        const params = isObject(message.params) ? message.params : {};
        if (typeof message.method === "string") {
            const id = message.id;
            if (id === undefined || id === null) {
                this.onNotification(message.method, params);
                return;
            }
            this.onRequest(message.method, params).then(
                result => this.write({ jsonrpc: "2.0", id, result }),
                ex => this.write({
                    jsonrpc: "2.0",
                    id,
                    error: { code: ex instanceof AcpError ? ex.code : -32603, message: ex instanceof Error ? ex.message : String(ex) },
                }),
            );
            return;
        }
        const waiting = typeof message.id === "number" ? this.pending.get(message.id) : undefined;
        if (!waiting) return;
        this.pending.delete(message.id as number);
        if (isObject(message.error)) {
            const code = typeof message.error.code === "number" ? message.error.code : -32603;
            waiting.reject(new AcpError(code, String(message.error.message ?? "")));
        } else {
            waiting.resolve(message.result);
        }
    }
}

// --- the conversation ---------------------------------------------------------------

export type ToolStatus = "pending" | "in_progress" | "completed" | "failed";

export interface ToolItem {
    kind: "tool";
    id: string;
    // What the agent calls it, and the tool of ours it is, when it is one.
    title: string;
    tool: string;
    status: ToolStatus;
    input: unknown;
    output: string;
}

export interface TextItem {
    kind: "user" | "agent" | "thought" | "error";
    text: string;
}

export type Item = TextItem | ToolItem;

const MAX_OUTPUT = 20000;
// The name the page gives fleet-mcp in a session, and so the prefix of its tools.
export const MCP_SERVER = "cockpit-fleet";
const TOOL_PREFIX = `mcp__${MCP_SERVER}__`;

// The tool of fleet-mcp a call is for, or "" when it is something else.
export function fleetTool(toolCall: unknown): string {
    if (!isObject(toolCall)) return "";
    const meta = isObject(toolCall._meta) && isObject(toolCall._meta.claudeCode) ? toolCall._meta.claudeCode : {};
    for (const name of [meta.toolName, toolCall.title]) {
        if (typeof name === "string" && name.startsWith(TOOL_PREFIX)) return name.slice(TOOL_PREFIX.length);
    }
    return "";
}

function textOf(content: unknown): string {
    if (isObject(content) && content.type === "text" && typeof content.text === "string") return content.text;
    return "";
}

// What a tool answered, from the content of its call.
function outputOf(update: Json): string | null {
    if (Array.isArray(update.content)) {
        const text = update.content.map(part => (isObject(part) ? textOf(part.content) : "")).filter(Boolean).join("\n");
        if (text) return text.slice(0, MAX_OUTPUT);
    }
    if (update.rawOutput !== undefined && update.rawOutput !== null) {
        const raw = typeof update.rawOutput === "string" ? update.rawOutput : JSON.stringify(update.rawOutput, null, 2);
        return raw.slice(0, MAX_OUTPUT);
    }
    return null;
}

const STATUSES: ToolStatus[] = ["pending", "in_progress", "completed", "failed"];

function append(items: Item[], kind: TextItem["kind"], text: string): Item[] {
    if (!text) return items;
    const last = items[items.length - 1];
    if (last && last.kind === kind) return [...items.slice(0, -1), { kind, text: last.text + text }];
    return [...items, { kind, text }];
}

// One "session/update" applied to the conversation; what it does not show
// (the agent's commands, its modes, its usage) leaves it as it was.
export function applyUpdate(items: Item[], update: unknown): Item[] {
    if (!isObject(update)) return items;
    switch (update.sessionUpdate) {
    case "user_message_chunk":
        return append(items, "user", textOf(update.content));
    case "agent_message_chunk":
        return append(items, "agent", textOf(update.content));
    case "agent_thought_chunk":
        return append(items, "thought", textOf(update.content));
    case "tool_call":
    case "tool_call_update": {
        const id = String(update.toolCallId ?? "");
        const known = items.find((item): item is ToolItem => item.kind === "tool" && item.id === id);
        const status = STATUSES.find(one => one === update.status);
        const tool: ToolItem = {
            kind: "tool",
            id,
            title: typeof update.title === "string" ? update.title : known?.title ?? "",
            tool: fleetTool(update) || known?.tool || "",
            status: status ?? known?.status ?? "pending",
            input: update.rawInput ?? known?.input ?? null,
            output: outputOf(update) ?? known?.output ?? "",
        };
        return known ? items.map(item => (item === known ? tool : item)) : [...items, tool];
    }
    default:
        return items;
    }
}

// --- permissions --------------------------------------------------------------------

export interface PermissionOption {
    optionId: string;
    name: string;
    kind: string;
}

// The answer to a permission request. "Always" is kept by the page, for the
// session: the agent's own option of that kind would write a rule in the
// user's settings, so it is never the one chosen.
export function permissionOutcome(options: unknown, allow: boolean): { outcome: Json } {
    const wanted = allow ? "allow_once" : "reject_once";
    const list = Array.isArray(options) ? options.filter(isObject) : [];
    const option = list.find(one => one.kind === wanted);
    if (!option || typeof option.optionId !== "string") return { outcome: { outcome: "cancelled" } };
    return { outcome: { outcome: "selected", optionId: option.optionId } };
}
