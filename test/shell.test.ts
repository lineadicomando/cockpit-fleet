import { test } from "node:test";
import assert from "node:assert/strict";
import { shellPage } from "../src/lib/shell.ts";

const frame = "/cockpit/@localhost/fleet/index.html";

test("another page of the shell is beside this one, on the same host and root", () => {
    assert.equal(shellPage("/fleet", frame, "/system/terminal"), "/system/terminal");
    assert.equal(shellPage("/@node2/fleet", "/cockpit/@node2/fleet/index.html", "/system/terminal"), "/@node2/system/terminal");
    assert.equal(shellPage("/root/fleet", "/root/cockpit/@localhost/fleet/index.html", "/system/terminal"), "/root/system/terminal");
    assert.equal(shellPage("/fleet/", frame, "/system/terminal"), "/system/terminal");
});

test("outside the shell there is no address", () => {
    assert.equal(shellPage(frame, frame, "/system/terminal"), "");
    assert.equal(shellPage("/system", frame, "/system/terminal"), "");
    assert.equal(shellPage("/fleet", "index.html", "/system/terminal"), "");
});
