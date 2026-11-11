import cockpit from "cockpit";
import { basename, dirname, isInside, renamed } from "./files.ts";
import { AccessError, FLEETCTL, FRAME, FleetError, accessFailure, call, isElevated } from "./fleet.ts";

// The store: files on the controller that playbooks copy to nodes. Its files
// are of any size, so they do not travel as the parameters of a call: an
// upload is a channel that streams the bytes after its parameters, a
// download a link the browser saves as it comes.

export type StoreOrigin = "upload" | "run" | "found" | "url" | "copy";

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
    // Of a file downloaded from a URL: where from, and how checking it goes.
    refresh?: StoreSource | null;
}

export type Every = "never" | "daily" | "weekly" | "monthly";
export const EVERY: Every[] = ["never", "daily", "weekly", "monthly"];

export interface StoreSource {
    url: string;
    every: Every;
    checked_at: number | null;
    changed_at: number | null;
    error: string;
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
    // Changes with the listing; given back as since, it may be answered with StoreUnchanged.
    token?: string;
}

export interface StoreUnchanged {
    unchanged: true;
    token: string;
    free: number;
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
    // A download from a URL, or a copy within the store too large to make at once.
    kind?: "download" | "copy";
    // Of a copy: the first path copied, and how many were.
    source?: string;
    count?: number;
    // Of a download: how often the file is checked afterwards; whether this
    // was a check of a file the store had, which found it unchanged; and the
    // bytes one that ended short left, to be taken up.
    every?: Every;
    refresh?: boolean;
    unchanged?: boolean;
    partial?: number;
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

// The link that has Cockpit run a command of fleetctl that streams out and
// hand its output to the browser as a file to save. The paths are the
// arguments fleetctl takes there: a channel opened by a link has no input.
function streamUrl(command: string, paths: string[], name: string, type: string): string {
    const options = {
        payload: "stream",
        binary: "raw",
        spawn: [...fleetctl(command), ...paths],
        environ: ["LC_ALL=C"],
        ...(isElevated() ? { superuser: "try" } : {}),
        external: {
            "content-disposition": `attachment; filename*=UTF-8''${encodeURIComponent(name)}`,
            "content-type": type,
        },
    };
    const prefix = new URL(cockpit.transport.uri("channel/" + cockpit.transport.csrf_token)).pathname;
    return `${prefix}?${base64(JSON.stringify(options))}`;
}

export function downloadUrl(path: string): string {
    return streamUrl("store.read", [path], path.split("/").pop() || "file", "application/octet-stream");
}

// The paths travel in the link: a long one is refused by the web server on
// its way, so a selection of many is archived through its folder instead.
export const ARCHIVE_LINK = 6000;

export function archiveUrl(paths: string[], name: string): string {
    return streamUrl("store.archive", paths, name, "application/x-tar");
}

// The name of the tar of PATHS: the folder's own name when it is one.
export function archiveName(paths: string[], directory: string): string {
    if (paths.length === 1) return `${basename(paths[0])}.tar`;
    return `${basename(directory) || "store"}.tar`;
}

function save(href: string, name: string) {
    const link = document.createElement("a");
    link.href = href;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
}

// Saves files and folders of the store as one tar. Asked first whether they
// are all there: once the browser has begun saving, an error would only be
// a file with nothing in it.
export async function downloadArchive(paths: string[], name: string): Promise<void> {
    const url = archiveUrl(paths, name);
    if (url.length > ARCHIVE_LINK) throw new Error("too many entries for one archive: download their folder");
    const list = await call<StoreList>("store.list");
    const there = new Set([...list.files.map(file => file.path), ...list.directories]);
    const missing = paths.find(path => !there.has(path));
    if (missing) throw new Error(`no such file or directory in the store: ${missing}`);
    save(url, name);
}

// Saves a file of the store. Asked first: once the browser has begun saving,
// a refusal would only be a file with nothing in it.
export async function downloadFromStore(path: string): Promise<StoreFile> {
    const file = await call<StoreFile>("store.get", { path });
    save(downloadUrl(path), path.split("/").pop() || "file");
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

// The entries of every directory of the store, each list sorted by name:
// built once per listing, not each time a folder is drawn.
export function indexStore(list: StoreList): Map<string, StoreEntry[]> {
    const index = new Map<string, StoreEntry[]>([["", []]]);
    const into = (directory: string) => {
        let entries = index.get(directory);
        if (!entries) index.set(directory, entries = []);
        return entries;
    };
    for (const path of list.directories) {
        into(path);
        into(dirname(path)).push({ name: basename(path), path, file: null });
    }
    for (const file of list.files) into(dirname(file.path)).push({ name: basename(file.path), path: file.path, file });
    for (const entries of index.values()) entries.sort((a, b) => a.name.localeCompare(b.name));
    return index;
}

export type StoreSortKey = "name" | "size" | "updated";

export interface StoreSort {
    by: StoreSortKey;
    ascending: boolean;
}

export function readStoreSort(text: string | null): StoreSort {
    try {
        const value = JSON.parse(text ?? "") as Partial<StoreSort>;
        if (value.by === "name" || value.by === "size" || value.by === "updated") return { by: value.by, ascending: value.ascending !== false };
    } catch {
        // what was stored is not ours: the default
    }
    return { by: "name", ascending: true };
}

// Folders always first, by name; files by the key asked, then by name.
export function sortEntries(entries: StoreEntry[], sort: StoreSort): StoreEntry[] {
    const sign = sort.ascending ? 1 : -1;
    const key = (entry: StoreEntry) => (sort.by === "size" ? entry.file?.size ?? 0 : entry.file?.updated_at ?? 0);
    return [...entries].sort((a, b) => {
        if (!a.file !== !b.file) return a.file ? 1 : -1;
        if (sort.by === "name" || !a.file) return sign * a.name.localeCompare(b.name);
        return sign * (key(a) - key(b)) || a.name.localeCompare(b.name);
    });
}

// The entries of a folder whose name has QUERY in it, ignoring case.
export function filterEntries(entries: StoreEntry[], query: string): StoreEntry[] {
    const wanted = query.trim().toLowerCase();
    return wanted ? entries.filter(entry => entry.name.toLowerCase().includes(wanted)) : entries;
}

// Files and folders anywhere in the store whose path has QUERY in it: those
// whose name starts with it first, then those whose name has it.
export function searchStore(list: StoreList, query: string, limit: number): { entries: StoreEntry[]; more: number } {
    const wanted = query.trim().toLowerCase();
    if (!wanted) return { entries: [], more: 0 };
    const all: StoreEntry[] = [
        ...list.directories.map(path => ({ name: basename(path), path, file: null })),
        ...list.files.map(file => ({ name: basename(file.path), path: file.path, file })),
    ];
    const rank = (entry: StoreEntry) => {
        const name = entry.name.toLowerCase();
        if (name.startsWith(wanted)) return 0;
        if (name.includes(wanted)) return 1;
        return entry.path.toLowerCase().includes(wanted) ? 2 : -1;
    };
    const found = all.map(entry => ({ entry, rank: rank(entry) }))
        .filter(item => item.rank >= 0)
        .sort((a, b) => a.rank - b.rank || a.entry.path.localeCompare(b.entry.path))
        .map(item => item.entry);
    return { entries: found.slice(0, limit), more: Math.max(0, found.length - limit) };
}

// How many files PATHS hold, folders counted with what is in them, and their bytes.
export function sizeOf(list: StoreList, paths: string[]): { files: number; bytes: number } {
    const inside = list.files.filter(file => paths.some(path => isInside(file.path, path)));
    return { files: inside.length, bytes: inside.reduce((total, file) => total + file.size, 0) };
}

// A name not in TAKEN: "setup.msi" becomes "setup (1).msi", then "(2)"...
export function freeName(name: string, taken: Set<string>): string {
    if (!taken.has(name)) return name;
    const match = /^(.+?)((?:\.tar)?\.[^./]+)?$/.exec(name);
    const stem = match?.[1] ?? name;
    const extension = match?.[2] ?? "";
    for (let number = 1; ; number++) {
        const candidate = `${stem} (${number})${extension}`;
        if (!taken.has(candidate)) return candidate;
    }
}

// How a playbook names a file of the store.
export function playbookPath(path: string): string {
    return `{{ fleet_store }}/${path}`;
}

// Where a path of the store is being typed after {{ fleet_store }}/ on a
// line: the folders written so far, and the part of a name after them.
export function storePathAt(line: string): { folder: string; partial: string } | null {
    const found = /\{\{-?\s*fleet_store\s*-?\}\}\/((?:[^\s"'{}/]+\/)*)([^\s"'{}/]*)$/.exec(line);
    if (!found) return null;
    return { folder: found[1].replace(/\/$/, ""), partial: found[2] };
}

// What the store holds in FOLDER whose name starts with PARTIAL (ignoring
// case): folders first, with a / that goes on to their content.
export function storeChoices(list: StoreList, folder: string, partial: string): { label: string; folder: boolean; size?: number }[] {
    const wanted = partial.toLowerCase();
    return (indexStore(list).get(folder) ?? [])
        .filter(entry => entry.name.toLowerCase().startsWith(wanted))
        .sort((a, b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name))
        .map(entry => (entry.file ? { label: entry.name, folder: false, size: entry.file.size } : { label: `${entry.name}/`, folder: true }));
}

// The store as it will be once MOVES are done: shown before the backend
// has answered, and replaced by its listing as soon as it comes.
export function moveInList(list: StoreList, moves: { path: string; to: string }[]): StoreList {
    const move = (path: string) => {
        for (const { path: from, to } of moves) {
            const next = renamed(path, from, to);
            if (next !== null) return next;
        }
        return path;
    };
    const directories = new Set(list.directories.map(move));
    // The folders a move goes into are made on the way.
    for (const { to } of moves) {
        for (let parent = dirname(to); parent; parent = dirname(parent)) directories.add(parent);
    }
    return {
        ...list,
        files: list.files.map(file => ({ ...file, path: move(file.path) })),
        directories: [...directories].sort(),
    };
}

export function removeFromList(list: StoreList, paths: string[]): StoreList {
    const gone = (path: string) => paths.some(removed => isInside(path, removed));
    return { ...list, files: list.files.filter(file => !gone(file.path)), directories: list.directories.filter(path => !gone(path)) };
}

// The entries from one to the other in the order shown, both included:
// what a click with Shift selects.
export function rangeBetween(order: string[], from: string, to: string): string[] {
    const start = order.indexOf(from);
    const end = order.indexOf(to);
    if (start < 0 || end < 0) return [to];
    return order.slice(Math.min(start, end), Math.max(start, end) + 1);
}

// A file to upload with its path below the folder it is dropped on, and
// the folders to make even with nothing in them.
export interface Dropped {
    files: { file: File; path: string }[];
    directories: string[];
}

// What can be read of a dropped entry: the parts of FileSystemEntry used here.
interface EntryLike {
    isFile: boolean;
    isDirectory: boolean;
    name: string;
    file?: (success: (file: File) => void, failure?: (error: unknown) => void) => void;
    createReader?: () => { readEntries: (success: (entries: EntryLike[]) => void, failure?: (error: unknown) => void) => void };
}

// The files and folders dropped from the desktop, folders read whole with
// the paths inside them. A reader gives its entries a hundred at a time.
export async function readDropped(entries: EntryLike[], prefix = ""): Promise<Dropped> {
    const dropped: Dropped = { files: [], directories: [] };
    for (const entry of entries) {
        const path = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (entry.isFile && entry.file) {
            const file = await new Promise<File>((resolve, reject) => entry.file!(resolve, reject));
            dropped.files.push({ file, path });
        } else if (entry.isDirectory && entry.createReader) {
            const reader = entry.createReader();
            const children: EntryLike[] = [];
            for (;;) {
                const batch = await new Promise<EntryLike[]>((resolve, reject) => reader.readEntries(resolve, reject));
                if (!batch.length) break;
                children.push(...batch);
            }
            const inside = await readDropped(children, path);
            if (!inside.files.length) dropped.directories.push(path);
            dropped.files.push(...inside.files);
            dropped.directories.push(...inside.directories);
        }
    }
    return dropped;
}

// What a drop carries: entries when the browser gives them (folders then
// keep what is in them), the plain files otherwise.
export async function droppedOf(transfer: DataTransfer): Promise<Dropped> {
    const entries = Array.from(transfer.items ?? [])
        .filter(item => item.kind === "file")
        .map(item => item.webkitGetAsEntry?.() as EntryLike | null)
        .filter((entry): entry is EntryLike => !!entry);
    if (entries.length) return readDropped(entries);
    return { files: Array.from(transfer.files).map(file => ({ file, path: file.name })), directories: [] };
}

// The files of a folder chosen with the browser's chooser of folders.
export function chosenFolder(files: File[]): Dropped {
    return { files: files.map(file => ({ file, path: file.webkitRelativePath || file.name })), directories: [] };
}

export type Clash = "replace" | "keep" | "skip";

// Where each item goes when some of their paths are taken: replaced (only
// files can be), under a free name, or left out. TAKEN are the paths of
// the store; the new names do not take one another's either.
export function settle<T extends { path: string }>(items: T[], taken: Set<string>, clash: Clash): (T & { replace: boolean })[] {
    const used = new Set(taken);
    const settled: (T & { replace: boolean })[] = [];
    for (const item of items) {
        if (!used.has(item.path)) {
            used.add(item.path);
            settled.push({ ...item, replace: false });
        } else if (clash === "replace") {
            settled.push({ ...item, replace: true });
        } else if (clash === "keep") {
            const directory = dirname(item.path);
            const siblings = new Set([...used].filter(path => dirname(path) === directory).map(basename));
            const path = joinPath(directory, freeName(basename(item.path), siblings));
            used.add(path);
            settled.push({ ...item, path, replace: false });
        }
    }
    return settled;
}
