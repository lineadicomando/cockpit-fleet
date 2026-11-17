// The lists of hosts of the pages Hosts and Assets: what is typed in the
// filter, the groups by inventory, and the view kept in the browser.

import { byGroupName, groupItems, isOpen, oneOf, readObject, readOpen, readText, withOpen, type Group } from "./listview.ts";
import type { CheckState, FleetHost } from "./types.ts";

export type HostGroupBy = "inventory" | "none";
export type HostsShow = "problems" | "hosts" | "grid";

export interface HostListView {
    search: string;
    groupBy: HostGroupBy;
    // Groups opened or closed by hand, by "groupBy:key"; every group starts open.
    open: Record<string, boolean>;
}

export interface HostsView extends HostListView {
    show: HostsShow;
}

export const DEFAULT_HOST_LIST_VIEW: HostListView = { search: "", groupBy: "inventory", open: {} };
export const DEFAULT_HOSTS_VIEW: HostsView = { ...DEFAULT_HOST_LIST_VIEW, show: "problems" };

export function readHostListView(text: string | null): HostListView {
    const raw = readObject(text);
    return {
        search: readText(raw.search),
        groupBy: oneOf(raw.groupBy, ["inventory", "none"], DEFAULT_HOST_LIST_VIEW.groupBy),
        open: readOpen(raw.open),
    };
}

export function readHostsView(text: string | null): HostsView {
    return { ...readHostListView(text), show: oneOf(readObject(text).show, ["problems", "hosts", "grid"], DEFAULT_HOSTS_VIEW.show) };
}

// The words typed, each of which must be found somewhere.
function words(text: string): string[] {
    return text.toLowerCase().split(/\s+/).filter(Boolean);
}

function hostText(host: FleetHost): string[] {
    return [host.name, host.address, host.inventory, ...host.groups, host.os];
}

function every(found: (string | null | undefined)[], text: string): boolean {
    const wanted = words(text);
    if (wanted.length === 0) return true;
    const haystack = found.filter(Boolean).join(" ").toLowerCase();
    return wanted.every(word => haystack.includes(word));
}

// Every word somewhere in the host's name, address, inventory, groups or system.
export function hostMatches(host: FleetHost, text: string): boolean {
    return every(hostText(host), text);
}

// A state matches by its host (as above, when known), its check or what it found.
export function stateMatches(state: CheckState, host: FleetHost | undefined, text: string): boolean {
    return every([...(host ? hostText(host) : [state.host]), state.check, state.summary], text);
}

// One group per inventory, by name; a host in two inventories is a row of each.
export function groupByInventory<T extends { inventory_id: string }>(items: T[], nameOf: (item: T) => string): Group<T>[] {
    return byGroupName(groupItems(items, item => item.inventory_id, nameOf));
}

// The list as it is shown: in groups by inventory, or in one group of key "".
export function hostGroups<T extends { inventory_id: string }>(items: T[], grouped: boolean, nameOf: (item: T) => string): Group<T>[] {
    return grouped ? groupByInventory(items, nameOf) : [{ key: "", name: "", items }];
}

export function isHostGroupOpen(view: HostListView, group: Group<unknown>): boolean {
    return isOpen(view.open, view.groupBy, group.key, true);
}

export function withHostGroupsOpen<V extends HostListView>(view: V, groups: Group<unknown>[], open: boolean): V {
    return { ...view, open: withOpen(view.open, view.groupBy, groups.map(group => group.key), open) };
}
