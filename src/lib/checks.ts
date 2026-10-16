// What the pages of checks compute from what the backend sends: the look of
// a state, the thresholds as a form edits them, metrics for a person.

import { formatDuration } from "./format.ts";
import type { Metric, StateCounts, StateName, Thresholds } from "./types.ts";

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
