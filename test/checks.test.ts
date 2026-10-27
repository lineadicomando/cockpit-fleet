import { test } from "node:test";
import assert from "node:assert/strict";
import {
    activeFilters, bySeverity, isGroupOpen, withGroupsOpen, DEFAULT_VIEW, filterChecks, formatBytes, groupChecks, kindCategory, readView, sortChecks, standingOf, sumCounts, formatInterval, formatMetric, fromLocalInput, linesOf, ownThresholds,
    thresholdFields, thresholdsValid, toLocalInput, worstState,
} from "../src/lib/checks.ts";
import type { CheckDef, CheckKind, StateCounts } from "../src/lib/types.ts";

const DEFAULTS = { used_pct: { warning: 85, critical: 95 }, inodes_used_pct: { warning: 85, critical: 95 } };

test("only the thresholds that differ from the catalog's are sent", () => {
    const fields = thresholdFields(DEFAULTS);
    assert.deepEqual(fields.used_pct, { warning: "85", critical: "95" });
    assert.deepEqual(ownThresholds(DEFAULTS, fields), {});
    fields.used_pct = { warning: "70,5", critical: "" };
    assert.deepEqual(ownThresholds(DEFAULTS, fields), { used_pct: { warning: 70.5, critical: null } });
    assert.equal(thresholdsValid(fields), true);
    fields.inodes_used_pct = { warning: "lots", critical: "95" };
    assert.equal(thresholdsValid(fields), false);
});

test("the worst state of a host comes first", () => {
    assert.equal(worstState({ ok: 3, warning: 1, critical: 0, unknown: 1, pending: 0 }), "unknown");
    assert.equal(worstState({ ok: 0, warning: 0, critical: 0, unknown: 0, pending: 2 }), null);
    const sorted = bySeverity([{ state: "ok" as const }, { state: null }, { state: "critical" as const }, { state: "warning" as const }]);
    assert.deepEqual(sorted.map(item => item.state), ["critical", "warning", "ok", null]);
});

test("metrics read as a person would write them", () => {
    assert.equal(formatBytes(512), "512 B");
    assert.equal(formatBytes(16 * 1024 * 1024), "16.0 MiB");
    assert.equal(formatBytes(195892707328), "182 GiB");
    assert.equal(formatMetric({ name: "used_pct", value: 61.3, unit: "%" }), "61.3%");
    assert.equal(formatMetric({ name: "uptime", value: 7260, unit: "s" }), "2 h 01 min");
    assert.equal(formatMetric({ name: "offset", value: 1.8, unit: "s" }), "1.8 s");
    assert.equal(formatMetric({ name: "days_left", value: 3, unit: "d" }), "3 d");
    assert.equal(formatMetric({ name: "not_running", value: 1, unit: "" }), "1");
});

test("intervals, parameters and dates of the forms", () => {
    assert.equal(formatInterval(300), "5 min");
    assert.equal(formatInterval(5400), "1 h 30 min");
    assert.equal(formatInterval(3600), "1 h");
    assert.equal(formatInterval(86400), "1 d");
    assert.deepEqual(linesOf(" ssh \n\n cron\n"), ["ssh", "cron"]);
    const now = Math.floor(Date.now() / 1000 / 60) * 60;
    assert.equal(fromLocalInput(toLocalInput(now)), now);
    assert.equal(fromLocalInput(""), null);
});

function check(name: string, kind: CheckKind, inventory: string, states: Partial<StateCounts>, more: Partial<CheckDef> = {}): CheckDef {
    return {
        id: name, name, kind, inventory_id: inventory, inventory: inventory.toUpperCase(), group: "all", interval: 300, retry_interval: 60,
        max_attempts: 3, params: {}, thresholds: {}, effective_thresholds: {}, machine_credential_id: null, project_id: null, playbook: "",
        become: false, enabled: true, states: { ok: 0, warning: 0, critical: 0, unknown: 0, pending: 0, ...states }, ...more,
    };
}

const CHECKS = [
    check("Disks", "disk", "lab", { ok: 3, critical: 1 }),
    check("ssh", "services", "lab", { ok: 4 }, { group: "web" }),
    check("Mail queue", "playbook", "prod", { warning: 2, ok: 1 }, { playbook: "checks/mailq.yml", interval: 60 }),
    check("Clock", "clock", "prod", { pending: 5 }),
    check("Updates", "updates", "prod", { ok: 2 }, { enabled: false, interval: 86400 }),
];
const names = (checks: CheckDef[]) => checks.map(item => item.name);

test("kinds fall in four families", () => {
    assert.equal(kindCategory("memory"), "resources");
    assert.equal(kindCategory("certificates"), "system");
    assert.equal(kindCategory("compliance"), "inventory");
    assert.equal(kindCategory("playbook"), "own");
});

test("a stored view that is not understood falls back to the default", () => {
    assert.deepEqual(readView(null), DEFAULT_VIEW);
    assert.deepEqual(readView("not json"), DEFAULT_VIEW);
    assert.deepEqual(readView("[1]"), DEFAULT_VIEW);
    const view = readView(JSON.stringify({ state: "problems", groupBy: "everything", sort: "states", descending: "yes", inventory: 3 }));
    assert.equal(view.state, "problems");
    assert.equal(view.groupBy, "inventory");
    assert.equal(view.sort, "states");
    assert.equal(view.descending, false);
    assert.equal(view.inventory, "");
    assert.equal(activeFilters(view), 1);
    assert.deepEqual(readView(JSON.stringify({ open: { "kind:own": false, "kind:system": "yes" } })).open, { "kind:own": false });
});

test("filters narrow the checks", () => {
    const by = (more: Partial<typeof DEFAULT_VIEW>) => names(filterChecks(CHECKS, { ...DEFAULT_VIEW, ...more }, kind => kind === "disk" ? "Disk space" : kind));
    assert.deepEqual(by({}), names(CHECKS));
    assert.deepEqual(by({ state: "problems" }), ["Disks", "Mail queue"]);
    assert.deepEqual(by({ state: "critical" }), ["Disks"]);
    assert.deepEqual(by({ state: "ok" }), ["ssh", "Updates"]);
    assert.deepEqual(by({ state: "pending" }), ["Clock"]);
    assert.deepEqual(by({ inventory: "prod", enabled: "enabled" }), ["Mail queue", "Clock"]);
    assert.deepEqual(by({ enabled: "disabled" }), ["Updates"]);
    assert.deepEqual(by({ category: "inventory" }), ["Updates"]);
    assert.deepEqual(by({ search: "SPACE" }), ["Disks"]);
    assert.deepEqual(by({ search: "mailq" }), ["Mail queue"]);
    assert.deepEqual(by({ search: "web" }), ["ssh"]);
    assert.deepEqual(by({ search: " PROD " }), ["Mail queue", "Clock", "Updates"]);
});

test("checks sort by name, interval or how bad they stand", () => {
    assert.equal(standingOf(CHECKS[4]), "disabled");
    assert.equal(standingOf(CHECKS[3]), "pending");
    assert.deepEqual(names(sortChecks(CHECKS, "name", false)), ["Clock", "Disks", "Mail queue", "ssh", "Updates"]);
    assert.deepEqual(names(sortChecks(CHECKS, "name", true)), ["Updates", "ssh", "Mail queue", "Disks", "Clock"]);
    assert.deepEqual(names(sortChecks(CHECKS, "interval", false)), ["Mail queue", "Clock", "Disks", "ssh", "Updates"]);
    assert.deepEqual(names(sortChecks(CHECKS, "states", false)), ["Disks", "Mail queue", "ssh", "Clock", "Updates"]);
});

test("checks group by inventory, family or standing, in a fixed order", () => {
    assert.deepEqual(groupChecks(CHECKS, "none").map(group => group.items.length), [5]);
    const inventories = groupChecks(CHECKS, "inventory");
    assert.deepEqual(inventories.map(group => [group.key, group.name, names(group.items)]),
        [["lab", "LAB", ["Disks", "ssh"]], ["prod", "PROD", ["Mail queue", "Clock", "Updates"]]]);
    assert.deepEqual(sumCounts(inventories[1].items), { ok: 3, warning: 2, critical: 0, unknown: 0, pending: 5 });
    assert.deepEqual(groupChecks(CHECKS, "kind").map(group => group.key), ["resources", "system", "inventory", "own"]);
    assert.deepEqual(groupChecks(CHECKS, "state").map(group => group.key), ["critical", "warning", "ok", "pending", "disabled"]);
});

test("groups with a problem start open, and can all be opened or closed", () => {
    const view = { ...DEFAULT_VIEW, groupBy: "inventory" as const };
    const groups = groupChecks(CHECKS, "inventory");
    // lab has a critical disk, prod a warning: both open; a group all OK would be closed.
    assert.deepEqual(groups.map(group => isGroupOpen(view, group, groups.length)), [true, true]);
    const calm = groupChecks(CHECKS.filter(item => item.name === "ssh" || item.name === "Clock"), "inventory");
    assert.deepEqual(calm.map(group => isGroupOpen(view, group, calm.length)), [false, false]);
    assert.deepEqual(calm.map(group => isGroupOpen({ ...view, search: "s" }, group, calm.length)), [true, true]);
    const closed = withGroupsOpen(view, groups, false);
    assert.deepEqual(closed.open, { "inventory:lab": false, "inventory:prod": false });
    assert.deepEqual(groups.map(group => isGroupOpen(closed, group, groups.length)), [false, false]);
    const opened = withGroupsOpen(closed, [groups[1]], true);
    assert.deepEqual(groups.map(group => isGroupOpen(opened, group, groups.length)), [false, true]);
    // Grouping another way keeps its own choices.
    const byKind = groupChecks(CHECKS, "kind");
    assert.equal(isGroupOpen({ ...closed, groupBy: "kind" }, byKind[0], byKind.length), true);
});
