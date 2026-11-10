import cockpit from "cockpit";
import { AccessError, FLEETCTL, FRAME, FleetError, accessFailure, call, isElevated } from "./fleet.ts";

// The store: files on the controller that playbooks copy to nodes. Its files
// are of any size, so they do not travel as the parameters of a call: an
// upload is a channel that streams the bytes after its parameters, a
// download a link the browser saves as it comes.

export type StoreOrigin = "upload" | "run" | "found" | "url";

export interface StoreFile {
    path: string;
    size: number;
    // Null until the tick of the scheduler has taken it.
    sha256: string | null;
    origin: StoreOrigin;
    // The name it was uploaded as, or the run that wrote it.
    source: string;
    updated_by: string | null;
    updated_at: number;
    // False for a file a run cannot be given: not the service account's own.
    usable: boolean;
}

export interface StoreList {
    files: StoreFile[];
    directories: string[];
    // False when the tree had more entries than are listed.
    complete: boolean;
    used: number;
    free: number;
    // Bytes the store may take; 0 for no limit but the disk.
    limit: number;
}

export interface PendingUpload {
    path: string;
    size: number;
    received: number;
    started_at: number;
}

// A download from a URL into the store, as store.downloads gives it.
export interface StoreDownload {
    run_id: string;
    status: "queued" | "running" | "successful" | "failed" | "canceled" | "error";
    url: string;
    path: string;
    sha256: string;
    replace: boolean;
    // What has arrived, and the whole size when the server said it: while it runs.
    received: number | null;
    total: number | null;
    // The size of the file once it is in the store.
    size: number | null;
    error: string;
    created_by: string;
    created_at: number;
    finished_at: number | null;
}

// The name a file downloaded from URL would have: the last part of its path.
export function nameFromUrl(url: string): string {
    try {
        const last = new URL(url).pathname.split("/").filter(Boolean).pop() ?? "";
        return decodeURIComponent(last).replace(/[\\/\x00-\x1f]/g, "");
    } catch {
        return "";
    }
}

export interface Stored {
    path: string;
    size: number;
    sha256: string;
}

// Bytes read from the file at a time, one message each, and sent without an
// acknowledgement at most: the page holds no more than that of a file of any size.
const CHUNK = FRAME;
const WINDOW = 16 * 1024 * 1024;

function fleetctl(command: string): string[] {
    return ["sudo", "-n", "-u", "cockpit-fleet", FLEETCTL, command];
}

// The parameters of store.put: one line, before the bytes.
export function putHeader(path: string, size: number, offset: number, replace: boolean): Uint8Array {
    return new TextEncoder().encode(JSON.stringify({ path, size, offset, replace }) + "\n");
}

export interface UploadOptions {
    // Bytes of an earlier upload of the same file the backend already has.
    offset?: number;
    replace?: boolean;
    onProgress?: (sent: number) => void;
}

export interface Upload {
    done: Promise<Stored>;
    cancel: () => void;
}

// Sends a file to the store. Progress counts what the backend acknowledged.
export function uploadToStore(file: Blob, path: string, options: UploadOptions = {}): Upload {
    const offset = options.offset ?? 0;
    const channel = cockpit.channel({
        payload: "stream",
        spawn: fleetctl("store.put"),
        binary: true,
        "send-acks": "bytes",
        err: "message",
        environ: ["LC_ALL=C"],
        ...(isElevated() ? { superuser: "try" as const } : {}),
    });
    const header = putHeader(path, file.size, offset, options.replace ?? false);
    const output: Uint8Array[] = [];
    let acknowledged = 0;
    let wake: (() => void) | null = null;
    let closed = false;
    // Why the page stopped sending, when it did: the file could not be read.
    let failure: unknown = null;

    channel.addEventListener("message", (_event, data) => output.push(data));
    channel.addEventListener("control", (_event, message) => {
        if (message.command !== "ack") return;
        acknowledged += message.bytes ?? 0;
        options.onProgress?.(Math.min(file.size, offset + Math.max(0, acknowledged - header.length)));
        wake?.();
    });
    const ended = new Promise<Stored>((resolve, reject) => {
        channel.addEventListener("close", (_event, message) => {
            closed = true;
            wake?.();
            const text = new TextDecoder().decode(concat(output));
            let answer: { ok: boolean; data?: Stored; error?: { code: string; message: string } } | null = null;
            try {
                answer = JSON.parse(text);
            } catch {
                answer = null;
            }
            if (failure !== null) reject(new Error(`cannot read the file: ${describe(failure)}`));
            else if (answer?.ok && answer.data) resolve(answer.data);
            else if (answer && !answer.ok) reject(new FleetError(answer.error?.code ?? "internal", answer.error?.message ?? ""));
            else if (message.problem) reject(accessFailure(message.problem, message.message ?? ""));
            else reject(new AccessError("failed", text.slice(0, 200) || message.message || "the upload ended without an answer"));
        });
    });

    async function send() {
        channel.send(header);
        let sent = header.length;
        for (let at = offset; at < file.size && !closed; at += CHUNK) {
            while (sent - acknowledged > WINDOW && !closed) {
                await new Promise<void>(resolve => { wake = resolve; });
                wake = null;
            }
            if (closed) break;
            const piece = new Uint8Array(await file.slice(at, Math.min(at + CHUNK, file.size)).arrayBuffer());
            channel.send(piece);
            sent += piece.length;
        }
        if (!closed) channel.control({ command: "done" });
    }

    send().catch(error => {
        failure = error;
        channel.close("internal-error");
    });
    return { done: ended, cancel: () => channel.close("cancelled") };
}

// What went wrong, as text, whatever was thrown.
export function describe(error: unknown): string {
    if (error instanceof Error) return error.message;
    if (error && typeof error === "object") {
        const { message, problem, name } = error as { message?: unknown; problem?: unknown; name?: unknown };
        for (const value of [message, problem, name]) if (typeof value === "string" && value) return value;
        try {
            return JSON.stringify(error);
        } catch {
            return String(error);
        }
    }
    return String(error);
}

function concat(pieces: Uint8Array[]): Uint8Array {
    const whole = new Uint8Array(pieces.reduce((total, piece) => total + piece.length, 0));
    let at = 0;
    for (const piece of pieces) {
        whole.set(piece, at);
        at += piece.length;
    }
    return whole;
}

function base64(text: string): string {
    let binary = "";
    for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
    return btoa(binary);
}

// The link that has Cockpit run store.read and hand its output to the
// browser as a file to save. The path is the one argument fleetctl takes
// there: a channel opened by a link has no input.
export function downloadUrl(path: string): string {
    const name = path.split("/").pop() || "file";
    const options = {
        payload: "stream",
        binary: "raw",
        spawn: [...fleetctl("store.read"), path],
        environ: ["LC_ALL=C"],
        ...(isElevated() ? { superuser: "try" } : {}),
        external: {
            "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
            "content-type": "application/octet-stream",
        },
    };
    const prefix = new URL(cockpit.transport.uri("channel/" + cockpit.transport.csrf_token)).pathname;
    return `${prefix}?${base64(JSON.stringify(options))}`;
}

// Saves a file of the store. Asked first: once the browser has begun saving,
// a refusal would only be a file with nothing in it.
export async function downloadFromStore(path: string): Promise<StoreFile> {
    const file = await call<StoreFile>("store.get", { path });
    const link = document.createElement("a");
    link.href = downloadUrl(path);
    link.download = path.split("/").pop() || "file";
    document.body.appendChild(link);
    link.click();
    link.remove();
    return file;
}

// What the store holds in one directory, from the list of all of it.
export interface StoreEntry {
    name: string;
    path: string;
    file: StoreFile | null;
}

export function entriesOf(list: StoreList, directory: string): StoreEntry[] {
    const prefix = directory ? directory + "/" : "";
    const direct = (path: string) => path.startsWith(prefix) && !path.slice(prefix.length).includes("/");
    const folders = list.directories.filter(direct).map(path => ({ name: path.slice(prefix.length), path, file: null }));
    const files = list.files.filter(file => direct(file.path)).map(file => ({ name: file.path.slice(prefix.length), path: file.path, file }));
    const byName = (a: StoreEntry, b: StoreEntry) => a.name.localeCompare(b.name);
    return [...folders.sort(byName), ...files.sort(byName)];
}

export function joinPath(directory: string, name: string): string {
    return directory ? `${directory}/${name}` : name;
}
