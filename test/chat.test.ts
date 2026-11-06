import { test } from "node:test";
import assert from "node:assert/strict";
import { AcpError, Connection, applyUpdate, fleetTool, permissionOutcome, type Item } from "../src/lib/acp.ts";
import { Chat, question, type ToolInfo, type Transport } from "../src/lib/chat.ts";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test("a message is handled when its line is whole, wherever the pieces end", () => {
    const seen: string[] = [];
    const connection = new Connection(() => undefined, method => seen.push(method), async () => null);
    connection.feed('{"jsonrpc":"2.0","method":"a"}\n{"jsonrpc":"2.0","me');
    assert.deepEqual(seen, ["a"]);
    connection.feed('thod":"b"}\nnot json\n\n');
    assert.deepEqual(seen, ["a", "b"]);
});

test("requests are answered by id, errors with their code, and none once closed", async () => {
    const sent: { id: number; method: string }[] = [];
    const connection = new Connection(line => sent.push(JSON.parse(line)), () => undefined, async () => null);
    const first = connection.request<{ n: number }>("one", {});
    const second = connection.request("two", {});
    const third = connection.request("three", {});
    connection.feed(`{"jsonrpc":"2.0","id":${sent[1].id},"error":{"code":-32000,"message":"Authentication required"}}\n`);
    connection.feed(`{"jsonrpc":"2.0","id":${sent[0].id},"result":{"n":1}}\n`);
    assert.deepEqual(await first, { n: 1 });
    await assert.rejects(second, (ex: unknown) => ex instanceof AcpError && ex.code === -32000);
    connection.close("gone");
    await assert.rejects(third, /gone/);
});

test("a request of the agent gets its answer, or an error, with the same id", async () => {
    const sent: Record<string, unknown>[] = [];
    const connection = new Connection(line => sent.push(JSON.parse(line)), () => undefined, async method => {
        if (method === "fs/read_text_file") throw new AcpError(-32601, "method not found");
        return { fine: true };
    });
    connection.feed('{"jsonrpc":"2.0","id":7,"method":"session/request_permission","params":{}}\n');
    connection.feed('{"jsonrpc":"2.0","id":"x","method":"fs/read_text_file","params":{}}\n');
    await tick();
    assert.deepEqual(sent, [
        { jsonrpc: "2.0", id: 7, result: { fine: true } },
        { jsonrpc: "2.0", id: "x", error: { code: -32601, message: "method not found" } },
    ]);
});

test("updates become the conversation: text joins, a tool call is one item that changes", () => {
    let items: Item[] = [];
    const say = (update: object) => { items = applyUpdate(items, update); };
    say({ sessionUpdate: "user_message_chunk", content: { type: "text", text: "ping the nodes" } });
    say({ sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "hm" } });
    say({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "On " } });
    say({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "it." } });
    say({ sessionUpdate: "available_commands_update", availableCommands: [] });
    say({
        sessionUpdate: "tool_call", toolCallId: "t1", title: "run_adhoc", status: "pending", rawInput: { module: "ansible.builtin.ping" },
        _meta: { claudeCode: { toolName: "mcp__cockpit-fleet__run_adhoc" } },
    });
    say({
        sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed",
        content: [{ type: "content", content: { type: "text", text: '{"id":"r1"}' } }],
    });
    say({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Done." } });
    assert.deepEqual(items, [
        { kind: "user", text: "ping the nodes" },
        { kind: "thought", text: "hm" },
        { kind: "agent", text: "On it." },
        { kind: "tool", id: "t1", title: "run_adhoc", tool: "run_adhoc", status: "completed", input: { module: "ansible.builtin.ping" }, output: '{"id":"r1"}' },
        { kind: "agent", text: "Done." },
    ]);
});

test("only a tool of fleet-mcp is recognised as one", () => {
    assert.equal(fleetTool({ _meta: { claudeCode: { toolName: "mcp__cockpit-fleet__run_start" } } }), "run_start");
    assert.equal(fleetTool({ title: "mcp__cockpit-fleet__run_log" }), "run_log");
    assert.equal(fleetTool({ _meta: { claudeCode: { toolName: "mcp__other__run_start" } }, title: "Bash" }), "");
    assert.equal(fleetTool(null), "");
});

const OPTIONS = [
    { optionId: "allow-once", name: "Yes", kind: "allow_once" },
    { optionId: "allow-with-updates", name: "Yes, and don't ask again", kind: "allow_always" },
    { optionId: "reject", name: "No", kind: "reject_once" },
];

test("a permission is given once or refused, never as a rule the agent would keep", () => {
    assert.deepEqual(permissionOutcome(OPTIONS, true), { outcome: { outcome: "selected", optionId: "allow-once" } });
    assert.deepEqual(permissionOutcome(OPTIONS, false), { outcome: { outcome: "selected", optionId: "reject" } });
    assert.deepEqual(permissionOutcome([OPTIONS[1]], true), { outcome: { outcome: "cancelled" } });
    assert.deepEqual(permissionOutcome(undefined, false), { outcome: { outcome: "cancelled" } });
});

// --- a whole session, against an agent played by the test ------------------------------

const TOOLS: ToolInfo[] = [
    { name: "run_list", description: "The runs.", readOnly: true, class: "read", inputSchema: {} },
    { name: "run_start", description: "Launch a job template.", readOnly: false, class: "run", inputSchema: {} },
    { name: "run_cancel", description: "Cancel a run.", readOnly: false, class: "run", inputSchema: {} },
    { name: "run_adhoc", description: "Run an ad-hoc command.", readOnly: false, class: "adhoc", inputSchema: {} },
    { name: "template_update", description: "Change a template.", readOnly: false, class: "config", inputSchema: {} },
];

interface Message { id?: number; method?: string; params?: any; result?: any }

class Agent {
    readonly chat: Chat;
    readonly received: Message[] = [];
    closed = false;
    spawned: string[][] = [];
    // The profiles the tools were asked for.
    listed: string[] = [];
    private onData: (text: string) => void = () => undefined;
    private onExit: (error: string) => void = () => undefined;
    // How the agent answers each method; a function gets the message.
    answers: Record<string, unknown> = {
        initialize: { protocolVersion: 1, agentCapabilities: { loadSession: true, sessionCapabilities: { list: {} } } },
        "session/list": { sessions: [] },
        "session/new": { sessionId: "s1" },
        "session/load": {},
    };

    constructor() {
        this.chat = new Chat((agent, profile, onData, onExit): Transport => {
            this.spawned.push([agent, profile]);
            this.onData = onData;
            this.onExit = onExit;
            return {
                send: text => {
                    const message: Message = JSON.parse(text);
                    this.received.push(message);
                    if (message.method && message.id !== undefined && message.method in this.answers) {
                        const answer = this.answers[message.method];
                        this.say(typeof answer === "object" && answer !== null && "error" in answer
                            ? { jsonrpc: "2.0", id: message.id, ...answer }
                            : { jsonrpc: "2.0", id: message.id, result: answer });
                    }
                },
                close: () => { this.closed = true; },
            };
        }, async profile => { this.listed.push(profile); return TOOLS; });
    }

    say(message: object): void { this.onData(JSON.stringify(message) + "\n"); }
    exit(error: string): void { this.onExit(error); }
    methods(): (string | undefined)[] { return this.received.map(message => message.method); }
    last(method: string): Message { return this.received.filter(message => message.method === method).slice(-1)[0]; }

    async ready(profile = "assisted"): Promise<void> {
        this.chat.start("claude", profile);
        this.say({
            jsonrpc: "2.0", method: "_fleet/ready", params: {
                agent: "claude", cwd: "/home/anna", profile, instructions: "Be brief.",
                layers: [{ layer: "base", source: "package", text: "Be brief." }],
                mcp: { command: "/usr/libexec/cockpit-fleet/fleet-mcp", args: ["--profile", profile], env: [] },
            },
        });
        await tick();
    }

    update(update: object, sessionId = "s1"): void {
        this.say({ jsonrpc: "2.0", method: "session/update", params: { sessionId, update } });
    }

    ask(id: number, tool: string): void {
        this.say({
            jsonrpc: "2.0", id, method: "session/request_permission",
            params: { sessionId: "s1", options: OPTIONS, toolCall: { toolCallId: `c${id}`, title: tool, rawInput: { limit: "web" }, _meta: { claudeCode: { toolName: `mcp__cockpit-fleet__${tool}` } } } },
        });
    }

    answerTo(id: number): Message | undefined { return this.received.find(message => message.id === id && !message.method); }
}

test("the agent is started by name and told nothing until fleet-chat says it is ready", async () => {
    const agent = new Agent();
    agent.chat.start("claude", "assisted");
    assert.deepEqual(agent.spawned, [["claude", "assisted"]]);
    assert.equal(agent.chat.getState().phase, "starting");
    assert.deepEqual(agent.received, []);
    await agent.ready();
    assert.deepEqual(agent.methods(), ["initialize", "session/list"]);
    assert.equal(agent.chat.getState().phase, "ready");
    const capabilities = agent.last("initialize").params.clientCapabilities;
    assert.deepEqual(capabilities, { fs: { readTextFile: false, writeTextFile: false }, terminal: false });
    const state = agent.chat.getState();
    assert.equal(state.profile, "assisted");
    assert.deepEqual(state.layers, [{ layer: "base", source: "package", text: "Be brief." }]);
    assert.deepEqual(agent.listed, ["assisted"]);
});

test("the first message opens a session with fleet-mcp as the only server and no tool of the agent", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    const sent = agent.chat.send("how are the nodes?");
    await tick();
    agent.update({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Fine." } });
    agent.update({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "not ours" } }, "other");
    await sent;
    const created = agent.last("session/new").params;
    assert.equal(created.cwd, "/home/anna");
    assert.deepEqual(created.mcpServers, [{ name: "cockpit-fleet", command: "/usr/libexec/cockpit-fleet/fleet-mcp", args: ["--profile", "assisted"], env: [] }]);
    assert.equal(created._meta.disableBuiltInTools, true);
    assert.deepEqual(created._meta.systemPrompt, { append: "Be brief." });
    assert.deepEqual(created._meta.claudeCode.options, { settingSources: [], strictMcpConfig: true });
    assert.deepEqual(agent.last("session/prompt").params, { sessionId: "s1", prompt: [{ type: "text", text: "how are the nodes?" }] });
    const state = agent.chat.getState();
    assert.deepEqual(state.items, [{ kind: "user", text: "how are the nodes?" }, { kind: "agent", text: "Fine." }]);
    assert.equal(state.busy, false);
    // The second message goes to the same session.
    await agent.chat.send("and now?");
    assert.equal(agent.methods().filter(method => method === "session/new").length, 1);
});

test("reading is allowed without asking, a change waits for the person", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.send("launch it");
    await tick();
    agent.ask(50, "run_list");
    await tick();
    assert.equal(agent.chat.getState().ask, null);
    assert.deepEqual(agent.answerTo(50)!.result, { outcome: { outcome: "selected", optionId: "allow-once" } });

    agent.ask(51, "run_start");
    await tick();
    const ask = agent.chat.getState().ask!;
    assert.equal(ask.tool?.description, "Launch a job template.");
    assert.deepEqual(ask.input, { limit: "web" });
    assert.equal(agent.answerTo(51), undefined);
    agent.chat.answer("refuse");
    await tick();
    assert.deepEqual(agent.answerTo(51)!.result, { outcome: { outcome: "selected", optionId: "reject" } });
    assert.equal(agent.chat.getState().ask, null);
});

test("always is remembered by the page for the class, for the conversation, and answered as once", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.send("launch it twice");
    await tick();
    agent.ask(60, "run_start");
    agent.ask(61, "run_cancel");
    await tick();
    assert.equal(agent.chat.getState().ask!.always, true);
    agent.chat.answer("always");
    await tick();
    for (const id of [60, 61]) assert.deepEqual(agent.answerTo(id)!.result, { outcome: { outcome: "selected", optionId: "allow-once" } });
    agent.ask(62, "run_start");
    await tick();
    assert.equal(agent.chat.getState().ask, null);
    assert.ok(agent.answerTo(62));
});

test("the questions of each profile", () => {
    const tool = (name: string) => TOOLS.find(one => one.name === name)!;
    const diagnostic: ToolInfo = { ...tool("run_adhoc"), class: "adhoc-diagnostic" };
    const table: [string, ToolInfo | null, string][] = [
        ["observer", tool("run_list"), "none"],
        ["assisted", tool("run_list"), "none"],
        ["assisted", tool("run_start"), "ask"],
        ["diagnostic", diagnostic, "none"],
        ["diagnostic", tool("run_start"), "ask"],
        ["autonomous", tool("run_start"), "none"],
        ["autonomous", tool("run_adhoc"), "every"],
        ["administrator", tool("run_start"), "ask"],
        ["administrator", tool("run_adhoc"), "every"],
        ["administrator", tool("template_update"), "every"],
        ["administrator", { ...tool("template_update"), class: "code" }, "every"],
        // Diagnostic only in its own profile: elsewhere the backend would not offer it.
        ["assisted", diagnostic, "ask"],
        ["autonomous", null, "every"],
    ];
    for (const [profile, one, expected] of table) assert.equal(question(profile, one), expected, `${profile} ${one?.name} ${one?.class}`);
});

test("an ad-hoc command of an administrator is asked about every time, with no always", async () => {
    const agent = new Agent();
    await agent.ready("administrator");
    agent.chat.send("check the disks");
    await tick();
    agent.ask(90, "run_adhoc");
    await tick();
    assert.equal(agent.chat.getState().ask!.always, false);
    agent.chat.answer("always");
    await tick();
    agent.ask(91, "run_adhoc");
    await tick();
    assert.equal(agent.chat.getState().ask!.tool?.name, "run_adhoc");
});

test("an autonomous agent launches with no question; the backend holds it", async () => {
    const agent = new Agent();
    await agent.ready("autonomous");
    agent.chat.send("launch it");
    await tick();
    agent.ask(95, "run_start");
    await tick();
    assert.equal(agent.chat.getState().ask, null);
    assert.deepEqual(agent.answerTo(95)!.result, { outcome: { outcome: "selected", optionId: "allow-once" } });
});

test("something that is not a tool of ours is always asked about, with no always", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.send("x");
    await tick();
    agent.say({ jsonrpc: "2.0", id: 70, method: "session/request_permission", params: { sessionId: "s1", options: OPTIONS, toolCall: { title: "Bash", rawInput: { command: "id" } } } });
    await tick();
    assert.equal(agent.chat.getState().ask!.tool, null);
    agent.chat.answer("always");
    await tick();
    agent.say({ jsonrpc: "2.0", id: 71, method: "session/request_permission", params: { sessionId: "s1", options: OPTIONS, toolCall: { title: "Bash", rawInput: { command: "id" } } } });
    await tick();
    assert.equal(agent.chat.getState().ask!.title, "Bash");
});

test("stopping an answer cancels it and what was waiting for a yes", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.send("launch it");
    await tick();
    agent.ask(80, "run_start");
    await tick();
    agent.chat.cancel();
    await tick();
    assert.deepEqual(agent.last("session/cancel").params, { sessionId: "s1" });
    assert.deepEqual(agent.answerTo(80)!.result, { outcome: { outcome: "cancelled" } });
    assert.equal(agent.chat.getState().ask, null);
});

test("an agent with no login to its model is said so, not shown as an error", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.answers["session/prompt"] = { error: { code: -32000, message: "Authentication required" } };
    await agent.chat.send("hello");
    const state = agent.chat.getState();
    assert.equal(state.needsLogin, true);
    assert.deepEqual(state.items, [{ kind: "user", text: "hello" }]);
    agent.answers["session/prompt"] = { error: { code: -32603, message: "boom" } };
    await agent.chat.send("again");
    assert.deepEqual(agent.chat.getState().items.slice(-1)[0], { kind: "error", text: "boom" });
});

test("an earlier conversation is replayed by the agent", async () => {
    const agent = new Agent();
    agent.answers["session/list"] = { sessions: [
        { sessionId: "old", title: "Older", updatedAt: "2026-10-12T10:00:00Z" },
        { sessionId: "new", title: "Newer", updatedAt: "2026-10-13T10:00:00Z" },
    ] };
    await agent.ready();
    assert.deepEqual(agent.chat.getState().sessions.map(one => one.sessionId), ["new", "old"]);
    delete agent.answers["session/load"];
    const opened = agent.chat.open("old");
    await tick();
    assert.equal(agent.chat.getState().loading, true);
    agent.update({ sessionUpdate: "user_message_chunk", content: { type: "text", text: "before" } }, "old");
    const load = agent.last("session/load");
    assert.equal(load.params.sessionId, "old");
    assert.equal(load.params.mcpServers.length, 1);
    agent.say({ jsonrpc: "2.0", id: load.id, result: {} });
    await opened;
    assert.deepEqual(agent.chat.getState().items, [{ kind: "user", text: "before" }]);
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("go on");
    assert.equal(agent.last("session/prompt").params.sessionId, "old");
    assert.ok(!agent.methods().includes("session/new"));
});

test("a refusal by the backend ends the start and is what the page shows", async () => {
    const agent = new Agent();
    agent.chat.start("claude", "assisted");
    agent.say({ jsonrpc: "2.0", method: "_fleet/error", params: { code: "conflict", message: "2 chat sessions are open already" } });
    agent.exit("exit status 1");
    const state = agent.chat.getState();
    assert.equal(state.phase, "ended");
    assert.equal(state.error, "2 chat sessions are open already");
    assert.deepEqual(agent.received, []);
});

test("an agent that dies leaves the conversation and says why; the chat can start again", async () => {
    const agent = new Agent();
    await agent.ready();
    const sent = agent.chat.send("hello");
    await tick();
    agent.exit("Killed");
    await sent;
    let state = agent.chat.getState();
    assert.deepEqual([state.phase, state.error, state.busy], ["ended", "Killed", false]);
    assert.deepEqual(state.items, [{ kind: "user", text: "hello" }]);
    await agent.ready();
    state = agent.chat.getState();
    assert.deepEqual([state.phase, state.error, state.items.length], ["ready", "", 0]);
});

test("ending the chat closes the channel, and so the agent", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.stop();
    assert.equal(agent.closed, true);
    assert.equal(agent.chat.getState().phase, "idle");
    // What the channel says once closed changes nothing.
    agent.exit("cancelled");
    assert.equal(agent.chat.getState().error, "");
});
