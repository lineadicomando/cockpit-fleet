import { test } from "node:test";
import assert from "node:assert/strict";
import {
    DEFAULT_HOSTS_VIEW, hostGroups, hostMatches, isHostGroupOpen, readHostListView, readHostsView, stateMatches, withHostGroupsOpen,
} from "../src/lib/hosts.ts";
import type { CheckState, FleetHost } from "../src/lib/types.ts";

const COUNTS = { ok: 0, warning: 0, critical: 0, unknown: 0, pending: 0 };

function host(id: string, name: string, inventoryId: string, inventory: string, more: Partial<FleetHost> = {}): FleetHost {
    return {
        id, name, inventory_id: inventoryId, inventory, address: "", groups: [], platform: "linux", os: "",
        facts: {}, facts_at: null, reachable: null, states: COUNTS, ...more,
    };
}

const WEB = host("h1", "web01", "i-prod", "prod", { address: "10.0.0.10", groups: ["web", "linux"], os: "Debian 13" });
const DB = host("h2", "db01", "i-prod", "prod", { address: "10.0.0.20", os: "AlmaLinux 10" });
const PC = host("h3", "pc00", "i-lab", "lab", { groups: ["windows"], os: "Windows 11" });
// The same machine in a second inventory is a row of its own.
const WEB_LAB = host("h4", "web01", "i-lab", "lab", { address: "10.0.0.10" });

test("every word typed is found in the host's name, address, inventory, groups or system", () => {
    assert.ok(hostMatches(WEB, ""));
    assert.ok(hostMatches(WEB, "WEB01"));
    assert.ok(hostMatches(WEB, "10.0.0.1"));
    assert.ok(hostMatches(WEB, "prod debian"));
    assert.ok(hostMatches(WEB, "linux"));
    assert.ok(!hostMatches(WEB, "prod windows"));
    assert.ok(hostMatches(PC, "lab   windows"));
    assert.ok(!hostMatches(DB, "debian"));
});

test("a state matches by its host, its check or what it found", () => {
    const state = { host_id: "h2", host: "db01", check: "Disk space", summary: "/var at 95%" } as CheckState;
    assert.ok(stateMatches(state, DB, "alma"));
    assert.ok(stateMatches(state, DB, "disk /var"));
    assert.ok(!stateMatches(state, DB, "web01"));
    // A host not loaded is matched by the name the state carries.
    assert.ok(stateMatches(state, undefined, "db0"));
    assert.ok(!stateMatches(state, undefined, "alma"));
});

test("hosts are grouped by inventory, by name, or not at all", () => {
    const hosts = [WEB, PC, DB, WEB_LAB];
    const groups = hostGroups(hosts, true, item => item.inventory);
    assert.deepEqual(groups.map(group => [group.key, group.name, group.items.map(item => item.id)]),
        [["i-lab", "lab", ["h3", "h4"]], ["i-prod", "prod", ["h1", "h2"]]]);
    assert.deepEqual(hostGroups(hosts, false, item => item.inventory), [{ key: "", name: "", items: hosts }]);
    assert.deepEqual(hostGroups([], true, (item: FleetHost) => item.inventory), []);
});

test("groups start open and remember being closed", () => {
    const view = { ...DEFAULT_HOSTS_VIEW, groupBy: "inventory" as const };
    const [lab, prod] = hostGroups([WEB, PC], true, item => item.inventory);
    assert.ok(isHostGroupOpen(view, lab));
    const closed = withHostGroupsOpen(view, [lab], false);
    assert.equal(closed.show, "problems");
    assert.ok(!isHostGroupOpen(closed, lab));
    assert.ok(isHostGroupOpen(closed, prod));
    assert.deepEqual(closed.open, { "inventory:i-lab": false });
});

test("a stored view not understood falls back to the default", () => {
    assert.deepEqual(readHostsView(null), DEFAULT_HOSTS_VIEW);
    assert.deepEqual(readHostsView("not json"), DEFAULT_HOSTS_VIEW);
    assert.deepEqual(readHostsView(JSON.stringify({ search: 5, groupBy: "host", show: "map", open: { "inventory:a": "yes" } })), DEFAULT_HOSTS_VIEW);
    assert.deepEqual(readHostsView(JSON.stringify({ search: "web", groupBy: "inventory", show: "grid", open: { "inventory:a": false } })),
        { search: "web", groupBy: "inventory", show: "grid", open: { "inventory:a": false } });
    assert.deepEqual(readHostListView(JSON.stringify({ search: "x".repeat(500), groupBy: "inventory", show: "grid" })),
        { search: "x".repeat(200), groupBy: "inventory", open: {} });
});
