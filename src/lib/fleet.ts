import cockpit from "cockpit";

const FLEETCTL = "/usr/libexec/cockpit-fleet/fleetctl";

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

function spawn(command: string, params: object, superuser: boolean): Promise<string> {
    return cockpit
        .spawn(["sudo", "-n", "-u", "cockpit-fleet", FLEETCTL, command], {
            err: "message",
            // English messages from sudo, so that classify() can read them.
            environ: ["LC_ALL=C"],
            ...(superuser ? { superuser: "try" as const } : {}),
        })
        .input(JSON.stringify(params));
}

async function run(command: string, params: object): Promise<string> {
    try {
        return await spawn(command, params, elevated);
    } catch (ex) {
        const error = classify(ex);
        if (error.problem !== "no-access" || elevated) throw error;
    }
    // Not in a role group: an administrator of the machine still gets in, as
    // root, when administrative access is active in the Cockpit session.
    try {
        const output = await spawn(command, params, true);
        elevated = true;
        return output;
    } catch (ex) {
        throw classify(ex);
    }
}

// Calls one backend command. Parameters travel as JSON on stdin, never in argv.
export async function call<T>(command: string, params: object = {}): Promise<T> {
    const output = await run(command, params);
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

// For tests: forget that a previous call needed administrative access.
export function resetAccess(): void {
    elevated = false;
}
