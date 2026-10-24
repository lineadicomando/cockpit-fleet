import { test } from "node:test";
import assert from "node:assert/strict";
import { layers, nextStepId } from "../src/lib/workflow.ts";

test("steps go in columns after the longest chain they follow", () => {
    const edges = [
        { from: "a", to: "b", condition: "success" as const },
        { from: "b", to: "d", condition: "success" as const },
        { from: "a", to: "c", condition: "failure" as const },
        { from: "c", to: "d", condition: "always" as const },
        { from: "a", to: "d", condition: "always" as const },
    ];
    assert.deepEqual(layers(["a", "b", "c", "d", "e"], edges), [["a", "e"], ["b", "c"], ["d"]]);
});

test("a new step takes the first id not taken", () => {
    assert.equal(nextStepId([]), "s1");
    assert.equal(nextStepId(["s1", "s3"]), "s2");
});
