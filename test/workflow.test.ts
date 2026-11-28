import { test } from "node:test";
import assert from "node:assert/strict";
import {
    DEFAULT_WORKFLOWS_VIEW, inScope, isWorkflowGroupOpen, layers, listWorkflows, nextStepId, readWorkflowsView, stepColumns, templateGroups,
    withoutWorkflowFilters, withWorkflowGroupsOpen, workflowInventories, type WorkflowNames, type WorkflowsView,
} from "../src/lib/workflow.ts";
import type { RunStatus, Template, Workflow, WorkflowNode } from "../src/lib/types.ts";

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
    const template = (id: string, name: string, project_id: string, inventory_id: string | null) =>
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
    // Those with no default inventory: under the label given for them.
    const free = templateGroups([...templates, template("t4", "audit", "p2", null)], { project_id: "", inventory_id: "" },
        inventories, projects, "Chosen at launch");
    assert.deepEqual(free.map(group => [group.label, group.options.map(option => option.id)]),
        [["Chosen at launch", ["t4"]], ["lab2", ["t3", "t2"]], ["lab10", ["t1"]]]);
    // A workflow of one inventory: one group with no label.
    const one = templateGroups(templates.slice(1), { project_id: "", inventory_id: "i1" }, inventories, projects);
    assert.deepEqual(one.map(group => group.label), [""]);
});

test("each step is marked by the links that reach it", () => {
    const node = (id: string, name = "", approval: "none" | "confirm" = "none") => ({ id, name, template_id: `t-${id}`, approval });
    const nodes = [node("a", "backup"), node("b"), node("c", "restore"), node("d", "", "confirm"), node("e")];
    const edges = [
        { from: "a", to: "b", condition: "success" as const },
        { from: "a", to: "c", condition: "failure" as const },
        { from: "b", to: "d", condition: "always" as const },
        { from: "c", to: "d", condition: "always" as const },
        { from: "b", to: "e", condition: "success" as const },
        { from: "c", to: "e", condition: "always" as const },
    ];
    const columns = stepColumns(nodes, edges, item => item.name || item.id, item => item.approval !== "none");
    assert.deepEqual(columns, [
        [{ id: "a", name: "backup", branch: "", approval: false }],
        [{ id: "b", name: "b", branch: "", approval: false }, { id: "c", name: "restore", branch: "failure", approval: false }],
        // Reached always by all its links; by links of two kinds, unmarked.
        [{ id: "d", name: "d", branch: "always", approval: true }, { id: "e", name: "e", branch: "", approval: false }],
    ]);
});

const run = (id: string, status: RunStatus, created_at: number) => ({ id, status, created_at });

function workflow(id: string, name: string, extra: Partial<Workflow> = {}): Workflow {
    const nodes: WorkflowNode[] = [{ id: "a", name: "", template_id: "t1", limit: "", answers: {}, approval: "none", needs_approval: false }];
    return {
        id, name, description: "", project_id: null, inventory_id: null, inventory_ids: null, needs_inventory: false,
        nodes, edges: [], created_at: 0, updated_at: 0, last_run: null, last_runs: {}, ...extra,
    };
}

const names: WorkflowNames = {
    template: id => ({ t1: { id: "t1", name: "deploy", project_id: "p1", approval: "none" },
        t2: { id: "t2", name: "reboot", project_id: "p2", approval: "other" } } as unknown as Record<string, Template>)[id],
    project: id => ({ p1: "school", p2: "office" } as Record<string, string>)[id] ?? "",
    inventory: id => ({ i1: "lab1", i2: "lab2", "": "Each step's own" } as Record<string, string>)[id] ?? "",
    schedules: id => id === "w2" ? 1 : 0,
    inventories: ["i1", "i2"],
};

const view = (change: Partial<WorkflowsView> = {}): WorkflowsView => ({ ...DEFAULT_WORKFLOWS_VIEW, ...change });

test("a workflow may run on its inventory, those a launch chooses, or each step's own", () => {
    assert.deepEqual(workflowInventories(workflow("w", "w", { inventory_id: "i2" }), names.inventories), ["i2"]);
    assert.deepEqual(workflowInventories(workflow("w", "w"), names.inventories), ["i1", "i2", ""]);
    // A starter, not usable, is left out; a step with no default needs one chosen.
    assert.deepEqual(workflowInventories(workflow("w", "w", { inventory_ids: ["i1", "starter"], needs_inventory: true }), names.inventories), ["i1"]);
});

test("the workflows are listed under each inventory, by its last run there", () => {
    const workflows = [
        workflow("w1", "update", { inventory_id: "i1", last_run: run("r1", "failed", 5), last_runs: { i1: run("r1", "failed", 5) } }),
        workflow("w2", "backup", { inventory_ids: ["i2"], last_run: run("r3", "successful", 9),
            last_runs: { i2: run("r2", "failed", 7), "": run("r3", "successful", 9) } }),
    ];
    const listed = listWorkflows(workflows, view(), names);
    assert.deepEqual(listed.groups.map(group => [group.key, group.items.map(item => item.id)]), [["i1", ["w1"]], ["i2", ["w2"]], ["", ["w2"]]]);
    assert.equal(listed.shown, 2);
    // Failed on lab2, successful on each step's own.
    const failed = listWorkflows(workflows, view({ outcome: "failed" }), names);
    assert.deepEqual(failed.groups.map(group => [group.key, group.items.map(item => item.id)]), [["i1", ["w1"]], ["i2", ["w2"]]]);
    // Not grouped, the last run on any inventory.
    const flat = listWorkflows(workflows, view({ groupBy: "none", outcome: "failed" }), names);
    assert.deepEqual(flat.groups[0].items.map(item => item.id), ["w1"]);
});

test("the workflows are filtered by text, project, feature and grouped by project", () => {
    const workflows = [
        workflow("w1", "update", { description: "monthly" }),
        workflow("w2", "restart", { nodes: [
            { id: "a", name: "stop", template_id: "t1", limit: "", answers: {}, approval: "none", needs_approval: false },
            { id: "b", name: "", template_id: "t2", limit: "", answers: {}, approval: "none", needs_approval: false },
        ] }),
        workflow("w3", "fixed", { inventory_id: "i1", project_id: "p1" }),
    ];
    const ids = (change: Partial<WorkflowsView>) => listWorkflows(workflows, view({ groupBy: "none", ...change }), names).groups[0].items.map(item => item.id);
    assert.deepEqual(ids({ search: "MONTH" }), ["w1"]);
    assert.deepEqual(ids({ search: "reboot" }), ["w2"]);
    assert.deepEqual(ids({ search: "office" }), ["w2"]);
    assert.deepEqual(ids({ project: "p2" }), ["w2"]);
    assert.deepEqual(ids({ trait: "approval" }), ["w2"]);
    assert.deepEqual(ids({ trait: "scheduled" }), ["w2"]);
    assert.deepEqual(ids({ trait: "unscheduled" }), ["w3", "w1"]);
    assert.deepEqual(ids({ trait: "choose" }), ["w2", "w1"]);
    assert.deepEqual(ids({ inventory: "i2" }), ["w2", "w1"]);
    const byProject = listWorkflows(workflows, view({ groupBy: "project" }), names);
    assert.deepEqual(byProject.groups.map(group => [group.name, group.items.map(item => item.id)]),
        [["office", ["w2"]], ["school", ["w3", "w2", "w1"]]]);
    assert.equal(byProject.shown, 3);
});

test("the workflows sort by last run, never launched first", () => {
    const workflows = [
        workflow("w1", "b", { last_run: run("r1", "successful", 9) }),
        workflow("w2", "a"),
        workflow("w3", "c", { last_run: run("r2", "successful", 3) }),
    ];
    const ids = (descending: boolean) => listWorkflows(workflows, view({ groupBy: "none", sort: "last_run", descending }), names)
        .groups[0].items.map(item => item.id);
    assert.deepEqual(ids(false), ["w2", "w3", "w1"]);
    assert.deepEqual(ids(true), ["w1", "w3", "w2"]);
});

test("the view of the workflows is read back, groups open and filters cleared", () => {
    assert.deepEqual(readWorkflowsView(null), DEFAULT_WORKFLOWS_VIEW);
    assert.deepEqual(readWorkflowsView("{not json"), DEFAULT_WORKFLOWS_VIEW);
    const read = readWorkflowsView(JSON.stringify({ groupBy: "project", trait: "nonsense", outcome: "failed", sort: "last_run", descending: true }));
    assert.deepEqual([read.groupBy, read.trait, read.outcome, read.sort, read.descending], ["project", "", "failed", "last_run", true]);
    const group = { key: "i1", name: "lab1", items: [] };
    assert.ok(isWorkflowGroupOpen(view(), group));
    assert.ok(!isWorkflowGroupOpen(withWorkflowGroupsOpen(view(), [group], false), group));
    assert.deepEqual(withoutWorkflowFilters(view({ search: "x", project: "p1", inventory: "i1", trait: "choose", outcome: "never" })), view());
});
