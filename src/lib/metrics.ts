// What the charts of a host draw from metric.query: one chart per metric,
// one line per instance, a break where values are missing.

import { formatBytes } from "./checks.ts";

export type Resolution = "raw" | "hour" | "day";

// A point as metric.query sends it: [time, value] as it came, or
// [start of the bucket, average, lowest, highest] for aggregates.
export type Point = [number, number] | [number, number, number, number];

export interface MetricSeries {
    check_id: string;
    check: string;
    name: string;
    instance: string;
    unit: string;
    points: Point[];
    truncated: boolean;
}

export interface MetricQuery {
    resolution: Resolution;
    since: number;
    until: number;
    series: MetricSeries[];
}

// A point as the chart takes it; y null breaks the line.
export interface Datum {
    x: number;
    y: number | null;
    low?: number;
    high?: number;
}

export interface Line {
    instance: string;
    data: Datum[];
}

export interface MetricChart {
    name: string;
    unit: string;
    lines: Line[];
    truncated: boolean;
}

export const SPANS = { day: 86400, week: 7 * 86400, month: 30 * 86400, year: 365 * 86400 } as const;
export type Span = keyof typeof SPANS;

// The usual distance between two values of a series: the median, so that a
// few late or missing values do not change it.
function usualStep(points: Point[]): number {
    const steps: number[] = [];
    for (let i = 1; i < points.length; i++) steps.push(points[i][0] - points[i - 1][0]);
    if (steps.length === 0) return 0;
    steps.sort((a, b) => a - b);
    return steps[Math.floor(steps.length / 2)];
}

// Seconds in, milliseconds out (what a date axis wants), with a point of
// no value wherever the distance from the previous one is well beyond the
// usual: a host that could not be reached is a gap, not a slope.
export function withGaps(points: Point[], resolution: Resolution): Datum[] {
    const step = resolution === "hour" ? 3600 : resolution === "day" ? 86400 : usualStep(points);
    const data: Datum[] = [];
    points.forEach((point, index) => {
        if (index > 0 && step > 0) {
            const previous = points[index - 1][0];
            if (point[0] - previous > step * 2.5) data.push({ x: (previous + step) * 1000, y: null });
        }
        data.push(point.length === 4
            ? { x: point[0] * 1000, y: point[1], low: point[2], high: point[3] }
            : { x: point[0] * 1000, y: point[1] });
    });
    return data;
}

// One chart per metric of a check, in the order the backend sends them.
export function chartsOf(query: MetricQuery): MetricChart[] {
    const charts = new Map<string, MetricChart>();
    for (const series of query.series) {
        let chart = charts.get(series.name);
        if (!chart) {
            chart = { name: series.name, unit: series.unit, lines: [], truncated: false };
            charts.set(series.name, chart);
        }
        chart.lines.push({ instance: series.instance, data: withGaps(series.points, query.resolution) });
        chart.truncated ||= series.truncated;
    }
    return [...charts.values()];
}

// The range of the values axis: a percentage always from 0 to 100, anything
// else from 0 when it is never negative, so that a flat line looks flat.
export function valueDomain(chart: MetricChart): [number, number] {
    if (chart.unit === "%") return [0, 100];
    let low = Infinity;
    let high = -Infinity;
    for (const line of chart.lines) {
        for (const datum of line.data) {
            if (datum.y === null) continue;
            low = Math.min(low, datum.low ?? datum.y);
            high = Math.max(high, datum.high ?? datum.y);
        }
    }
    if (!Number.isFinite(low)) return [0, 1];
    if (low >= 0) low = 0;
    if (high <= low) high = low + 1;
    return [low, high];
}

function round(value: number): string {
    const size = Math.abs(value);
    const scale = size >= 100 ? 1 : size >= 10 ? 10 : 100;
    return String(Math.round(value * scale) / scale);
}

// Ticks of the values axis for bytes, at round binary sizes (1, 2, 5, 10,
// 20... times a power of 1024), so that they read 10 GiB, not 9.3 GiB.
// Other units are left to the chart.
const BYTE_STEPS = [0, 1, 2, 3, 4, 5].flatMap(power =>
    [1, 2, 5, 10, 20, 50, 100, 200, 500].map(factor => factor * 1024 ** power));

export function valueTicks(domain: [number, number], unit: string, count = 4): number[] | undefined {
    if (unit !== "B") return undefined;
    const [low, high] = domain;
    const rough = (high - low) / count;
    const step = BYTE_STEPS.find(candidate => candidate >= rough);
    if (rough <= 0 || !step) return undefined;
    const ticks: number[] = [];
    for (let tick = Math.ceil(low / step) * step; tick <= high; tick += step) ticks.push(tick);
    return ticks;
}

// A value on an axis or in a tooltip: short, in the unit of the metric.
export function formatValue(value: number, unit: string): string {
    if (unit === "B") return formatBytes(Math.round(value));
    if (unit === "s") {
        if (value >= 86400) return `${round(value / 86400)} d`;
        if (value >= 3600) return `${round(value / 3600)} h`;
        if (value >= 60) return `${round(value / 60)} min`;
        return `${round(value)} s`;
    }
    if (unit === "%" || unit === "") return `${round(value)}${unit}`;
    return `${round(value)} ${unit}`;
}

// A time on the axis: hours over a day, days beyond.
export function formatTick(ms: number, span: Span, language: string): string {
    const options: Intl.DateTimeFormatOptions = span === "day"
        ? { hour: "2-digit", minute: "2-digit" }
        : span === "week" ? { weekday: "short", day: "numeric" } : { day: "numeric", month: "short" };
    return new Intl.DateTimeFormat(language, options).format(new Date(ms));
}

// A time in a tooltip: as precise as the values are. Daily buckets are UTC
// days, so their date is read in UTC.
export function formatPointTime(ms: number, resolution: Resolution, language: string): string {
    const options: Intl.DateTimeFormatOptions = resolution === "day"
        ? { dateStyle: "medium", timeZone: "UTC" }
        : { dateStyle: "short", timeStyle: "short" };
    return new Intl.DateTimeFormat(language, options).format(new Date(ms));
}
