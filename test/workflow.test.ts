import { test } from "node:test";
import assert from "node:assert/strict";
import { inScope, layers, nextStepId, templateGroups } from "../src/lib/workflow.ts";
import type { Template } from "../src/lib/types.ts";

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
    const template = { project_id: "p1", inventory_id: "i1", inventory_ids: [], any_inventory: false };
    assert.ok(inScope(template, { project_id: "", inventory_id: "" }));
    assert.ok(inScope({ ...template, inventory_ids: ["i2"] }, { project_id: "p1", inventory_id: "i2" }));
    assert.ok(inScope({ ...template, any_inventory: true }, { project_id: "p1", inventory_id: "i3" }));
    assert.ok(inScope(template, { project_id: "p1", inventory_id: "" }));
    assert.ok(inScope(template, { project_id: "p1", inventory_id: "i1" }));
    assert.ok(!inScope(template, { project_id: "p1", inventory_id: "i2" }));
    assert.ok(!inScope(template, { project_id: "p2", inventory_id: "" }));
});

test("the templates a step may take go by inventory, by name in each", () => {
    const inventories = new Map([["i1", "lab2"], ["i2", "lab10"]]);
    const projects = new Map([["p1", "school"], ["p2", "office"]]);
    const template = (id: string, name: string, project_id: string, inventory_id: string) =>
        ({ id, name, project_id, inventory_id, playbook: `${name}.yaml` }) as Template;
    const templates = [
        template("t1", "shutdown", "p1", "i2"),
        template("t2", "wol", "p1", "i1"),
        template("t3", "ping", "p2", "i1"),
    ];
    assert.deepEqual(templateGroups(templates, { project_id: "", inventory_id: "" }, inventories, projects), [
        { label: "lab2", options: [
            { id: "t3", label: "ping", description: "office · ping.yaml" },
            { id: "t2", label: "wol", description: "school · wol.yaml" },
        ] },
        { label: "lab10", options: [{ id: "t1", label: "shutdown", description: "school · shutdown.yaml" }] },
    ]);
    // A workflow of one inventory: one group with no label.
    const one = templateGroups(templates.slice(1), { project_id: "", inventory_id: "i1" }, inventories, projects);
    assert.deepEqual(one.map(group => group.label), [""]);
});
