// How a group of check cycles keeps to its interval, for the page of health.
import type { HealthGroup } from "./types.ts";

export type Keeping = "idle" | "ok" | "failing" | "memory" | "slow" | "late" | "behind" | "unprepared";

// The worst that can be said of a group. A cycle should end within half the
// shortest interval of what it checks; one that takes longer than the
// interval makes the next skip.
export function keeping(group: HealthGroup, budget: number): Keeping {
    if (group.unprepared) return "unprepared";
    if (group.behind) return "behind";
    if (group.skipped > 0 || (group.duration_max ?? 0) > group.interval) return "late";
    if (group.cycles === 0) return group.active ? "ok" : "idle";
    if ((group.duration_median ?? 0) > group.interval / 2) return "slow";
    if (budget > 0 && (group.memory_peak ?? 0) > budget) return "memory";
    if (group.unsuccessful > 0) return "failing";
    return "ok";
}

// No tone for a group with nothing measured yet.
export function keepingTone(state: Keeping): "good" | "warning" | "bad" | null {
    if (state === "behind" || state === "unprepared") return "bad";
    if (state === "ok") return "good";
    return state === "idle" ? null : "warning";
}

// A share as a whole percentage, or null when there is nothing to divide by.
export function share(part: number | null, whole: number): number | null {
    if (part === null || whole <= 0) return null;
    return Math.round(part * 100 / whole);
}
