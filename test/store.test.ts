import { test } from "node:test";
import assert from "node:assert/strict";
import { channels } from "./mocks/cockpit.ts";
import {
    chosenFolder, describe, downloadUrl, entriesOf, filterEntries, freeName, indexStore, moveInList, nameFromUrl, playbookPath, putHeader,
    rangeBetween, readDropped, readStoreSort, removeFromList, searchStore, settle, sizeOf, sortEntries, uploadToStore, type StoreFile, type StoreList,
    archiveName, archiveUrl, storeChoices, storePathAt,
} from "../src/lib/store.ts";
import { FleetError } from "../src/lib/fleet.ts";

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

function answer(channel: (typeof channels)[number], value: object) {
    channel.emit("message", new TextEncoder().encode(JSON.stringify(value)));
    channel.emit("close", { command: "close", "exit-status": 0 });
}

test("an upload sends its parameters on one line, then the bytes, never in argv", async () => {
    channels.length = 0;
    const data = Uint8Array.from({ length: 3 * 1024 * 1024 + 7 }, (_v, i) => i % 251);
    const progress: number[] = [];
    const upload = uploadToStore(new Blob([data]), "apps/my setup.msi", { onProgress: sent => progress.push(sent) });
    const channel = channels[0];
    assert.deepEqual(channel.options.spawn, ["sudo", "-n", "-u", "cockpit-fleet", "/usr/libexec/cockpit-fleet/fleetctl", "store.put"]);
    assert.equal(channel.options.binary, true);
    assert.equal(channel.options["send-acks"], "bytes");
    while (!channel.controls.length) {
        channel.emit("control", { command: "ack", bytes: 1024 * 1024 });
        await tick();
    }
    assert.deepEqual(channel.controls, [{ command: "done" }]);
    const sent = channel.bytes();
    const line = sent.indexOf(10);
    assert.deepEqual(JSON.parse(new TextDecoder().decode(sent.subarray(0, line))),
                     { path: "apps/my setup.msi", size: data.length, offset: 0, replace: false });
    assert.deepEqual(sent.subarray(line + 1), data);
    // cockpit-ws closes the session on a message over 128 KiB.
    assert.ok(channel.sent.every(piece => piece.length <= 64 * 1024));
    assert.ok(progress.length > 0 && progress.every(value => value <= data.length));
    answer(channel, { ok: true, data: { path: "apps/my setup.msi", size: data.length, sha256: "ab" } });
    assert.equal((await upload.done).sha256, "ab");
});

test("an upload waits for acknowledgements and resumes from an offset", async () => {
    channels.length = 0;
    const data = new Uint8Array(40 * 1024 * 1024);
    const upload = uploadToStore(new Blob([data]), "big.iso", { offset: 2 * 1024 * 1024, replace: true });
    const channel = channels[0];
    for (let i = 0; i < 20; i++) await tick();
    // No more than the window is sent before the backend says it has it.
    const header = channel.sent[0].length;
    assert.ok(channel.bytes().length - header <= 17 * 1024 * 1024, String(channel.bytes().length));
    assert.equal(JSON.parse(new TextDecoder().decode(channel.sent[0])).offset, 2 * 1024 * 1024);
    channel.emit("message", new TextEncoder().encode(JSON.stringify({ ok: false, error: { code: "conflict", message: "full" } })));
    channel.emit("close", { command: "close", "exit-status": 0 });
    await assert.rejects(upload.done, (error: unknown) => error instanceof FleetError && error.code === "conflict");
});

test("a channel refused by sudo is an access problem", async () => {
    channels.length = 0;
    const upload = uploadToStore(new Blob([new Uint8Array(10)]), "a");
    channels[0].emit("close", { command: "close", problem: "internal-error", message: "sudo: a password is required" });
    await assert.rejects(upload.done, (error: unknown) => error instanceof Error && error.name === "AccessError");
});

test("a download is a link to a channel that runs store.read on the path", () => {
    const url = downloadUrl("iso/Windows 11 è.iso");
    const [path, query] = url.split("?");
    assert.equal(path, "/cockpit/channel/token");
    const options = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(query), c => c.charCodeAt(0))));
    assert.deepEqual(options.spawn.slice(-2), ["store.read", "iso/Windows 11 è.iso"]);
    assert.equal(options.binary, "raw");
    assert.equal(options.external["content-disposition"], "attachment; filename*=UTF-8''Windows%2011%20%C3%A8.iso");
});

test("entriesOf lists one directory, folders first", () => {
    const file = (path: string) => ({ path, size: 1, sha256: null, origin: "found" as const, source: "", updated_by: null, updated_at: 0, usable: true });
    const list: StoreList = {
        files: [file("top.msi"), file("apps/b.msi"), file("apps/a.msi"), file("apps/x/deep.msi")],
        directories: ["apps", "apps/x", "iso"], complete: true, used: 4, free: 1, limit: 0,
    };
    assert.deepEqual(entriesOf(list, "").map(e => e.name), ["apps", "iso", "top.msi"]);
    assert.deepEqual(entriesOf(list, "apps").map(e => e.path), ["apps/x", "apps/a.msi", "apps/b.msi"]);
});

test("putHeader is one line of JSON", () => {
    const text = new TextDecoder().decode(putHeader("a\nb", 3, 0, false));
    assert.equal(text.split("\n").length, 2);
});

test("what was thrown is said as text, whatever it is", () => {
    assert.equal(describe(new Error("boom")), "boom");
    assert.equal(describe({ problem: "not-found" }), "not-found");
    assert.equal(describe({ code: 3 }), '{"code":3}');
    assert.equal(describe("plain"), "plain");
});

test("the name of a download is the last part of the URL's path", () => {
    assert.equal(nameFromUrl("https://example.org/dl/LibreOffice%207.6.msi?x=1"), "LibreOffice 7.6.msi");
    assert.equal(nameFromUrl("https://example.org/a/b/"), "b");
    assert.equal(nameFromUrl("https://example.org"), "");
    assert.equal(nameFromUrl("not a url"), "");
    assert.equal(nameFromUrl("https://example.org/a%2Fb%5Cc"), "abc");
});

function stored(path: string, size = 1, updated_at = 0): StoreFile {
    return { path, size, sha256: null, origin: "upload", source: "", updated_by: null, updated_at, usable: true };
}

const STORE: StoreList = {
    files: [stored("win/setup.msi", 30, 3), stored("win/office/office.iso", 500, 1), stored("readme.txt", 2, 9), stored("win/agent.exe", 10, 5)],
    directories: ["empty", "win", "win/office"],
    complete: true, used: 542, free: 1000, limit: 0,
};

test("indexStore gives each folder its entries, as entriesOf does", () => {
    const index = indexStore(STORE);
    for (const directory of ["", "win", "win/office", "empty"]) assert.deepEqual(index.get(directory), entriesOf(STORE, directory).sort((a, b) => a.name.localeCompare(b.name)), directory);
    assert.deepEqual(index.get("empty"), []);
});

test("sortEntries keeps folders first and sorts files by the key asked", () => {
    const entries = indexStore(STORE).get("win")!;
    assert.deepEqual(sortEntries(entries, { by: "name", ascending: true }).map(entry => entry.name), ["office", "agent.exe", "setup.msi"]);
    assert.deepEqual(sortEntries(entries, { by: "size", ascending: false }).map(entry => entry.name), ["office", "setup.msi", "agent.exe"]);
    assert.deepEqual(sortEntries(entries, { by: "updated", ascending: true }).map(entry => entry.name), ["office", "setup.msi", "agent.exe"]);
    assert.deepEqual(readStoreSort(null), { by: "name", ascending: true });
    assert.deepEqual(readStoreSort('{"by":"size","ascending":false}'), { by: "size", ascending: false });
    assert.deepEqual(readStoreSort('{"by":"x"}'), { by: "name", ascending: true });
});

test("filterEntries and searchStore find by name, the best matches first", () => {
    assert.deepEqual(filterEntries(indexStore(STORE).get("win")!, "SET").map(entry => entry.name), ["setup.msi"]);
    const found = searchStore(STORE, "off", 10);
    assert.deepEqual(found.entries.map(entry => entry.path), ["win/office", "win/office/office.iso"]);
    assert.deepEqual(searchStore(STORE, "win", 1), { entries: [{ name: "win", path: "win", file: null }], more: 4 });
    assert.deepEqual(searchStore(STORE, " ", 10).entries, []);
});

test("sizeOf counts the files inside folders", () => {
    assert.deepEqual(sizeOf(STORE, ["win/office", "readme.txt"]), { files: 2, bytes: 502 });
    assert.deepEqual(sizeOf(STORE, ["empty"]), { files: 0, bytes: 0 });
});

test("freeName adds a number before the extension", () => {
    assert.equal(freeName("a.iso", new Set()), "a.iso");
    assert.equal(freeName("a.iso", new Set(["a.iso"])), "a (1).iso");
    assert.equal(freeName("a.iso", new Set(["a.iso", "a (1).iso"])), "a (2).iso");
    assert.equal(freeName("x.tar.gz", new Set(["x.tar.gz"])), "x (1).tar.gz");
    assert.equal(freeName("a.b.c", new Set(["a.b.c"])), "a.b (1).c");
    assert.equal(freeName(".bashrc", new Set([".bashrc"])), ".bashrc (1)");
    assert.equal(freeName("folder", new Set(["folder"])), "folder (1)");
});

test("settle replaces, renames or leaves out what clashes", () => {
    const taken = new Set(["win/setup.msi", "win"]);
    const items = [{ path: "win/setup.msi" }, { path: "win/new.msi" }];
    assert.deepEqual(settle(items, taken, "replace"), [{ path: "win/setup.msi", replace: true }, { path: "win/new.msi", replace: false }]);
    assert.deepEqual(settle(items, taken, "skip"), [{ path: "win/new.msi", replace: false }]);
    assert.deepEqual(settle([...items, { path: "win/setup.msi" }], taken, "keep").map(item => item.path),
        ["win/setup (1).msi", "win/new.msi", "win/setup (2).msi"]);
});

test("moveInList and removeFromList show a change before the backend answers", () => {
    const moved = moveInList(STORE, [{ path: "win/office", to: "iso/office" }, { path: "readme.txt", to: "doc/readme.txt" }]);
    assert.deepEqual(moved.files.map(file => file.path).sort(), ["doc/readme.txt", "iso/office/office.iso", "win/agent.exe", "win/setup.msi"]);
    assert.deepEqual(moved.directories, ["doc", "empty", "iso", "iso/office", "win"]);
    const removed = removeFromList(STORE, ["win/office", "readme.txt"]);
    assert.deepEqual(removed.files.map(file => file.path), ["win/setup.msi", "win/agent.exe"]);
    assert.deepEqual(removed.directories, ["empty", "win"]);
});

test("rangeBetween selects what is between two entries, both ways", () => {
    assert.deepEqual(rangeBetween(["a", "b", "c", "d"], "c", "a"), ["a", "b", "c"]);
    assert.deepEqual(rangeBetween(["a", "b"], "x", "b"), ["b"]);
});

test("readDropped reads folders whole, a hundred entries at a time", async () => {
    const file = (name: string) => ({ isFile: true, isDirectory: false, name, file: (ok: (file: File) => void) => ok(new File(["x"], name)) });
    const folder = (name: string, children: object[]) => ({
        isFile: false, isDirectory: true, name,
        createReader: () => {
            let given = false;
            return { readEntries: (ok: (entries: object[]) => void) => { ok(given ? [] : children); given = true; } };
        },
    });
    const dropped = await readDropped([file("a.txt"), folder("iso", [file("b.iso"), folder("empty", []), folder("deep", [file("c")])])] as never);
    assert.deepEqual(dropped.files.map(item => item.path), ["a.txt", "iso/b.iso", "iso/deep/c"]);
    assert.deepEqual(dropped.directories, ["iso/empty"]);
    const chosen = chosenFolder([Object.assign(new File(["x"], "b.iso"), {}), new File(["y"], "c")]);
    assert.deepEqual(chosen.files.map(item => item.path), ["b.iso", "c"]);
});

test("playbookPath names a file as a playbook does", () => {
    assert.equal(playbookPath("win/setup.msi"), "{{ fleet_store }}/win/setup.msi");
});

test("storePathAt finds a path being typed after {{ fleet_store }}/", () => {
    assert.deepEqual(storePathAt('    src: "{{ fleet_store }}/'), { folder: "", partial: "" });
    assert.deepEqual(storePathAt("    src: '{{fleet_store}}/win/office/off"), { folder: "win/office", partial: "off" });
    assert.deepEqual(storePathAt("src: {{- fleet_store -}}/win/"), { folder: "win", partial: "" });
    assert.equal(storePathAt('src: "{{ fleet_store }}/win/a.msi" '), null);
    assert.equal(storePathAt("src: {{ other }}/win"), null);
    assert.equal(storePathAt("src: {{ fleet_store }}"), null);
});

test("storeChoices offers a folder's entries, folders first with a slash", () => {
    assert.deepEqual(storeChoices(STORE, "", ""), [
        { label: "empty/", folder: true }, { label: "win/", folder: true }, { label: "readme.txt", folder: false, size: 2 },
    ]);
    assert.deepEqual(storeChoices(STORE, "win", "S").map(choice => choice.label), ["setup.msi"]);
    assert.deepEqual(storeChoices(STORE, "nowhere", ""), []);
});

test("an archive is streamed by store.archive, the paths in argv, under a name that says what it holds", () => {
    const url = archiveUrl(["win", "readme.txt"], "store.tar");
    const options = JSON.parse(Buffer.from(url.split("?")[1], "base64").toString());
    assert.deepEqual(options.spawn.slice(-3), ["store.archive", "win", "readme.txt"]);
    assert.equal(options.external["content-type"], "application/x-tar");
    assert.equal(archiveName(["win/office"], "win"), "office.tar");
    assert.equal(archiveName(["a", "b"], "win/office"), "office.tar");
    assert.equal(archiveName(["a", "b"], ""), "store.tar");
});
