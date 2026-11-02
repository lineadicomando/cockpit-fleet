import { test } from "node:test";
import assert from "node:assert/strict";
import { effectiveVariables, moved, parseVariables } from "../src/lib/variables.ts";
import type { VariableSet } from "../src/lib/types.ts";

const set = (id: string, name: string, variables: Record<string, unknown>): VariableSet =>
    ({ id, name, description: "", variables, templates: [], updated_at: 0 });

test("sets apply in order, the template's own last", () => {
    const sets = [set("v1", "school", { domain: "a.edu", rooms: 1 }), set("v2", "lab", { rooms: 2, iso: "x" })];
    const got = effectiveVariables(sets, ["v1", "v2"], { iso: "mine" });
    assert.deepEqual(got.map(one => [one.name, one.value, one.from, one.overrides]), [
        ["domain", "a.edu", "school", []],
        ["iso", "mine", null, ["lab"]],
        ["rooms", 2, "lab", ["school"]],
    ]);
    // The order chosen decides; a set not found is left out.
    assert.equal(effectiveVariables(sets, ["v2", "v1", "gone"], {}).find(one => one.name === "rooms")?.from, "school");
});

test("the editor's text is an object or nothing", () => {
    assert.deepEqual(parseVariables("  "), {});
    assert.deepEqual(parseVariables('{"a": 1}'), { a: 1 });
    for (const text of ["[1]", "3", "null", "{", '"x"']) assert.equal(parseVariables(text), null, text);
});

test("an item moves within the list", () => {
    assert.deepEqual(moved(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
    assert.deepEqual(moved(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
    assert.deepEqual(moved(["a", "b"], 0, -1), ["a", "b"]);
});
