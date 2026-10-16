import { test } from "node:test";
import assert from "node:assert/strict";
import { launchCommand, runCommand, shellWord, uploadCommand } from "../src/lib/terminal.ts";

test("a word is quoted only when the shell needs it", () => {
    assert.equal(shellWord("web01"), "web01");
    assert.equal(shellWord("greeting=ciao"), "greeting=ciao");
    assert.equal(shellWord("Say hello"), "'Say hello'");
    assert.equal(shellWord("it's"), "'it'\\''s'");
    assert.equal(shellWord("$(id)"), "'$(id)'");
    assert.equal(shellWord("all,!web03"), "'all,!web03'");
    assert.equal(shellWord(""), "''");
});

test("a launch says what the form says, secrets left to the terminal", () => {
    assert.equal(launchCommand("Say hello", { greeting: "ciao a tutti", count: 3, empty: "", hosts: ["a", "b"] }, " web01 ", true),
        "fleet launch 'Say hello' -a 'greeting=ciao a tutti' -a count=3 -a hosts=a,b --limit web01 --check");
    assert.equal(launchCommand("deploy", {}, "", false), "fleet launch deploy");
});

test("a run is followed while it goes on, read once it ended", () => {
    assert.equal(runCommand("r1", true), "fleet log -f r1");
    assert.equal(runCommand("r1", false), "fleet result r1");
    assert.equal(uploadCommand("shipped", "site v2.tar.gz"), "fleet project upload shipped --archive 'site v2.tar.gz'");
    assert.equal(uploadCommand("shipped", ""), "fleet project upload shipped --archive ARCHIVE.tar.gz");
});
