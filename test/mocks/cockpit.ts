// Test double for the "cockpit" module: records every spawn() and answers
// through a handler set by each test.

export interface SpawnCall {
    args: string[];
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
    input(data: string): SpawnProcess;
}

const cockpit = {
    spawn(args: string[]): SpawnProcess {
        const call: SpawnCall = { args };
        calls.push(call);
        // Deferred by a microtask so a chained .input() is recorded first.
        const p = Promise.resolve().then(() => handler(call)) as SpawnProcess;
        p.input = (data: string) => { call.input = data; return p; };
        return p;
    },
    location: { path: [] as string[], options: {}, go() { /* no-op */ } },
    addEventListener() { /* no-op */ },
    removeEventListener() { /* no-op */ },
};

export default cockpit;
