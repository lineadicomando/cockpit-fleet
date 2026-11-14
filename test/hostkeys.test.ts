import { test } from "node:test";
import assert from "node:assert/strict";
import {
    chosen, DEFAULT_HOST_KEYS_VIEW, filterKeyHosts, groupKeyHosts, inventoriesOf, isHostKeyGroupOpen, keyHosts, NO_INVENTORY, readHostKeysView,
    splitKnownHost, withHostKeyGroupsOpen, withoutHostKeyFilters, type HostKeysView,
} from "../src/lib/hostkeys.ts";
import type { HostKey, HostKeyInventory } from "../src/lib/types.ts";

const PROD = (host: string): HostKeyInventory => ({ id: "i-prod", name: "prod", host });
const LAB = (host: string): HostKeyInventory => ({ id: "i-lab", name: "lab", host });

let next = 1;
function key(host: string, keyType: string, at: number, inventories: HostKeyInventory[] = []): HostKey {
    return { id: next++, host, key_type: keyType, fingerprint: `SHA256:${host}-${keyType}`, added_by: `u${at}`, added_at: at, inventories };
}

const KEYS = [
    key("10.0.0.10", "ssh-ed25519", 5, [PROD("web")]),
    key("10.0.0.10", "ssh-rsa", 9, [PROD("web")]),
    key("[10.0.0.2]:2222", "ssh-ed25519", 3, [PROD("db"), LAB("10.0.0.2")]),
    key("old.example.org", "ssh-ed25519", 1),
];
const UNTRUSTED = [{ host: "10.0.0.3", inventories: [LAB("new")] }];
const HOSTS = keyHosts(KEYS, UNTRUSTED);
const view = (more: Partial<HostKeysView> = {}) => ({ ...DEFAULT_HOST_KEYS_VIEW, ...more });
const names = (hosts: { host: string }[]) => hosts.map(host => host.host);

test("a host as known_hosts names it has an address and a port", () => {
    assert.deepEqual(splitKnownHost("web01"), { address: "web01", port: 22 });
    assert.deepEqual(splitKnownHost("[fe80::1]:2222"), { address: "fe80::1", port: 2222 });
});

test("keys come together by host, in the order of addresses, the untrusted ones among them", () => {
    assert.deepEqual(names(HOSTS), ["10.0.0.2", "10.0.0.3", "10.0.0.10", "old.example.org"].map(address =>
        address === "10.0.0.2" ? "[10.0.0.2]:2222" : address));
    const web = HOSTS.find(host => host.host === "10.0.0.10")!;
    assert.equal(web.keys.length, 2);
    assert.equal(web.added_by, "u9");
    assert.equal(HOSTS.find(host => host.host === "10.0.0.3")!.trusted, false);
});

test("hosts are filtered by inventory, by trust and by text", () => {
    assert.deepEqual(names(filterKeyHosts(HOSTS, view({ inventory: "i-lab" }))), ["[10.0.0.2]:2222", "10.0.0.3"]);
    assert.deepEqual(names(filterKeyHosts(HOSTS, view({ inventory: NO_INVENTORY }))), ["old.example.org"]);
    assert.deepEqual(names(filterKeyHosts(HOSTS, view({ trust: "untrusted" }))), ["10.0.0.3"]);
    assert.equal(filterKeyHosts(HOSTS, view({ trust: "trusted" })).length, 3);
    assert.deepEqual(names(filterKeyHosts(HOSTS, view({ search: "DB" }))), ["[10.0.0.2]:2222"]);
    assert.deepEqual(names(filterKeyHosts(HOSTS, view({ search: "10.0.0.10-ssh-rsa" }))), ["10.0.0.10"]);
    assert.deepEqual(withoutHostKeyFilters(view({ search: "x", inventory: "i-lab", trust: "trusted", groupBy: "none" })), view({ groupBy: "none" }));
});

test("by inventory a host is in each of its inventories, those in none last", () => {
    const groups = groupKeyHosts(HOSTS, "inventory");
    assert.deepEqual(groups.map(group => [group.key, names(group.items)]), [
        ["i-lab", ["[10.0.0.2]:2222", "10.0.0.3"]],
        ["i-prod", ["[10.0.0.2]:2222", "10.0.0.10"]],
        [NO_INVENTORY, ["old.example.org"]],
    ]);
    assert.deepEqual(groupKeyHosts(filterKeyHosts(HOSTS, view({ inventory: "i-prod" })), "inventory", "i-prod")
        .map(group => [group.key, names(group.items)]), [["i-prod", ["[10.0.0.2]:2222", "10.0.0.10"]]]);
    assert.deepEqual(groupKeyHosts(filterKeyHosts(HOSTS, view({ inventory: NO_INVENTORY })), "inventory", NO_INVENTORY)
        .map(group => group.key), [NO_INVENTORY]);
    assert.equal(groupKeyHosts(HOSTS, "host").length, 4);
    assert.equal(groupKeyHosts(HOSTS, "none")[0].items.length, 4);
    assert.deepEqual(inventoriesOf(HOSTS), [{ id: "i-lab", name: "lab" }, { id: "i-prod", name: "prod" }]);
});

test("hosts start closed, inventories open", () => {
    const [host] = groupKeyHosts(HOSTS, "host");
    const [inventory] = groupKeyHosts(HOSTS, "inventory");
    assert.equal(isHostKeyGroupOpen(view(), host), false);
    assert.equal(isHostKeyGroupOpen(view({ groupBy: "inventory" }), inventory), true);
    assert.equal(isHostKeyGroupOpen(withHostKeyGroupsOpen(view(), [host], true), host), true);
});

test("an action reaches only the chosen hosts that are shown", () => {
    const shown = filterKeyHosts(HOSTS, view({ inventory: "i-prod" }));
    assert.deepEqual(names(chosen(shown, new Set(["10.0.0.10", "old.example.org"]))), ["10.0.0.10"]);
});

test("a stored view of host keys that is not understood falls back to the default", () => {
    assert.deepEqual(readHostKeysView(null), DEFAULT_HOST_KEYS_VIEW);
    assert.deepEqual(readHostKeysView('{"groupBy": "x", "trust": 1}'), DEFAULT_HOST_KEYS_VIEW);
    assert.equal(readHostKeysView('{"groupBy": "inventory"}').groupBy, "inventory");
});
