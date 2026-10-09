import { test } from "node:test";
import assert from "node:assert/strict";
import { parseAnsi } from "../src/lib/ansi.ts";
import { formatDuration, runDuration } from "../src/lib/format.ts";

test("parseAnsi splits text by colour and weight", () => {
    assert.deepEqual(parseAnsi("PLAY \x1b[0;32mok: [a]\x1b[0m done"), [
        { text: "PLAY ", color: "", bold: false },
        { text: "ok: [a]", color: "green", bold: false },
        { text: " done", color: "", bold: false },
    ]);
    assert.deepEqual(parseAnsi("\x1b[1;31mfatal\x1b[0m"), [{ text: "fatal", color: "red", bold: true }]);
    assert.deepEqual(parseAnsi("\x1b[1;35m[WARNING]\x1b[0m"), [{ text: "[WARNING]", color: "magenta", bold: true }]);
    assert.deepEqual(parseAnsi("\x1b[91mx"), [{ text: "x", color: "bright-red", bold: false }]);
});

test("parseAnsi merges neighbours and drops empty runs", () => {
    assert.deepEqual(parseAnsi("a\x1b[0mb\x1b[0m"), [{ text: "ab", color: "", bold: false }]);
    assert.deepEqual(parseAnsi(""), []);
    assert.deepEqual(parseAnsi("\x1b[0;33m\x1b[0m"), []);
});

test("parseAnsi leaves text that is not a colour code alone", () => {
    assert.deepEqual(parseAnsi("<b>&amp;</b> [0;32m"), [{ text: "<b>&amp;</b> [0;32m", color: "", bold: false }]);
});

test("formatDuration is precise when short and rounded when long", () => {
    assert.equal(formatDuration(0), "0 s");
    assert.equal(formatDuration(42.4), "42 s");
    assert.equal(formatDuration(185), "3 min 05 s");
    assert.equal(formatDuration(7620), "2 h 07 min");
    assert.equal(formatDuration(-5), "0 s");
});

test("runDuration uses the current time for a run still going", () => {
    assert.equal(runDuration({ started_at: null, finished_at: null }, 100), "");
    assert.equal(runDuration({ started_at: 100, finished_at: null }, 130), "30 s");
    assert.equal(runDuration({ started_at: 100, finished_at: 110 }, 999), "10 s");
});

import { addEvents, changesByHost, countOutcomes, diffLines } from "../src/lib/events.ts";

const line = (event: object) => `${JSON.stringify(event)}\n`;

test("addEvents groups host results under their task", () => {
    const text =
        line({ event: "play_start", play: "Web" }) +
        line({ event: "task_start", task: "Install", task_uuid: "t1" }) +
        line({ event: "host_ok", host: "a", task_uuid: "t1", changed: true }) +
        line({ event: "host_ok", host: "b", task_uuid: "t1", changed: false }) +
        line({ event: "host_failed", host: "c", task_uuid: "t1", result: { msg: "no such package" } }) +
        line({ event: "host_unreachable", host: "d", task_uuid: "t1", result: { msg: { reason: "timeout" } } }) +
        line({ event: "host_skipped", host: "e", task_uuid: "t1" });
    const { tasks, play } = addEvents([], text);
    assert.equal(play, "Web");
    assert.equal(tasks.length, 1);
    assert.deepEqual(tasks[0].hosts, { a: "changed", b: "ok", c: "failed", d: "unreachable", e: "skipped" });
    assert.deepEqual(tasks[0].messages, { c: "no such package", d: '{"reason":"timeout"}' });
    assert.deepEqual(countOutcomes(tasks[0]), { ok: 1, changed: 1, failed: 1, unreachable: 1, skipped: 1 });
});

test("addEvents reports which element of a loop failed", () => {
    const text =
        line({ event: "task_start", task: "Install", task_uuid: "t1" }) +
        line({ event: "item_ok", host: "a", task_uuid: "t1", item: "vim" }) +
        line({ event: "item_failed", host: "a", task_uuid: "t1", item: "nope", msg: "no package" }) +
        line({ event: "host_failed", host: "a", task_uuid: "t1", result: { msg: "One or more items failed" } });
    const { tasks } = addEvents([], text);
    assert.deepEqual(tasks[0].hosts, { a: "failed" });
    assert.deepEqual(tasks[0].messages, { "a [nope]": "no package", a: "One or more items failed" });
});

test("addEvents continues across chunks and ignores what it cannot read", () => {
    const first = addEvents([], line({ event: "play_start", play: "P" }) + line({ event: "task_start", task: "A", task_uuid: "t1" }));
    const second = addEvents(first.tasks, `not json\n${line({ event: "host_ok", host: "a", task_uuid: "t1" })}${line({ event: "host_ok", host: "x", task_uuid: "unknown" })}${line({ event: "task_start", task: "B", task_uuid: "t2" })}`, first.play);
    assert.deepEqual(second.tasks.map(task => [task.name, task.play, Object.keys(task.hosts)]), [["A", "P", ["a"]], ["B", "P", []]]);
    // The earlier result is not modified in place.
    assert.deepEqual(first.tasks[0].hosts, {});
});

test("changesByHost collects the diffs of every task under their host", () => {
    const text =
        line({ event: "play_start", play: "Web" }) +
        line({ event: "task_start", task: "Config", task_uuid: "t1" }) +
        line({ event: "host_ok", host: "b", task_uuid: "t1", changed: true, diff: "--- before\n+++ after\n" }) +
        line({ event: "host_ok", host: "a", task_uuid: "t1", changed: false }) +
        line({ event: "task_start", task: "Lines", task_uuid: "t2" }) +
        line({ event: "item_ok", host: "a", task_uuid: "t2", item: "one", changed: true, diff: "-x\n+y\n", diff_cut: true }) +
        line({ event: "item_ok", host: "a", task_uuid: "t2", item: "two", changed: false }) +
        line({ event: "host_ok", host: "a", task_uuid: "t2", changed: true }) +
        line({ event: "host_failed", host: "b", task_uuid: "t2", diff: "-p\n+q\n", result: { msg: "no" } });
    const { tasks } = addEvents([], text);
    assert.deepEqual(changesByHost(tasks), [
        { host: "a", changes: [{ host: "a", item: "one", text: "-x\n+y\n", cut: true, task: "Lines", play: "Web" }] },
        { host: "b", changes: [
            { host: "b", item: "", text: "--- before\n+++ after\n", cut: false, task: "Config", play: "Web" },
            { host: "b", item: "", text: "-p\n+q\n", cut: false, task: "Lines", play: "Web" },
        ] },
    ]);
    assert.deepEqual(tasks[1].messages, { b: "no" });
    assert.deepEqual(changesByHost([]), []);
});

test("diffLines tells the kinds of line of a unified diff", () => {
    const text = "--- before: /etc/motd\n+++ after: /etc/motd\n@@ -1,2 +1,2 @@\n same\n-old\n+new\n\\ No newline at end of file\n";
    assert.deepEqual(diffLines(text).map(entry => entry.kind),
        ["header", "header", "hunk", "context", "removed", "added", "context"]);
    assert.equal(diffLines("-- not a header\n")[0].kind, "removed");
});
