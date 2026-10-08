// Reads the run's event stream (one JSON object per line) into what the
// task view shows: for each task, how it went on each host.

export type HostOutcome = "ok" | "changed" | "failed" | "unreachable" | "skipped";

export interface TaskView {
    uuid: string;
    name: string;
    play: string;
    hosts: Record<string, HostOutcome>;
    // What Ansible said for the hosts where the task did not succeed.
    messages: Record<string, string>;
}

interface RawEvent {
    event: string;
    play?: string;
    task?: string;
    task_uuid?: string;
    host?: string;
    changed?: boolean;
    ignored?: boolean;
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
            const task = { uuid: event.task_uuid, name: event.task ?? "", play: current, hosts: {}, messages: {} };
            byUuid.set(task.uuid, task);
            result.push(task);
        } else if (event.event.startsWith("host_") && event.task_uuid && event.host) {
            const task = byUuid.get(event.task_uuid);
            if (!task) continue;
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
