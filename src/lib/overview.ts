// What overview.get answers, and how the Overview page turns it into
// figures and chart data, apart from the page so that it can be tested.

import type { RunStatus, StateCounts, StateName } from "./types.ts";

export const WINDOWS = [7, 14, 30] as const;
export type Window = typeof WINDOWS[number];
export const DEFAULT_WINDOW: Window = 14;

// A state, or a check not run yet on a host.
export type CountName = StateName | "pending";
export const STATE_ORDER: CountName[] = ["critical", "warning", "unknown", "pending", "ok"];

export interface RunDay { start: number; successful: number; failed: number; stopped: number }
export interface AlertDay { start: number; problems: number; recoveries: number }

export interface Problem {
    host_id: string;
    host: string;
    check_id: string;
    check: string;
    state: Exclude<StateName, "ok">;
    summary: string;
    since: number | null;
    acknowledged: boolean;
    flapping: boolean;
    in_maintenance: boolean;
    suppressed: boolean;
}

export interface Overview {
    now: number;
    since: number;
    hosts: {
        total: number;
        reachable: number;
        unreachable: number;
        unknown: number;
        in_maintenance: number;
        systems: { name: string; hosts: number }[];
        uptime: { days: number; known: number; longer: number };
    };
    states: StateCounts;
    problems: { open: number; unacknowledged: number; top: Problem[] };
    runs: {
        days: RunDay[];
        successful: number;
        failed: number;
        stopped: number;
        success_rate: number | null;
        duration_median: number | null;
        duration_max: number | null;
        recent_failures: { id: string; title: string; status: RunStatus; created_by: string; finished_at: number | null }[];
        templates: { id: string; name: string; runs: number; failed: number }[];
        hosts: { name: string; failed: number }[];
        queue: { pending_approval: number; queued: number; running: number };
    };
    alerts: { days: AlertDay[]; recovered: number; time_to_recover: number | null };
    updates: { updates: number; security: number; hosts: number; security_hosts: number; known: number };
    compliance: { compliant: number; drifted: number };
    upcoming: {
        schedules: {
            id: string; name: string; next_at: number;
            template_id: string | null; template: string; workflow_id: string | null; workflow: string;
        }[];
        maintenances: {
            id: string; starts_at: number; ends_at: number; host_group: string; comment: string;
            inventory: string; host: string; active: boolean;
        }[];
    };
    controller: { behind: number; unprepared: number; notifications: { failed: number; pending: number } };
}

// Minutes east of UTC, as overview.get takes them, for a date.
export function tzOffset(date: Date): number {
    return -date.getTimezoneOffset();
}

export function readWindow(text: string | null): Window {
    const value = Number(text);
    return (WINDOWS as readonly number[]).includes(value) ? value as Window : DEFAULT_WINDOW;
}

// "92 %", rounded down below 100 so that one failure never shows as 100 %.
export function formatRate(rate: number | null, language: string): string {
    if (rate === null) return "–";
    const percent = rate < 1 ? Math.min(Math.floor(rate * 1000) / 10, 99.9) : 100;
    return new Intl.NumberFormat(language, { style: "percent", maximumFractionDigits: 1 }).format(percent / 100);
}

// The slices of the states, worst first, without the empty ones.
export function stateSlices(states: StateCounts): { state: CountName; count: number }[] {
    return STATE_ORDER.map(state => ({ state, count: states[state] ?? 0 })).filter(slice => slice.count > 0);
}

export function totalOf(states: StateCounts): number {
    return STATE_ORDER.reduce((sum, state) => sum + (states[state] ?? 0), 0);
}

// "16 nov", in the language of the page, for the day starting at a time.
export function dayLabel(start: number, language: string): string {
    return new Intl.DateTimeFormat(language, { day: "numeric", month: "short" }).format(new Date(start * 1000));
}

// Whether any day has something to draw.
export function anyDay<T extends { start: number }>(days: T[], keys: (keyof T)[]): boolean {
    return days.some(day => keys.some(key => Number(day[key]) > 0));
}

// The ticks of the days axis: every day up to a week, then one in two up
// to a fortnight and one in five beyond, always the last (today) included.
export function dayTicks(starts: number[]): number[] {
    const step = starts.length <= 7 ? 1 : starts.length <= 14 ? 2 : 5;
    const last = starts.length - 1;
    return starts.filter((_start, index) => (last - index) % step === 0);
}

// The ticks of a count axis up to at least `highest`: whole numbers, about
// four steps, starting at 0.
export function countTicks(highest: number): number[] {
    const step = Math.max(1, Math.ceil(highest / 4));
    const top = Math.max(step, Math.ceil(highest / step) * step);
    const ticks: number[] = [];
    for (let value = 0; value <= top; value += step) ticks.push(value);
    return ticks;
}

// The systems of the hosts, the most common first, folded into "other"
// beyond `keep`: a name of "" is a host whose system is not known yet.
export function topSystems(systems: { name: string; hosts: number }[], keep = 5): { name: string; hosts: number; other?: boolean }[] {
    const known = systems.filter(system => system.name !== "");
    const unknown = systems.find(system => system.name === "")?.hosts ?? 0;
    const shown: { name: string; hosts: number; other?: boolean }[] = known.slice(0, keep);
    const rest = known.slice(keep).reduce((sum, system) => sum + system.hosts, 0);
    if (rest > 0) shown.push({ name: "", hosts: rest, other: true });
    if (unknown > 0) shown.push({ name: "", hosts: unknown });
    return shown;
}

// Whether the controller has something to say: cycles behind or that could
// not be prepared, notifications that could not be delivered.
export function controllerTrouble(controller: Overview["controller"]): boolean {
    return controller.behind > 0 || controller.unprepared > 0 || controller.notifications.failed > 0;
}
