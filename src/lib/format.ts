// Dates and durations as the interface shows them.

export function formatTime(seconds: number | null | undefined, language: string): string {
    if (!seconds) return "";
    return new Intl.DateTimeFormat(language, { dateStyle: "short", timeStyle: "medium" })
        .format(new Date(seconds * 1000));
}

// "42 s", "3 min 05 s", "2 h 07 min": precise when short, rounded when long.
export function formatDuration(seconds: number): string {
    const total = Math.max(0, Math.round(seconds));
    if (total < 60) return `${total} s`;
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes} min ${String(total % 60).padStart(2, "0")} s`;
    return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

export function runDuration(
    run: { started_at: number | null; finished_at: number | null },
    now: number,
): string {
    if (!run.started_at) return "";
    return formatDuration((run.finished_at ?? now) - run.started_at);
}
