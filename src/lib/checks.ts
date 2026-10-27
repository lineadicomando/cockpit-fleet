// What the pages of checks compute from what the backend sends: the look of
// a state, the thresholds as a form edits them, metrics for a person.

import { formatDuration } from "./format.ts";
import { byGroupName, byGroupOrder, groupItems, isOpen, oneOf, readObject, readOpen, readText, withOpen, type Group } from "./listview.ts";
import type { CheckDef, CheckKind, Metric, StateCounts, StateName, Thresholds } from "./types.ts";

// From the least to the most severe, as the backend orders them.
export const SEVERITY: StateName[] = ["ok", "warning", "unknown", "critical"];

export function worstState(counts: StateCounts): StateName | null {
    for (const state of [...SEVERITY].reverse())
        if (counts[state] > 0) return state;
    return null;
}

export function bySeverity<T extends { state: StateName | null }>(items: T[]): T[] {
    const rank = (state: StateName | null) => state === null ? -1 : SEVERITY.indexOf(state);
    return [...items].sort((a, b) => rank(b.state) - rank(a.state));
}

// A threshold as a form holds it: a string, empty for none.
export interface ThresholdField {
    warning: string;
    critical: string;
}

export function thresholdFields(thresholds: Thresholds): Record<string, ThresholdField> {
    const fields: Record<string, ThresholdField> = {};
    for (const [metric, limits] of Object.entries(thresholds))
        fields[metric] = { warning: limits.warning === null ? "" : String(limits.warning), critical: limits.critical === null ? "" : String(limits.critical) };
    return fields;
}

function numberOrNull(text: string): number | null {
    const trimmed = text.trim().replace(",", ".");
    if (!trimmed) return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : NaN;
}

// What the check sets in place of the defaults of the catalog: only what
// differs from them, so that a later change of a default still applies.
export function ownThresholds(defaults: Thresholds, fields: Record<string, ThresholdField>): Thresholds {
    const own: Thresholds = {};
    for (const [metric, field] of Object.entries(fields)) {
        const warning = numberOrNull(field.warning);
        const critical = numberOrNull(field.critical);
        const base = defaults[metric];
        if (base && base.warning === warning && base.critical === critical) continue;
        own[metric] = { warning, critical };
    }
    return own;
}

export function thresholdsValid(fields: Record<string, ThresholdField>): boolean {
    return Object.values(fields).every(field =>
        !Number.isNaN(numberOrNull(field.warning)) && !Number.isNaN(numberOrNull(field.critical)));
}

// One entry per line: paths, or names of services.
export function linesOf(text: string): string[] {
    return text.split("\n").map(line => line.trim()).filter(Boolean);
}

const BINARY = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];

export function formatBytes(bytes: number): string {
    let value = bytes;
    let unit = 0;
    while (Math.abs(value) >= 1024 && unit < BINARY.length - 1) {
        value /= 1024;
        unit++;
    }
    return unit === 0 ? `${value} B` : `${value.toFixed(value >= 100 ? 0 : 1)} ${BINARY[unit]}`;
}

export function formatMetric(metric: Metric): string {
    if (metric.unit === "B") return formatBytes(metric.value);
    if (metric.unit === "s" && metric.value >= 60) return formatDuration(metric.value);
    if (metric.unit === "%" || metric.unit === "") return `${metric.value}${metric.unit}`;
    return `${metric.value} ${metric.unit}`;
}

// "5 min", "1 h", "1 h 30 min", "2 d": how often a check runs.
export function formatInterval(seconds: number): string {
    if (seconds % 86400 === 0) return `${seconds / 86400} d`;
    if (seconds < 60) return `${seconds} s`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    return minutes % 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes / 60} h`;
}

// A moment as an <input type="datetime-local"> takes it, in local time, and back.
export function toLocalInput(seconds: number): string {
    const date = new Date(seconds * 1000);
    const pad = (value: number) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromLocalInput(text: string): number | null {
    if (!text) return null;
    const time = new Date(text).getTime();
    return Number.isNaN(time) ? null : Math.floor(time / 1000);
}

// --- the list of checks: filters, groups and order ---------------------------------

// The kinds of the catalog in four families, for filtering and grouping.
export type KindCategory = "resources" | "system" | "inventory" | "own";
export const KIND_CATEGORIES: KindCategory[] = ["resources", "system", "inventory", "own"];

const CATEGORY_OF: Record<CheckKind, KindCategory> = {
    disk: "resources", cpu: "resources", memory: "resources",
    reachability: "system", services: "system", uptime: "system", certificates: "system", clock: "system",
    cmdb: "inventory", updates: "inventory", compliance: "inventory",
    playbook: "own",
};

export function kindCategory(kind: CheckKind): KindCategory {
    return CATEGORY_OF[kind] ?? "own";
}

// Where a check stands as a whole: its worst state, or why it has none.
export type Standing = StateName | "pending" | "disabled";
export const STANDINGS: Standing[] = ["critical", "unknown", "warning", "ok", "pending", "disabled"];

export function standingOf(check: CheckDef): Standing {
    if (!check.enabled) return "disabled";
    return worstState(check.states) ?? "pending";
}

export function problemCount(counts: StateCounts): number {
    return counts.critical + counts.unknown + counts.warning;
}

export function sumCounts(checks: CheckDef[]): StateCounts {
    const sum: StateCounts = { ok: 0, warning: 0, critical: 0, unknown: 0, pending: 0 };
    for (const check of checks)
        for (const state of Object.keys(sum) as (keyof StateCounts)[])
            sum[state] += check.states[state];
    return sum;
}

export type StateFilter = "" | "problems" | "critical" | "ok" | "pending";
export type EnabledFilter = "" | "enabled" | "disabled";
export type GroupBy = "none" | "inventory" | "kind" | "state";
export type SortColumn = "name" | "interval" | "states";

// What the page shows of the list, kept in the browser between visits.
export interface ChecksView {
    search: string;
    state: StateFilter;
    inventory: string;
    category: "" | KindCategory;
    enabled: EnabledFilter;
    groupBy: GroupBy;
    sort: SortColumn;
    descending: boolean;
    // The maintenance windows that have ended are shown too.
    ended: boolean;
    // Groups opened or closed by hand, by "groupBy:key"; the others follow the default.
    open: Record<string, boolean>;
}

export const DEFAULT_VIEW: ChecksView = {
    search: "", state: "", inventory: "", category: "", enabled: "", groupBy: "inventory", sort: "name", descending: false, ended: false, open: {},
};

// The filters only, as "Clear all filters" leaves them.
export function withoutFilters(view: ChecksView): ChecksView {
    return { ...view, search: "", state: "", inventory: "", category: "", enabled: "" };
}

// A view as stored: anything not understood, from an older version or edited
// by hand, falls back to the default.
export function readView(text: string | null): ChecksView {
    const raw = readObject(text);
    return {
        search: readText(raw.search),
        state: oneOf(raw.state, ["", "problems", "critical", "ok", "pending"], ""),
        inventory: readText(raw.inventory, 64),
        category: oneOf(raw.category, ["", ...KIND_CATEGORIES], ""),
        enabled: oneOf(raw.enabled, ["", "enabled", "disabled"], ""),
        groupBy: oneOf(raw.groupBy, ["none", "inventory", "kind", "state"], DEFAULT_VIEW.groupBy),
        sort: oneOf(raw.sort, ["name", "interval", "states"], "name"),
        descending: raw.descending === true,
        ended: raw.ended === true,
        open: readOpen(raw.open),
    };
}

// How many filters narrow the list: the search counts as one.
export function activeFilters(view: ChecksView): number {
    return [view.search.trim(), view.state, view.inventory, view.category, view.enabled].filter(Boolean).length;
}

// The checks the filters let through; kindName gives the kind as the page
// shows it, so that a search finds "Disk space" as well as "disk".
export function filterChecks(checks: CheckDef[], view: ChecksView, kindName: (kind: CheckKind) => string = kind => kind): CheckDef[] {
    const needle = view.search.trim().toLowerCase();
    return checks.filter(check => {
        if (view.inventory && check.inventory_id !== view.inventory) return false;
        if (view.category && kindCategory(check.kind) !== view.category) return false;
        if (view.enabled === "enabled" && !check.enabled) return false;
        if (view.enabled === "disabled" && check.enabled) return false;
        const counts = check.states;
        if (view.state === "problems" && problemCount(counts) === 0) return false;
        if (view.state === "critical" && counts.critical === 0) return false;
        if (view.state === "ok" && (problemCount(counts) > 0 || counts.ok === 0)) return false;
        if (view.state === "pending" && counts.pending === 0) return false;
        if (needle) {
            const text = [check.name, check.kind, kindName(check.kind), check.inventory, check.group, check.playbook].join("\n").toLowerCase();
            if (!text.includes(needle)) return false;
        }
        return true;
    });
}

const byName = (a: CheckDef, b: CheckDef) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });

// By states: the worst standing first, then the most hosts with a problem.
export function sortChecks(checks: CheckDef[], column: SortColumn, descending: boolean): CheckDef[] {
    const compare: Record<SortColumn, (a: CheckDef, b: CheckDef) => number> = {
        name: byName,
        interval: (a, b) => a.interval - b.interval || byName(a, b),
        states: (a, b) => STANDINGS.indexOf(standingOf(a)) - STANDINGS.indexOf(standingOf(b))
            || problemCount(b.states) - problemCount(a.states) || byName(a, b),
    };
    const sorted = [...checks].sort(compare[column]);
    return descending ? sorted.reverse() : sorted;
}

export type CheckGroup = Group<CheckDef>;

// Groups in a fixed order: inventories by name, categories and standings as listed above.
export function groupChecks(checks: CheckDef[], by: GroupBy): CheckGroup[] {
    if (by === "none") return [{ key: "", name: "", items: checks }];
    if (by === "inventory") return byGroupName(groupItems(checks, check => check.inventory_id, check => check.inventory));
    if (by === "kind") return byGroupOrder(groupItems(checks, check => kindCategory(check.kind)), KIND_CATEGORIES);
    return byGroupOrder(groupItems(checks, standingOf), STANDINGS);
}

// Open unless the user closed it: a group with a problem, the only group,
// and every group while filtering; the others start closed.
export function isGroupOpen(view: ChecksView, group: CheckGroup, groups: number): boolean {
    return isOpen(view.open, view.groupBy, group.key,
        groups === 1 || activeFilters(view) > 0 || problemCount(sumCounts(group.items)) > 0);
}

// The view with these groups opened, or closed, by hand.
export function withGroupsOpen(view: ChecksView, groups: CheckGroup[], open: boolean): ChecksView {
    return { ...view, open: withOpen(view.open, view.groupBy, groups.map(group => group.key), open) };
}
