import { test } from "node:test";
import assert from "node:assert/strict";
import {
    anyDay, controllerTrouble, countTicks, dayTicks, formatRate, readWindow, stateSlices, topSystems, totalOf, tzOffset,
} from "../src/lib/overview.ts";

test("the window is one of those offered", () => {
    assert.equal(readWindow("7"), 7);
    assert.equal(readWindow("30"), 30);
    assert.equal(readWindow(null), 14);
    assert.equal(readWindow("90"), 14);
    assert.equal(readWindow("x"), 14);
});

test("the offset is minutes east of UTC", () => {
    const date = new Date();
    assert.equal(tzOffset(date), -date.getTimezoneOffset());
});

test("a rate below one never shows as all", () => {
    assert.equal(formatRate(null, "en"), "–");
    assert.equal(formatRate(1, "en"), "100%");
    assert.equal(formatRate(0.9999, "en"), "99.9%");
    assert.equal(formatRate(0.5, "en"), "50%");
    assert.equal(formatRate(0, "en"), "0%");
});

test("slices go worst first and leave out the empty states", () => {
    const states = { ok: 10, warning: 2, critical: 1, unknown: 0, pending: 3 };
    assert.deepEqual(stateSlices(states).map(slice => slice.state), ["critical", "warning", "pending", "ok"]);
    assert.equal(totalOf(states), 16);
    assert.deepEqual(stateSlices({ ok: 0, warning: 0, critical: 0, unknown: 0, pending: 0 }), []);
});

test("an empty window has nothing to draw", () => {
    const days = [{ start: 0, problems: 0, recoveries: 0 }, { start: 86400, problems: 0, recoveries: 1 }];
    assert.equal(anyDay(days, ["problems", "recoveries"]), true);
    assert.equal(anyDay(days, ["problems"]), false);
});

test("the ticks of the days always include today", () => {
    const starts = (n: number) => Array.from({ length: n }, (_value, index) => index * 86400);
    assert.deepEqual(dayTicks(starts(7)), starts(7));
    const fourteen = dayTicks(starts(14));
    assert.equal(fourteen.length, 7);
    assert.equal(fourteen[fourteen.length - 1], 13 * 86400);
    const thirty = dayTicks(starts(30));
    assert.equal(thirty[thirty.length - 1], 29 * 86400);
    assert.equal(thirty.length, 6);
});

test("systems beyond the first ones are folded, the unknown apart", () => {
    const systems = [
        { name: "Debian 13", hosts: 9 }, { name: "", hosts: 4 }, { name: "AlmaLinux 10", hosts: 3 },
        { name: "Windows 11", hosts: 2 }, { name: "Fedora 44", hosts: 1 },
    ];
    assert.deepEqual(topSystems(systems, 2), [
        { name: "Debian 13", hosts: 9 }, { name: "AlmaLinux 10", hosts: 3 },
        { name: "", hosts: 3, other: true }, { name: "", hosts: 4 },
    ]);
    assert.deepEqual(topSystems(systems.slice(0, 1)), [{ name: "Debian 13", hosts: 9 }]);
});

test("the controller is in trouble when it falls behind or cannot deliver", () => {
    const fine = { behind: 0, unprepared: 0, notifications: { failed: 0, pending: 5 } };
    assert.equal(controllerTrouble(fine), false);
    assert.equal(controllerTrouble({ ...fine, behind: 1 }), true);
    assert.equal(controllerTrouble({ ...fine, notifications: { failed: 1, pending: 0 } }), true);
});

test("a count axis has whole steps and reaches the highest value", () => {
    assert.deepEqual(countTicks(0), [0, 1]);
    assert.deepEqual(countTicks(1), [0, 1]);
    assert.deepEqual(countTicks(3), [0, 1, 2, 3]);
    assert.deepEqual(countTicks(8), [0, 2, 4, 6, 8]);
    assert.deepEqual(countTicks(9), [0, 3, 6, 9]);
    assert.deepEqual(countTicks(41), [0, 11, 22, 33, 44]);
});
