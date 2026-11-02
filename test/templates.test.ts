import { test } from "node:test";
import assert from "node:assert/strict";
import { byGroupOrder, groupItems, isOpen, readOpen, withOpen } from "../src/lib/listview.ts";
import {
    DEFAULT_TEMPLATES_VIEW, filterTemplates, groupTemplates, isTemplateGroupOpen, outcomeOf, readTemplatesView, sortTemplates,
    withoutTemplateFilters, withTemplateGroupsOpen, type TemplatesView,
} from "../src/lib/templates.ts";
import type { RunStatus, Template } from "../src/lib/types.ts";

function template(name: string, project: string, inventory: string, more: Partial<Template> = {}, last?: [RunStatus, number]): Template {
    return {
        id: name, name, project_id: project, playbook: `${name}.yml`, inventory_id: inventory, machine_credential_id: null,
        vault_credential_ids: [], limit_hosts: "", tags: [], skip_tags: [], extra_vars: {}, variable_set_ids: [], survey: [], forks: null, verbosity: 0,
        become: false, needs_approval: false, ...more,
        last_run: last ? { id: `r-${name}`, status: last[0], created_at: last[1], finished_at: null } : null,
    };
}

const TEMPLATES = [
    template("Deploy", "p2", "i1", { tags: ["web"] }, ["failed", 300]),
    template("backup", "p1", "i1", { needs_approval: true }, ["successful", 100]),
    template("Patch", "p1", "i2", { survey: [{ name: "reboot", type: "boolean" }] }, ["running", 200]),
    template("audit", "p2", "i2"),
    template("Reset", "p1", "i2", {}, ["rejected", 50]),
];
const NAMES = {
    project: (id: string) => ({ p1: "infra", p2: "apps" })[id] ?? "",
    inventory: (id: string) => ({ i1: "prod", i2: "lab" })[id] ?? "",
    schedules: (id: string) => id === "backup" ? 2 : 0,
};
const names = (items: Template[]) => items.map(item => item.name);
const view = (more: Partial<TemplatesView> = {}) => ({ ...DEFAULT_TEMPLATES_VIEW, ...more });

test("the last run says how a template went", () => {
    assert.deepEqual(TEMPLATES.map(outcomeOf), ["failed", "successful", "active", "never", "canceled"]);
});

test("a stored view of templates that is not understood falls back to the default", () => {
    assert.deepEqual(readTemplatesView(null), DEFAULT_TEMPLATES_VIEW);
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
    const shown = view();
    assert.deepEqual(projects.map(group => isTemplateGroupOpen(shown, group)), [true, true]);
    const closed = withTemplateGroupsOpen(shown, projects, false);
    assert.deepEqual(projects.map(group => isTemplateGroupOpen(closed, group)), [false, false]);
    assert.equal(isTemplateGroupOpen({ ...closed, groupBy: "inventory" }, { key: "p1", name: "", items: [] }), true);
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
