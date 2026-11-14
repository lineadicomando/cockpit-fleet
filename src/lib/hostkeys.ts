// The trusted host keys as hosts: filters, groups and the view kept in the
// browser, from what hostkey.list returns.

import { byGroupName, groupItems, isOpen, oneOf, readObject, readOpen, readText, withOpen, type Group } from "./listview.ts";
import type { HostKey, HostKeyInventory, UntrustedHost } from "./types.ts";

// One host, with every key trusted for it; none when it is a host of an
// inventory that runs would refuse.
export interface KeyHost {
    // As known_hosts names it, which is what commands take.
    host: string;
    address: string;
    port: number;
    keys: HostKey[];
    inventories: HostKeyInventory[];
    trusted: boolean;
    // Of the key trusted last.
    added_by: string;
    added_at: number;
}

const BRACKETED = /^\[([^\]]+)\]:([0-9]{1,5})$/;

export function splitKnownHost(host: string): { address: string; port: number } {
    const bracketed = BRACKETED.exec(host);
    return bracketed ? { address: bracketed[1], port: Number(bracketed[2]) } : { address: host, port: 22 };
}

const compareText = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });

export function keyHosts(keys: HostKey[], untrusted: UntrustedHost[]): KeyHost[] {
    const hosts = new Map<string, KeyHost>();
    for (const key of keys) {
        const host = hosts.get(key.host) ?? {
            host: key.host, ...splitKnownHost(key.host), keys: [], inventories: key.inventories, trusted: true, added_by: "", added_at: 0,
        };
        host.keys.push(key);
        if (key.added_at >= host.added_at) {
            host.added_at = key.added_at;
            host.added_by = key.added_by;
        }
        hosts.set(key.host, host);
    }
    for (const item of untrusted) {
        if (!hosts.has(item.host)) {
            hosts.set(item.host, {
                host: item.host, ...splitKnownHost(item.host), keys: [], inventories: item.inventories, trusted: false, added_by: "", added_at: 0,
            });
        }
    }
    return [...hosts.values()].sort((a, b) => compareText(a.address, b.address) || a.port - b.port);
}

// The inventory filter's value for hosts that no inventory has.
export const NO_INVENTORY = "-";

export type HostKeyGroupBy = "host" | "inventory" | "none";
export type TrustFilter = "" | "trusted" | "untrusted";

export interface HostKeysView {
    search: string;
    inventory: string;
    trust: TrustFilter;
    groupBy: HostKeyGroupBy;
    // Groups opened or closed by hand, by "groupBy:key"; hosts start closed, inventories open.
    open: Record<string, boolean>;
}

export const DEFAULT_HOST_KEYS_VIEW: HostKeysView = { search: "", inventory: "", trust: "", groupBy: "host", open: {} };

export function readHostKeysView(text: string | null): HostKeysView {
    const raw = readObject(text);
    return {
        search: readText(raw.search),
        inventory: readText(raw.inventory, 64),
        trust: oneOf(raw.trust, ["", "trusted", "untrusted"], ""),
        groupBy: oneOf(raw.groupBy, ["host", "inventory", "none"], DEFAULT_HOST_KEYS_VIEW.groupBy),
        open: readOpen(raw.open),
    };
}

export function withoutHostKeyFilters(view: HostKeysView): HostKeysView {
    return { ...view, search: "", inventory: "", trust: "" };
}

export function filterKeyHosts(hosts: KeyHost[], view: HostKeysView): KeyHost[] {
    const needle = view.search.trim().toLowerCase();
    return hosts.filter(host => {
        if (view.trust === "trusted" && !host.trusted) return false;
        if (view.trust === "untrusted" && host.trusted) return false;
        if (view.inventory === NO_INVENTORY && host.inventories.length > 0) return false;
        if (view.inventory && view.inventory !== NO_INVENTORY && !host.inventories.some(item => item.id === view.inventory)) return false;
        if (needle) {
            const text = [host.host, ...host.inventories.map(item => item.host), ...host.keys.map(key => key.fingerprint)]
                .join("\n").toLowerCase();
            if (!text.includes(needle)) return false;
        }
        return true;
    });
}

// By inventory: a host in two inventories is in both groups, one in none in a
// group of its own (key NO_INVENTORY) after the others; with ONLY, the group
// of that inventory alone, the one the list is filtered on.
export function groupKeyHosts(hosts: KeyHost[], by: HostKeyGroupBy, only = ""): Group<KeyHost>[] {
    if (by === "host") return hosts.map(host => ({ key: host.host, name: host.host, items: [host] }));
    if (by === "none") return [{ key: "", name: "", items: hosts }];
    const pairs = hosts.flatMap(host => host.inventories.length
        ? host.inventories.map(item => ({ host, id: item.id, name: item.name }))
        : [{ host, id: NO_INVENTORY, name: "" }]);
    const groups = byGroupName(groupItems(only ? pairs.filter(pair => pair.id === only) : pairs, pair => pair.id, pair => pair.name))
        .map(group => ({ key: group.key, name: group.name, items: group.items.map(pair => pair.host) }));
    return [...groups.filter(group => group.key !== NO_INVENTORY), ...groups.filter(group => group.key === NO_INVENTORY)];
}

export function isHostKeyGroupOpen(view: HostKeysView, group: Group<KeyHost>): boolean {
    return isOpen(view.open, view.groupBy, group.key, view.groupBy !== "host");
}

export function withHostKeyGroupsOpen(view: HostKeysView, groups: Group<KeyHost>[], open: boolean): HostKeysView {
    return { ...view, open: withOpen(view.open, view.groupBy, groups.map(group => group.key), open) };
}

// The hosts that some inventory has, for the filter.
export function inventoriesOf(hosts: KeyHost[]): { id: string; name: string }[] {
    const found = new Map<string, string>();
    for (const host of hosts) for (const item of host.inventories) found.set(item.id, item.name);
    return [...found].map(([id, name]) => ({ id, name })).sort((a, b) => compareText(a.name, b.name));
}

// The hosts chosen among those shown, so that an action never reaches one a filter hides.
export function chosen(hosts: KeyHost[], selected: ReadonlySet<string>): KeyHost[] {
    return hosts.filter(host => selected.has(host.host));
}
