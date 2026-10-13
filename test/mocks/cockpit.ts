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

const cockpit = {
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
