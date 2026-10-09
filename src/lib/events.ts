// Reads the run's event stream (one JSON object per line) into what the
// task view shows: for each task, how it went on each host and what it changed.

export type HostOutcome = "ok" | "changed" | "failed" | "unreachable" | "skipped";

export interface TaskView {
    uuid: string;
    name: string;
    play: string;
    hosts: Record<string, HostOutcome>;
    // What Ansible said for the hosts where the task did not succeed.
    messages: Record<string, string>;
    diffs: TaskDiff[];
}

// What a task changed on a host, or would change in a dry run, as a unified
// diff. A task with a loop has one for each element that says so.
export interface TaskDiff {
    host: string;
    item: string;
    text: string;
    // The callback cut it: the whole of it is in the run's output.
    cut: boolean;
}

export interface HostChanges {
    host: string;
    changes: (TaskDiff & { task: string; play: string })[];
}

interface RawEvent {
    event: string;
    play?: string;
    task?: string;
    task_uuid?: string;
    host?: string;
    item?: string;
    msg?: string;
    changed?: boolean;
    ignored?: boolean;
    diff?: string;
    diff_cut?: boolean;
    result?: { msg?: unknown; stderr?: unknown };
}

export function addEvents(tasks: TaskView[], text: string, play = ""): { tasks: TaskView[]; play: string } {
    // Copies, so that the list passed in (React state) is never modified.
    const result = tasks.map(task => ({ ...task }));
    const byUuid = new Map(result.map(task => [task.uuid, task]));
    let current = play;

    for (const line of text.split("\n")) {
        if (!line.trim()) continue;
        let event: RawEvent;
        try {
            event = JSON.parse(line);
        } catch {
            continue;
        }
        if (event.event === "play_start") {
            current = event.play ?? "";
        } else if (event.event === "task_start" && event.task_uuid && !byUuid.has(event.task_uuid)) {
            const task = { uuid: event.task_uuid, name: event.task ?? "", play: current, hosts: {}, messages: {}, diffs: [] };
            byUuid.set(task.uuid, task);
            result.push(task);
        } else if (event.event.startsWith("item_") && event.task_uuid && event.host) {
            const task = byUuid.get(event.task_uuid);
            if (!task) continue;
            const item = event.item ?? "";
            // One element of a loop failed: say which, the task's own message is generic.
            if (event.event === "item_failed") task.messages = { ...task.messages, [`${event.host} [${item}]`]: event.msg ?? "" };
            if (event.diff) task.diffs = [...task.diffs, { host: event.host, item, text: event.diff, cut: !!event.diff_cut }];
        } else if (event.event.startsWith("host_") && event.task_uuid && event.host) {
            const task = byUuid.get(event.task_uuid);
            if (!task) continue;
            if (event.diff) task.diffs = [...task.diffs, { host: event.host, item: "", text: event.diff, cut: !!event.diff_cut }];
            const kind = event.event.slice(5);
            const outcome: HostOutcome = kind === "ok" ? (event.changed ? "changed" : "ok") : kind as HostOutcome;
            task.hosts = { ...task.hosts, [event.host]: outcome };
            if (outcome === "failed" || outcome === "unreachable") {
                const said = event.result?.msg ?? event.result?.stderr ?? "";
                task.messages = { ...task.messages, [event.host]: typeof said === "string" ? said : JSON.stringify(said) };
            }
        }
    }
    return { tasks: result, play: current };
}

export function countOutcomes(task: TaskView): Record<HostOutcome, number> {
    const counts = { ok: 0, changed: 0, failed: 0, unreachable: 0, skipped: 0 };
    for (const outcome of Object.values(task.hosts)) counts[outcome] += 1;
    return counts;
}

// The same diffs seen from the hosts: for each one, what the run changed on
// it, in the order of the tasks.
export function changesByHost(tasks: TaskView[]): HostChanges[] {
    const byHost = new Map<string, HostChanges>();
    for (const task of tasks) {
        for (const diff of task.diffs) {
            let entry = byHost.get(diff.host);
            if (!entry) {
                entry = { host: diff.host, changes: [] };
                byHost.set(diff.host, entry);
            }
            entry.changes.push({ ...diff, task: task.name, play: task.play });
        }
    }
    return [...byHost.values()].sort((a, b) => a.host.localeCompare(b.host));
}

export type DiffLineKind = "added" | "removed" | "hunk" | "header" | "context";

export function diffLines(text: string): { kind: DiffLineKind; text: string }[] {
    const lines = text.replace(/\n+$/, "").split("\n");
    return lines.map(line => {
        let kind: DiffLineKind = "context";
        if (line.startsWith("+++ ") || line.startsWith("--- ")) kind = "header";
        else if (line.startsWith("@@")) kind = "hunk";
        else if (line.startsWith("+")) kind = "added";
        else if (line.startsWith("-")) kind = "removed";
        return { kind, text: line };
    });
}
