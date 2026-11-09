// Test double for the "cockpit" module: records every spawn() and answers
// through a handler set by each test. A handler that throws rejects the call,
// the way a failed process does.

export interface SpawnOptions {
    superuser?: string;
    err?: string;
    environ?: string[];
}

export interface SpawnCall {
    args: string[];
    options: SpawnOptions;
    input?: string;
}

type SpawnHandler = (call: SpawnCall) => string | Promise<string>;

export const calls: SpawnCall[] = [];
let handler: SpawnHandler = () => "";

export function setSpawnHandler(h: SpawnHandler): void {
    handler = h;
    calls.length = 0;
}

interface SpawnProcess extends Promise<string> {
    input(data: string, stream?: boolean): SpawnProcess;
}

// A channel: records what the page sends, and lets the test answer as the
// bridge would, with acknowledgements, output and the close.
export class MockChannel {
    options: Record<string, unknown>;
    sent: Uint8Array[] = [];
    controls: { command: string }[] = [];
    closedWith: string | null = null;
    private handlers: Record<string, ((event: unknown, data: unknown) => void)[]> = {};

    constructor(options: Record<string, unknown>) {
        this.options = options;
    }

    send(data: Uint8Array | ArrayBuffer): void {
        this.sent.push(data instanceof Uint8Array ? data : new Uint8Array(data));
    }

    control(options: { command: string }): void {
        this.controls.push(options);
    }

    close(problem?: string): void {
        this.closedWith = problem ?? "";
        this.emit("close", { command: "close", problem });
    }

    addEventListener(event: string, handler: (event: unknown, data: unknown) => void): void {
        (this.handlers[event] ??= []).push(handler);
    }

    emit(event: string, data: unknown): void {
        for (const handler of this.handlers[event] ?? []) handler({}, data);
    }

    // Every byte sent, as one array.
    bytes(): Uint8Array {
        const whole = new Uint8Array(this.sent.reduce((total, piece) => total + piece.length, 0));
        let at = 0;
        for (const piece of this.sent) {
            whole.set(piece, at);
            at += piece.length;
        }
        return whole;
    }
}

export const channels: MockChannel[] = [];

const cockpit = {
    channel(options: Record<string, unknown>): MockChannel {
        const channel = new MockChannel(options);
        channels.push(channel);
        return channel;
    },
    transport: {
        csrf_token: "token",
        uri(suffix = "") { return `http://localhost:9090/cockpit/${suffix}`; },
    },
    spawn(args: string[], options: SpawnOptions = {}): SpawnProcess {
        const call: SpawnCall = { args, options };
        calls.push(call);
        // Deferred by a microtask so a chained .input() is recorded first.
        const p = Promise.resolve().then(() => handler(call)) as SpawnProcess;
        // Sent in pieces, with stream set on all but the last: joined here.
        p.input = (data: string) => { call.input = (call.input ?? "") + data; return p; };
        return p;
    },
    location: { path: [] as string[], options: {}, go() { /* no-op */ } },
    jump() { /* no-op */ },
    addEventListener() { /* no-op */ },
    removeEventListener() { /* no-op */ },
};

export default cockpit;
