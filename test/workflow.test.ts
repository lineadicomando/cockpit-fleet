import { test } from "node:test";
import assert from "node:assert/strict";
import { inScope, layers, nextStepId, templateGroups } from "../src/lib/workflow.ts";

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

test("a workflow of a project or an inventory takes only their templates", () => {
    const template = { project_id: "p1", inventory_id: "i1" };
    assert.ok(inScope(template, { project_id: "", inventory_id: "" }));
    assert.ok(inScope(template, { project_id: "p1", inventory_id: "" }));
    assert.ok(inScope(template, { project_id: "p1", inventory_id: "i1" }));
    assert.ok(!inScope(template, { project_id: "p1", inventory_id: "i2" }));
    assert.ok(!inScope(template, { project_id: "p2", inventory_id: "" }));
});

test("the templates a step may take go by inventory, by name in each", () => {
    const inventories = new Map([["i1", "lab2"], ["i2", "lab10"]]);
    const projects = new Map([["p1", "school"], ["p2", "office"]]);
    const templates = [
        { id: "t1", name: "shutdown", project_id: "p1", inventory_id: "i2" },
        { id: "t2", name: "ping", project_id: "p1", inventory_id: "i1" },
        { id: "t3", name: "ping", project_id: "p2", inventory_id: "i1" },
        { id: "t4", name: "wol", project_id: "p1", inventory_id: "i1" },
    ];
    assert.deepEqual(templateGroups(templates, { project_id: "", inventory_id: "" }, inventories, projects), [
        { label: "lab2", options: [{ id: "t2", label: "ping — school" }, { id: "t3", label: "ping — office" }, { id: "t4", label: "wol" }] },
        { label: "lab10", options: [{ id: "t1", label: "shutdown" }] },
    ]);
    // A workflow of one inventory: one group with no label.
    const one = templateGroups(templates.slice(1), { project_id: "", inventory_id: "i1" }, inventories, projects);
    assert.deepEqual(one.map(group => group.label), [""]);
});
