import { test } from "node:test";
import assert from "node:assert/strict";
import { AcpError, Connection, NOTICE_MARK, applyUpdate, fleetTool, namedByAgent, permissionOutcome, type Item } from "../src/lib/acp.ts";
import { Chat, question, type ToolInfo, type Transport } from "../src/lib/chat.ts";
import type { ChatSessionRun, ChatSessionRuns } from "../src/lib/types.ts";

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
    assert.equal(namedByAgent({ _meta: { claudeCode: { toolName: "mcp__cockpit-fleet__run_start" } } }), true);
    assert.equal(namedByAgent({ title: "mcp__cockpit-fleet__run_log" }), false);
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

const TOOLS: Omit<ToolInfo, "question">[] = [
    { name: "run_list", description: "The runs.", readOnly: true, class: "read", inputSchema: {} },
    { name: "run_start", description: "Launch a job template.", readOnly: false, class: "run", inputSchema: {} },
    { name: "run_cancel", description: "Cancel a run.", readOnly: false, class: "run", inputSchema: {} },
    { name: "run_adhoc", command: "run.adhoc", description: "Run an ad-hoc command.", readOnly: false, class: "adhoc", inputSchema: {} },
];

// What mcp.tools says of them in a profile (profiles.question in the backend),
// and in a session the person let the agent act without asking in.
// In assisted that is the mode autonomous: launches unasked, held by the backend.
function toolsOf(profile: string, unattended = false): ToolInfo[] {
    return TOOLS.map(tool => {
        if (unattended && profile !== "assisted") return { ...tool, question: "none" as const };
        let asked: ToolInfo["question"] = tool.class === "read" ? "none" : tool.class === "adhoc" ? "every" : "ask";
        if (unattended && tool.class === "run") asked = "none";
        return { ...tool, question: asked };
    });
}

interface Message { id?: number; method?: string; params?: any; result?: any }

class Agent {
    readonly chat: Chat;
    readonly received: Message[] = [];
    closed = false;
    spawned: string[][] = [];
    // The profiles the tools were asked for, with the session when one was named.
    listed: string[] = [];
    // What chat.unattended was asked, and the sessions it holds for.
    unattendedCalls: [string, boolean, string][] = [];
    unattendedSessions = new Set<string>();
    // The runs set for sessions of fleet-mcp, with the methods the agent had been sent by then.
    limits: { session: string; runs: number; before: number }[] = [];
    // Why setting them fails, when it does.
    limitFails = "";
    // What chat.session answers, and the sessions it was asked for.
    sessionRuns: ChatSessionRuns = { launched: 0, allowed: 2, granted: 0, not_ended: [], ended: [] };
    watched: string[] = [];
    // The calls the person allowed, recorded in the backend; why that fails, when it does.
    consents: { session: string; command: string; input: unknown; before: number }[] = [];
    consentFails = "";
    private onData: (text: string) => void = () => undefined;
    private onExit: (error: string) => void = () => undefined;
    // How the agent answers each method; a function gets the message.
    answers: Record<string, unknown> = {
        initialize: { protocolVersion: 1, agentCapabilities: { loadSession: true, sessionCapabilities: { list: {} } } },
        "session/list": { sessions: [] },
        "session/new": { sessionId: "s1" },
        "session/load": {},
    };

    constructor(watch = false) {
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
        }, async (profile, session) => {
            this.listed.push(session ? `${profile}@${session}` : profile);
            return toolsOf(profile, this.unattendedSessions.has(session));
        },
        async (session, runs) => {
            if (this.limitFails) throw new Error(this.limitFails);
            this.limits.push({ session, runs, before: this.received.length });
        }, watch ? {
            load: async session => { this.watched.push(session); return structuredClone(this.sessionRuns); },
            tell: (ended: ChatSessionRun[]) => `ended: ${ended.map(run => `${run.id} ${run.status}`).join(", ")}`,
        } : null, async (session, command, input) => {
            if (this.consentFails) throw new Error(this.consentFails);
            this.consents.push({ session, command, input, before: this.received.length });
        }, async (session, on, profile) => {
            this.unattendedCalls.push([session, on, profile]);
            if (on) this.unattendedSessions.add(session); else this.unattendedSessions.delete(session);
        });
    }

    // The runs of the conversation as chat.session says, and the page asking for them.
    async runs(notEnded: [string, string][], ended: [string, string][]): Promise<void> {
        this.sessionRuns = {
            ...this.sessionRuns,
            not_ended: notEnded.map(([id, status]) => ({ id, title: id, status })),
            ended: ended.map(([id, status]) => ({ id, title: id, status })),
        };
        await this.chat.refreshRuns();
        await tick();
    }

    prompts(): string[] { return this.received.filter(message => message.method === "session/prompt").map(message => message.params.prompt[0].text); }

    say(message: object): void { this.onData(JSON.stringify(message) + "\n"); }
    exit(error: string): void { this.onExit(error); }
    methods(): (string | undefined)[] { return this.received.map(message => message.method); }
    last(method: string): Message { return this.received.filter(message => message.method === method).slice(-1)[0]; }

    async ready(profile = "assisted"): Promise<void> {
        this.chat.start("claude", profile);
        await this.started(profile);
    }

    // What fleet-chat says once chat.start has let the agent start.
    async started(profile: string): Promise<void> {
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
    // With the session of fleet-mcp the page drew for the conversation, to follow it.
    const fleetSession = agent.chat.getState().fleetSession;
    assert.match(fleetSession, /^[0-9a-f]{16}$/);
    assert.deepEqual(created.mcpServers, [{ name: "cockpit-fleet", command: "/usr/libexec/cockpit-fleet/fleet-mcp", args: ["--profile", "assisted", "--session", fleetSession], env: [] }]);
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

test("a call known only by its title is asked about, even one that reads", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.send("look");
    await tick();
    agent.say({
        jsonrpc: "2.0", id: 60, method: "session/request_permission",
        params: { sessionId: "s1", options: OPTIONS, toolCall: { toolCallId: "c60", title: "mcp__cockpit-fleet__run_list", rawInput: {} } },
    });
    await tick();
    assert.equal(agent.chat.getState().ask?.tool?.name, "run_list");
    assert.equal(agent.answerTo(60), undefined);
    agent.chat.answer("once");
    await tick();
    assert.deepEqual(agent.answerTo(60)!.result, { outcome: { outcome: "selected", optionId: "allow-once" } });
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

test("a tool the page does not know, or says nothing of, is asked about every time", () => {
    assert.equal(question(null), "every");
    assert.equal(question({ ...TOOLS[0], question: "none" }), "none");
    assert.equal(question({ ...TOOLS[0] } as ToolInfo), "every");
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

test("an ad-hoc command allowed once is recorded before the agent is answered, so it does not wait again", async () => {
    const agent = new Agent();
    await agent.ready("administrator");
    agent.chat.send("check the disks");
    await tick();
    agent.ask(92, "run_adhoc");
    await tick();
    agent.chat.answer("once");
    await tick();
    const session = agent.chat.getState().fleetSession;
    assert.deepEqual(agent.consents.map(({ before, ...one }) => one), [{ session, command: "run.adhoc", input: { limit: "web" } }]);
    assert.ok(agent.consents[0].before <= agent.received.indexOf(agent.answerTo(92)!));
    // Refused, or a command that does not wait, records nothing; a failure still answers.
    agent.ask(93, "run_adhoc");
    await tick();
    agent.chat.answer("refuse");
    agent.ask(94, "run_start");
    await tick();
    agent.chat.answer("once");
    await tick();
    assert.equal(agent.consents.length, 1);
    agent.consentFails = "forbidden";
    agent.ask(96, "run_adhoc");
    await tick();
    agent.chat.answer("once");
    await tick();
    assert.deepEqual(agent.answerTo(96)!.result, { outcome: { outcome: "selected", optionId: "allow-once" } });
});

test("without questions, nothing is asked in any conversation of the session, until turned off or the profile changes", async () => {
    const agent = new Agent();
    await agent.ready("administrator");
    // Before the first message: set once its session is drawn, before the agent is told.
    await agent.chat.unattend(true);
    assert.deepEqual(agent.unattendedCalls, []);
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("free some space");
    const first = agent.chat.getState().fleetSession;
    assert.deepEqual(agent.unattendedCalls, [[first, true, "administrator"]]);
    agent.ask(97, "run_adhoc");
    await tick();
    assert.equal(agent.chat.getState().ask, null);
    assert.deepEqual(agent.answerTo(97)!.result, { outcome: { outcome: "selected", optionId: "allow-once" } });
    assert.deepEqual(agent.consents, []);
    // A new conversation gets it too.
    agent.chat.fresh();
    await agent.chat.send("again");
    const second = agent.chat.getState().fleetSession;
    assert.notEqual(second, first);
    assert.deepEqual(agent.unattendedCalls[agent.unattendedCalls.length - 1], [second, true, "administrator"]);
    // Off: asked again at once, and the backend told.
    await agent.chat.unattend(false);
    assert.deepEqual(agent.unattendedCalls[agent.unattendedCalls.length - 1], [second, false, "administrator"]);
    agent.ask(98, "run_adhoc");
    await tick();
    assert.equal(agent.chat.getState().ask!.tool?.name, "run_adhoc");
    agent.chat.answer("refuse");
    await tick();
    // On again, then another profile: asked again there.
    await agent.chat.unattend(true);
    assert.equal(agent.chat.getState().unattended, true);
    agent.chat.changeProfile("assisted");
    assert.equal(agent.chat.getState().unattended, false);
});

test("without questions in assisted it launches with no question; the backend holds it", async () => {
    const agent = new Agent();
    await agent.ready("assisted");
    await agent.chat.unattend(true);
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

test("each conversation has a session of fleet-mcp of its own", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    assert.equal(agent.chat.getState().fleetSession, "");
    await agent.chat.send("one");
    const first = agent.chat.getState().fleetSession;
    await agent.chat.send("two");
    assert.equal(agent.chat.getState().fleetSession, first);
    agent.chat.fresh();
    assert.equal(agent.chat.getState().fleetSession, "");
    await agent.chat.send("three");
    const second = agent.chat.getState().fleetSession;
    assert.match(second, /^[0-9a-f]{16}$/);
    assert.notEqual(second, first);
    assert.equal(agent.last("session/new").params.mcpServers[0].args.at(-1), second);
});

test("the runs chosen when the questions are turned off are set for each conversation before the agent is told", async () => {
    const agent = new Agent();
    await agent.ready("assisted");
    await agent.chat.unattend(true, 5);
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("reboot the lab");
    const first = agent.chat.getState().fleetSession;
    assert.deepEqual(agent.limits, [{ session: first, runs: 5, before: agent.received.indexOf(agent.last("session/new")) }]);
    await agent.chat.send("again");
    assert.equal(agent.limits.length, 1);
    // An earlier conversation opened again has a session of its own, set as well.
    await agent.chat.open("old");
    assert.equal(agent.limits.length, 2);
    assert.equal(agent.limits[1].session, agent.chat.getState().fleetSession);
    assert.notEqual(agent.limits[1].session, first);
    assert.equal(agent.limits[1].before, agent.received.indexOf(agent.last("session/load")));
});

test("nothing is set for config.toml's runs, nor while the questions are on", async () => {
    for (const unattended of [true, false]) {
        const agent = new Agent();
        await agent.ready("assisted");
        if (unattended) await agent.chat.unattend(true, 0);
        agent.answers["session/prompt"] = { stopReason: "end_turn" };
        await agent.chat.send("hello");
        assert.deepEqual(agent.limits, [], String(unattended));
        assert.equal(agent.unattendedCalls.length, unattended ? 1 : 0);
    }
});

test("the runs chosen in a conversation under way are set before the questions are off", async () => {
    const agent = new Agent();
    await agent.ready("assisted");
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("hello");
    const session = agent.chat.getState().fleetSession;
    await agent.chat.unattend(true, 7);
    assert.deepEqual(agent.limits.map(limit => [limit.session, limit.runs]), [[session, 7]]);
    assert.deepEqual(agent.unattendedCalls, [[session, true, "assisted"]]);
    assert.equal(agent.chat.getState().runs, 7);
});

test("runs that cannot be set leave the conversation unopened, and say why", async () => {
    const agent = new Agent();
    agent.limitFails = "chat.limit_runs requires the operator role";
    await agent.ready("assisted");
    await agent.chat.unattend(true, 5);
    await agent.chat.send("reboot the lab");
    assert.equal(agent.last("session/new"), undefined);
    const state = agent.chat.getState();
    assert.deepEqual(state.items[state.items.length - 1], { kind: "error", text: "chat.limit_runs requires the operator role" });
    assert.equal(state.sessionId, "");
    assert.equal(state.busy, false);
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
    assert.deepEqual(load.params.mcpServers[0].args.slice(-2), ["--session", agent.chat.getState().fleetSession]);
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

test("a prepared message waits for the session and is never sent by itself", async () => {
    const agent = new Agent();
    agent.chat.prepare("examine the run r1", "diagnostic");
    assert.equal(agent.chat.getState().suggested, "diagnostic");
    await agent.ready("diagnostic");
    let state = agent.chat.getState();
    assert.equal(state.draft, "examine the run r1");
    assert.equal(state.suggested, "");
    assert.equal(agent.chat.takeDraft(), "examine the run r1");
    assert.equal(agent.chat.getState().draft, "");
    assert.equal(agent.chat.takeDraft(), "");
    assert.ok(!agent.methods().includes("session/new"));
    assert.ok(!agent.methods().includes("session/prompt"));
    // In a conversation open, it goes to the composer of that one.
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("hello");
    agent.chat.prepare("examine the run r2");
    state = agent.chat.getState();
    assert.equal(state.draft, "examine the run r2");
    assert.equal(state.sessionId, "s1");
    assert.equal(agent.methods().filter(method => method === "session/prompt").length, 1);
});

test("what is written and not sent is kept across conversations and sessions", async () => {
    const agent = new Agent();
    await agent.ready();
    agent.chat.typed = "list the hosts of";
    agent.chat.fresh();
    assert.equal(agent.chat.typed, "list the hosts of");
    agent.chat.stop();
    agent.chat.start("claude", "assisted");
    assert.equal(agent.chat.typed, "list the hosts of");
    // A prepared message leaves it to the composer, which replaces it.
    agent.chat.prepare("examine the run r1");
    assert.equal(agent.chat.typed, "list the hosts of");
});

test("the profile changes with the conversation kept: the agent is started again and opens it", async () => {
    const agent = new Agent();
    await agent.ready("diagnostic");
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("why did pc215 fail?");
    // Nothing changes while it answers, nor to the same profile.
    agent.chat.changeProfile("diagnostic");
    assert.equal(agent.chat.getState().changing, "");
    agent.chat.changeProfile("administrator");
    let state = agent.chat.getState();
    assert.equal(state.changing, "administrator");
    assert.equal(agent.closed, true);
    // The next agent starts once the first has gone, not before.
    await tick();
    assert.equal(agent.spawned.length, 1);
    agent.exit("");
    await tick();
    assert.deepEqual(agent.spawned, [["claude", "diagnostic"], ["claude", "administrator"]]);
    state = agent.chat.getState();
    assert.equal(state.phase, "starting");
    assert.deepEqual(state.items[0], { kind: "user", text: "why did pc215 fail?" });
    await agent.started("administrator");
    await tick();
    const loaded = agent.last("session/load").params;
    assert.equal(loaded.sessionId, "s1");
    // Opened again in a new profile: a new session of fleet-mcp, with a count of its own.
    assert.deepEqual(loaded.mcpServers[0].args.slice(0, 3), ["--profile", "administrator", "--session"]);
    assert.match(loaded.mcpServers[0].args[3], /^[0-9a-f]{16}$/);
    state = agent.chat.getState();
    assert.equal(state.phase, "ready");
    assert.equal(state.profile, "administrator");
    assert.equal(state.changing, "");
    assert.equal(state.sessionId, "s1");
    assert.deepEqual(state.items.slice(-1), [{ kind: "profile", profile: "administrator" }]);
});

test("a change of profile waits for the sessions to free, and says why it could not", async t => {
    const agent = new Agent();
    await agent.ready("assisted");
    t.mock.timers.enable({ apis: ["setTimeout"] });
    agent.chat.changeProfile("observer");
    agent.exit("");
    t.mock.timers.tick(0);
    assert.equal(agent.spawned.length, 2);
    agent.say({ jsonrpc: "2.0", method: "_fleet/error", params: { code: "sessions_full", message: "2 chat sessions are open already" } });
    agent.exit("exit status 1");
    assert.equal(agent.chat.getState().changing, "observer");
    t.mock.timers.tick(1000);
    assert.equal(agent.spawned.length, 3);
    agent.say({ jsonrpc: "2.0", method: "_fleet/error", params: { code: "not_accepted", message: "not accepted" } });
    agent.exit("exit status 1");
    const state = agent.chat.getState();
    assert.equal(state.changing, "");
    assert.equal(state.phase, "ended");
    assert.equal(state.error, "not accepted");
});

test("a run the agent left running is told to it when it ends, once, in a message of the page", async () => {
    const agent = new Agent(true);
    await agent.ready();
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("delete the iso files");
    const session = agent.chat.getState().fleetSession;
    assert.ok(agent.watched.length > 0 && agent.watched.every(one => one === session));
    await agent.runs([["r1", "pending_approval"]], []);
    assert.deepEqual(agent.chat.getState().sessionRuns?.not_ended.map(run => run.id), ["r1"]);
    assert.deepEqual(agent.prompts(), ["delete the iso files"]);

    await agent.runs([], [["r1", "successful"]]);
    assert.deepEqual(agent.prompts(), ["delete the iso files", `${NOTICE_MARK}ended: r1 successful`]);
    const items = agent.chat.getState().items;
    assert.deepEqual(items[items.length - 1], { kind: "notice", text: `${NOTICE_MARK}ended: r1 successful` });
    await agent.runs([], [["r1", "successful"]]);
    assert.equal(agent.prompts().length, 2);
});

test("a run that ends while the agent answers is told once it is idle", async () => {
    const agent = new Agent(true);
    await agent.ready();
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("reboot PC215");
    await agent.runs([["r1", "running"]], []);
    delete agent.answers["session/prompt"];
    const answering = agent.chat.send("and the others?");
    await tick();
    await agent.runs([], [["r1", "failed"]]);
    assert.deepEqual(agent.prompts(), ["reboot PC215", "and the others?"]);
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    agent.say({ jsonrpc: "2.0", id: agent.last("session/prompt").id, result: { stopReason: "end_turn" } });
    await answering;
    await tick();
    assert.deepEqual(agent.prompts().slice(-1), [`${NOTICE_MARK}ended: r1 failed`]);
});

test("runs that end within the answer that launched them, or that the person stopped, are not told", async () => {
    const agent = new Agent(true);
    await agent.ready();
    delete agent.answers["session/prompt"];
    const answering = agent.chat.send("ping them");
    await tick();
    // Seen running while the agent answers, and ended before it is done: it followed them.
    await agent.runs([["r1", "running"]], []);
    await agent.runs([], [["r1", "successful"]]);
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    agent.say({ jsonrpc: "2.0", id: agent.last("session/prompt").id, result: { stopReason: "end_turn" } });
    await answering;
    await tick();
    assert.deepEqual(agent.prompts(), ["ping them"]);

    await agent.runs([["r2", "running"]], [["r1", "successful"]]);
    agent.chat.silenceRuns();
    await agent.runs([], [["r1", "successful"], ["r2", "canceled"]]);
    assert.deepEqual(agent.prompts(), ["ping them"]);
});

test("a new conversation forgets the runs of the last one", async () => {
    const agent = new Agent(true);
    await agent.ready();
    agent.answers["session/prompt"] = { stopReason: "end_turn" };
    await agent.chat.send("one");
    await agent.runs([["r1", "running"]], []);
    agent.chat.fresh();
    assert.equal(agent.chat.getState().sessionRuns, null);
    agent.sessionRuns = { ...agent.sessionRuns, not_ended: [] };
    await agent.chat.send("two");
    await agent.runs([], [["r1", "successful"]]);
    assert.deepEqual(agent.prompts(), ["one", "two"]);
});

test("a message of the page is told apart when the conversation is replayed", () => {
    let items: Item[] = [];
    const say = (text: string) => { items = applyUpdate(items, { sessionUpdate: "user_message_chunk", content: { type: "text", text } }); };
    say(`${NOTICE_MARK}A run has ended:`);
    say("\n- r1: successful");
    items = applyUpdate(items, { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "It went well." } });
    say("thanks");
    assert.deepEqual(items.map(item => item.kind), ["notice", "agent", "user"]);
    assert.equal((items[0] as { text: string }).text, `${NOTICE_MARK}A run has ended:\n- r1: successful`);
});
