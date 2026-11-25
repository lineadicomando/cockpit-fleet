import { test } from "node:test";
import assert from "node:assert/strict";
import { flatOptions, fold, searchGroups } from "../src/lib/search.ts";

const groups = [
    { label: "spalla_info1", options: [
        { id: "a", label: "spalla_info1-ping", description: "school · ping.yaml" },
        { id: "b", label: "spalla_info1-wu-run", description: "school · updates.yaml" },
    ] },
    { label: "spalla_info2", options: [
        { id: "c", label: "spalla_info2-wu-run", description: "school · updates.yaml" },
        { id: "d", label: "Riavvio", keywords: ["perché"] },
    ] },
];

const ids = (found: ReturnType<typeof searchGroups>) => flatOptions(found.groups).map(option => option.id);

test("nothing typed offers everything", () => {
    assert.deepEqual(ids(searchGroups(groups, "  ", 100)), ["a", "b", "c", "d"]);
});

test("every word typed must be found, in any order and anywhere", () => {
    assert.deepEqual(ids(searchGroups(groups, "wu", 100)), ["b", "c"]);
    // One word in the group's label, one in the option's.
    assert.deepEqual(ids(searchGroups(groups, "info2 wu", 100)), ["c"]);
    assert.deepEqual(ids(searchGroups(groups, "run INFO1", 100)), ["b"]);
    // In the description.
    assert.deepEqual(ids(searchGroups(groups, "updates.yaml", 100)), ["b", "c"]);
    assert.deepEqual(ids(searchGroups(groups, "nowhere", 100)), []);
});

test("case and accents do not count, keywords are searched", () => {
    assert.equal(fold("Perché RIAVVIO"), "perche riavvio");
    assert.deepEqual(ids(searchGroups(groups, "PERCHE", 100)), ["d"]);
    assert.deepEqual(ids(searchGroups(groups, "riavvìo", 100)), ["d"]);
});

test("groups with nothing that matches are left out, and the rest beyond the limit counted", () => {
    const found = searchGroups(groups, "wu", 100);
    assert.deepEqual(found.groups.map(group => group.label), ["spalla_info1", "spalla_info2"]);
    const limited = searchGroups(groups, "", 3);
    assert.deepEqual(ids(limited), ["a", "b", "c"]);
    assert.equal(limited.more, 1);
    assert.deepEqual(limited.groups.map(group => group.label), ["spalla_info1", "spalla_info2"]);
    assert.equal(searchGroups(groups, "", 2).groups.length, 1);
});
