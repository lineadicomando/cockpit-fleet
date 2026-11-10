import { test } from "node:test";
import assert from "node:assert/strict";
import { channels } from "./mocks/cockpit.ts";
import { describe, downloadUrl, entriesOf, nameFromUrl, putHeader, uploadToStore, type StoreList } from "../src/lib/store.ts";
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
