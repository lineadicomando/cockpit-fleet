import { test } from "node:test";
import assert from "node:assert/strict";
import { byGroupOrder, groupItems, isOpen, readOpen, withOpen } from "../src/lib/listview.ts";
import {
    allowedInventories, DEFAULT_TEMPLATES_VIEW, filterTemplates, groupTemplates, isTemplateGroupOpen, listTemplates, outcomeOf, overrideFields, overridesOf, readTemplatesView, sortTemplates,
    withoutTemplateFilters, withTemplateGroupsOpen, type TemplatesView,
} from "../src/lib/templates.ts";
import type { RunStatus, Template } from "../src/lib/types.ts";

function runs(name: string, inventory: string | null, last?: [RunStatus, number]): Pick<Template, "last_run" | "last_runs"> {
    const run = last ? { id: `r-${name}`, status: last[0], created_at: last[1], finished_at: null, inventory_id: inventory } : null;
    return { last_run: run, last_runs: run && inventory ? { [inventory]: run } : {} };
}

function template(name: string, project: string, inventory: string | null, more: Partial<Template> = {}, last?: [RunStatus, number]): Template {
    return {
        id: name, name, project_id: project, playbook: `${name}.yml`, inventory_id: inventory, inventory_ids: [], any_inventory: false, machine_credential_id: null,
        vault_credential_ids: [], limit_hosts: "", tags: [], skip_tags: [], extra_vars: {}, variable_set_ids: [], survey: [], forks: null, verbosity: 0,
        become: false, approval: "none", needs_approval: false, ...runs(name, inventory, last), ...more,
    };
}

const TEMPLATES = [
    template("Deploy", "p2", "i1", { tags: ["web"] }, ["failed", 300]),
    template("backup", "p1", "i1", { approval: "confirm", needs_approval: true }, ["successful", 100]),
    template("Patch", "p1", "i2", { survey: [{ name: "reboot", type: "boolean" }] }, ["running", 200]),
    template("audit", "p2", "i2"),
    template("Reset", "p1", "i2", {}, ["rejected", 50]),
];
const NAMES = {
    project: (id: string) => ({ p1: "infra", p2: "apps" })[id] ?? "",
    inventory: (id: string) => ({ i1: "prod", i2: "lab" })[id] ?? "",
    schedules: (id: string) => id === "backup" ? 2 : 0,
    inventories: ["i1", "i2"],
};
const names = (items: Template[]) => items.map(item => item.name);
const view = (more: Partial<TemplatesView> = {}) => ({ ...DEFAULT_TEMPLATES_VIEW, ...more });

test("the last run says how a template went", () => {
    assert.deepEqual(TEMPLATES.map(item => outcomeOf(item)), ["failed", "successful", "active", "never", "canceled"]);
});

test("a stored view of templates that is not understood falls back to the default", () => {
    assert.deepEqual(readTemplatesView(null), DEFAULT_TEMPLATES_VIEW);
    assert.equal(readTemplatesView(null).groupBy, "inventory");
    assert.deepEqual(readTemplatesView("{"), DEFAULT_TEMPLATES_VIEW);
    const stored = readTemplatesView(JSON.stringify({ groupBy: "outcome", trait: "magic", outcome: "failed", sort: "last_run", project: 7 }));
    assert.deepEqual([stored.groupBy, stored.trait, stored.outcome, stored.sort, stored.project], ["outcome", "", "failed", "last_run", ""]);
    assert.deepEqual(withoutTemplateFilters(view({ search: "x", project: "p1", outcome: "never", groupBy: "none" })), view({ groupBy: "none" }));
});

test("filters narrow the templates", () => {
    const by = (more: Partial<TemplatesView>) => names(filterTemplates(TEMPLATES, view(more), NAMES));
    assert.deepEqual(by({}), names(TEMPLATES));
    assert.deepEqual(by({ project: "p1" }), ["backup", "Patch", "Reset"]);
    assert.deepEqual(by({ inventory: "i1" }), ["Deploy", "backup"]);
    assert.deepEqual(by({ trait: "approval" }), ["backup"]);
    assert.deepEqual(by({ trait: "survey" }), ["Patch"]);
    assert.deepEqual(by({ trait: "scheduled" }), ["backup"]);
    assert.deepEqual(by({ trait: "unscheduled" }), ["Deploy", "Patch", "audit", "Reset"]);
    assert.deepEqual(by({ outcome: "never" }), ["audit"]);
    assert.deepEqual(by({ outcome: "failed" }), ["Deploy"]);
    assert.deepEqual(by({ search: "WEB" }), ["Deploy"]);
    assert.deepEqual(by({ search: "infra" }), ["backup", "Patch", "Reset"]);
    assert.deepEqual(by({ search: "lab", project: "p2" }), ["audit"]);
});

test("templates sort by name, project, inventory or last run", () => {
    assert.deepEqual(names(sortTemplates(TEMPLATES, "name", false, NAMES)), ["audit", "backup", "Deploy", "Patch", "Reset"]);
    assert.deepEqual(names(sortTemplates(TEMPLATES, "project", false, NAMES)), ["audit", "Deploy", "backup", "Patch", "Reset"]);
    assert.deepEqual(names(sortTemplates(TEMPLATES, "inventory", true, NAMES)), ["Deploy", "backup", "Reset", "Patch", "audit"]);
    assert.deepEqual(names(sortTemplates(TEMPLATES, "last_run", false, NAMES)), ["audit", "Reset", "backup", "Patch", "Deploy"]);
});

test("templates group by project, inventory or outcome, every group open at first", () => {
    const projects = groupTemplates(TEMPLATES, "project", NAMES);
    assert.deepEqual(projects.map(group => [group.name, names(group.items)]), [["apps", ["Deploy", "audit"]], ["infra", ["backup", "Patch", "Reset"]]]);
    assert.deepEqual(groupTemplates(TEMPLATES, "inventory", NAMES).map(group => group.name), ["lab", "prod"]);
    assert.deepEqual(groupTemplates(TEMPLATES, "outcome", NAMES).map(group => group.key), ["failed", "active", "canceled", "successful", "never"]);
    assert.deepEqual(groupTemplates(TEMPLATES, "none", NAMES).map(group => group.items.length), [5]);
    const shown = view({ groupBy: "project" });
    assert.deepEqual(projects.map(group => isTemplateGroupOpen(shown, group)), [true, true]);
    const closed = withTemplateGroupsOpen(shown, projects, false);
    assert.deepEqual(projects.map(group => isTemplateGroupOpen(closed, group)), [false, false]);
    assert.equal(isTemplateGroupOpen({ ...closed, groupBy: "inventory" }, { key: "p1", name: "", items: [] }), true);
});

test("a template that allows other inventories is under each of them", () => {
    const spread = [
        template("wol", "p1", "i1", { inventory_ids: ["i2"] }),
        template("anywhere", "p1", "i2", { any_inventory: true }),
        template("here", "p1", "i1"),
    ];
    const names3 = { ...NAMES, inventory: (id: string) => ({ i1: "prod", i2: "lab", i3: "test" })[id] ?? "", inventories: ["i1", "i2", "i3"] };
    assert.deepEqual(allowedInventories(spread[1], names3.inventories), ["i2", "i1", "i3"]);
    assert.deepEqual(groupTemplates(spread, "inventory", names3).map(group => [group.name, names(group.items)]),
        [["lab", ["wol", "anywhere"]], ["prod", ["wol", "anywhere", "here"]], ["test", ["anywhere"]]]);
    assert.deepEqual(names(filterTemplates(spread, view({ inventory: "i3" }), names3)), ["anywhere"]);
    assert.deepEqual(names(filterTemplates(spread, view({ search: "lab" }), names3)), ["wol", "anywhere"]);
});

test("under an inventory a template speaks of its last run there", () => {
    const at = (id: string, status: RunStatus, time: number, inventory: string) =>
        ({ id, status, created_at: time, finished_at: null, inventory_id: inventory });
    const lab = at("r1", "failed", 100, "i2");
    const prod = at("r2", "successful", 300, "i1");
    const both = [
        template("wol", "p1", "i1", { inventory_ids: ["i2"], last_run: prod, last_runs: { i1: prod, i2: lab } }),
        template("scan", "p1", "i1", { inventory_ids: ["i2"], last_run: at("r3", "successful", 200, "i1"), last_runs: { i1: at("r3", "successful", 200, "i1") } }),
    ];
    assert.deepEqual([outcomeOf(both[0]), outcomeOf(both[0], "i2"), outcomeOf(both[1], "i2")], ["successful", "failed", "never"]);

    const listed = (more: Partial<TemplatesView>) => listTemplates(both, view(more), NAMES);
    const shown = (more: Partial<TemplatesView>) => listed(more).groups.map(group => [group.name, names(group.items)]);
    // By last run there: under lab, wol (100) before scan (never there); under prod, scan (200) before wol (300).
    assert.deepEqual(shown({ sort: "last_run" }), [["lab", ["scan", "wol"]], ["prod", ["scan", "wol"]]]);
    assert.deepEqual(shown({ outcome: "failed" }), [["lab", ["wol"]]]);
    assert.deepEqual(shown({ outcome: "never" }), [["lab", ["scan"]]]);
    assert.equal(listed({ outcome: "successful" }).shown, 2);
    // Grouped otherwise, the last on any inventory.
    assert.equal(listed({ groupBy: "none", outcome: "failed" }).shown, 0);
    assert.deepEqual(listed({ groupBy: "outcome" }).groups.map(group => [group.key, names(group.items)]), [["successful", ["scan", "wol"]]]);
});

test("a template with no default inventory is only under those it allows", () => {
    const names3 = { ...NAMES, inventory: (id: string) => ({ i1: "prod", i2: "lab", i3: "test" })[id] ?? "", inventories: ["i1", "i2", "i3"] };
    const free = [
        template("chosen", "p1", null, { inventory_ids: ["i3", "i2"] }),
        template("any", "p1", null, { any_inventory: true }),
        template("here", "p1", "i1"),
    ];
    assert.deepEqual(allowedInventories(free[0], names3.inventories), ["i3", "i2"]);
    assert.deepEqual(allowedInventories(free[1], names3.inventories), ["i1", "i2", "i3"]);
    assert.deepEqual(groupTemplates(free, "inventory", names3).map(group => [group.name, names(group.items)]),
        [["lab", ["chosen", "any"]], ["prod", ["any", "here"]], ["test", ["chosen", "any"]]]);
    // By inventory, those with none come first.
    assert.deepEqual(names(sortTemplates(free, "inventory", false, names3)), ["any", "chosen", "here"]);
});

test("groups of any list keep the order asked for and what was opened by hand", () => {
    const groups = groupItems(["b1", "a1", "b2", "c1"], item => item[0]);
    assert.deepEqual(groups.map(group => [group.key, group.items]), [["b", ["b1", "b2"]], ["a", ["a1"]], ["c", ["c1"]]]);
    assert.deepEqual(byGroupOrder(groups, ["c", "a"]).map(group => group.key), ["c", "a", "b"]);
    const open = withOpen({}, "kind", ["a", "b"], false);
    assert.deepEqual([isOpen(open, "kind", "a", true), isOpen(open, "kind", "c", true), isOpen(open, "state", "a", true)], [false, true, true]);
    assert.deepEqual(readOpen({ "kind:a": true, "kind:b": "no" }), { "kind:a": true });
    assert.deepEqual(readOpen(["kind:a"]), {});
});

test("a launch replaces only the values of its template that differ", () => {
    const tpl = template("Deploy", "p1", "i1", { tags: ["web", "db"], skip_tags: ["slow"], forks: 5, verbosity: 1 });
    const fields = overrideFields(tpl);
    assert.deepEqual(fields, { tags: "web, db", skipTags: "slow", forks: "5", verbosity: "1" });
    assert.deepEqual(overridesOf(tpl, fields), {});
    assert.deepEqual(overridesOf(tpl, { ...fields, tags: "web,db " }), {});
    assert.deepEqual(overridesOf(tpl, { tags: "db", skipTags: "", forks: "20", verbosity: "3" }),
        { tags: ["db"], skip_tags: [], forks: 20, verbosity: 3 });
    // Forks left empty keep the template's, or the controller's default.
    assert.deepEqual(overridesOf(tpl, { ...fields, forks: "" }), {});
    assert.deepEqual(overridesOf(template("x", "p1", "i1"), { tags: "", skipTags: "", forks: "", verbosity: "0" }), {});
    // A schedule shows what it replaced.
    assert.deepEqual(overrideFields(tpl, { tags: [], verbosity: 3 }), { tags: "", skipTags: "slow", forks: "5", verbosity: "3" });
});
