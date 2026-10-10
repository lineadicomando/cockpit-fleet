import cockpit from "cockpit";

const FLEETCTL = "/usr/libexec/cockpit-fleet/fleetctl";
const FLEET_SECRET = "/usr/libexec/cockpit-fleet/fleet-secret";

// The backend answered, and the answer is an error.
export class FleetError extends Error {
    readonly code: string;

    constructor(code: string, message: string) {
        super(message);
        this.name = "FleetError";
        this.code = code;
    }
}

export type AccessProblem = "not-installed" | "no-access" | "failed";

// The backend could not be reached at all.
export class AccessError extends Error {
    readonly problem: AccessProblem;

    constructor(problem: AccessProblem, message: string) {
        super(message);
        this.name = "AccessError";
        this.problem = problem;
    }
}

interface SpawnFailure {
    problem?: string | null;
    message?: string;
}

function classify(ex: unknown): AccessError {
    const { problem, message = "" } = (ex ?? {}) as SpawnFailure;
    if (problem === "not-found" || /command not found|No such file or directory/.test(message))
        return new AccessError("not-installed", message);
    // sudo -n refuses instead of asking: the user is in no role group.
    if (/a password is required|is not allowed to|not in the sudoers|may not run sudo/.test(message))
        return new AccessError("no-access", message);
    return new AccessError("failed", message || String(ex));
}

// Set once a call only went through with Cockpit's administrative access.
let elevated = false;

// An input in pieces is one too large to be sent, or even built, as a whole.
type Input = string | string[];

function spawn(argv: string[], input: Input, superuser: boolean): Promise<string> {
    const process = cockpit.spawn(["sudo", "-n", ...argv], {
        err: "message",
        // English messages from sudo, so that classify() can read them.
        environ: ["LC_ALL=C"],
        ...(superuser ? { superuser: "try" as const } : {}),
    });
    if (typeof input === "string") return process.input(input);
    for (const piece of input.slice(0, -1)) process.input(piece, true);
    return process.input(input[input.length - 1] ?? "");
}

// Runs one of the helpers through sudo, with its input on stdin.
async function run(argv: string[], input: Input): Promise<string> {
    try {
        return await spawn(argv, input, elevated);
    } catch (ex) {
        const error = classify(ex);
        if (error.problem !== "no-access" || elevated) throw error;
    }
    // Not in a role group: an administrator of the machine still gets in, as
    // root, when administrative access is active in the Cockpit session.
    try {
        const output = await spawn(argv, input, true);
        elevated = true;
        return output;
    } catch (ex) {
        throw classify(ex);
    }
}

// Calls one backend command. Parameters travel as JSON on stdin, never in argv.
export function call<T>(command: string, params: object = {}): Promise<T> {
    return callWith<T>(command, JSON.stringify(params));
}

// The same, for parameters already written as JSON, whole or in pieces.
export async function callWith<T>(command: string, json: string | string[]): Promise<T> {
    const output = await run(["-u", "cockpit-fleet", FLEETCTL, command], json);
    let answer: { ok: boolean; data?: T; error?: { code: string; message: string } };
    try {
        answer = JSON.parse(output);
    } catch {
        throw new AccessError("failed", `unexpected answer from fleetctl: ${output.slice(0, 200)}`);
    }
    if (!answer.ok) {
        throw new FleetError(answer.error?.code ?? "internal", answer.error?.message ?? "");
    }
    return answer.data as T;
}

// Stores a secret. The value goes to the root helper on stdin and cannot be
// read back afterwards, by this interface or by anything else.
export async function setSecret(name: string, value: string): Promise<void> {
    await run([FLEET_SECRET, "set", name], value);
}

// Hands over the secret answer to a survey question and returns the ticket
// that run.start takes in its place. The ticket serves one launch.
export async function answerSecret(value: string): Promise<string> {
    return (await run([FLEET_SECRET, "answer"], value)).trim();
}

export async function deleteSecret(name: string): Promise<void> {
    await run([FLEET_SECRET, "delete", name], "");
}

// For tests: forget that a previous call needed administrative access.
export function resetAccess(): void {
    elevated = false;
}
