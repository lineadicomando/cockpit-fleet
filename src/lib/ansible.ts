// What the editor suggests while a playbook is written: where the cursor is
// (a play, a task, the parameters of a module) and what fits there. Nothing
// here parses YAML: it reads indentation, which is enough for a suggestion
// and never in the way when it is wrong.

export type Scope = "play" | "task" | "role";

export interface Place {
    // "key": a name is being typed; "value": what follows "key: ".
    kind: "key" | "value";
    // Where the cursor is: a keyword scope, or the parameters of a module.
    scope: Scope | "args";
    // For "args", the module as it is written; for "value", the key.
    module: string;
    key: string;
    // What was typed so far of the word, which ends at the cursor.
    prefix: string;
}

export interface ModuleOption {
    name: string;
    type: string;
    required: boolean;
    default: unknown;
    choices: string[];
    aliases: string[];
    description: string;
}

export interface ModuleDoc {
    name: string;
    description: string;
    options: ModuleOption[];
}

export interface Suggestion {
    label: string;
    kind: "keyword" | "module" | "option" | "value";
    detail?: string;
    info?: string;
    // What is written when it is chosen, if not the label.
    apply?: string;
    // Shown before the others that match as well.
    first?: boolean;
}

const COMMON = [
    "any_errors_fatal", "become", "become_exe", "become_flags", "become_method", "become_user", "check_mode",
    "collections", "connection", "debugger", "diff", "environment", "ignore_errors", "ignore_unreachable",
    "module_defaults", "name", "no_log", "port", "remote_user", "run_once", "tags", "throttle", "timeout", "vars",
];

export const KEYWORDS: Record<Scope, string[]> = {
    play: [...COMMON, "fact_path", "force_handlers", "gather_facts", "gather_subset", "gather_timeout", "handlers",
        "hosts", "import_playbook", "max_fail_percentage", "order", "post_tasks", "pre_tasks", "roles", "serial",
        "strategy", "tasks", "vars_files", "vars_prompt"].sort(),
    task: [...COMMON, "action", "always", "args", "async", "block", "changed_when", "delay", "delegate_facts",
        "delegate_to", "failed_when", "listen", "local_action", "loop", "loop_control", "notify", "poll", "register",
        "rescue", "retries", "until", "when"].sort(),
    role: ["become", "become_user", "delegate_to", "role", "tags", "vars", "when"],
};

const BOOLEANS = new Set(["any_errors_fatal", "become", "check_mode", "delegate_facts", "diff", "force_handlers",
    "gather_facts", "ignore_errors", "ignore_unreachable", "no_log", "run_once"]);
// Keys whose value is a list of tasks.
const TASK_LISTS = new Set(["tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"]);
const TASK_KEYWORDS = new Set(KEYWORDS.task);

interface Line {
    // Column of the first character of the key: past the dash of a list item.
    column: number;
    dash: boolean;
    key: string;
    // "key:" with nothing after it: what follows, further in, belongs to it.
    open: boolean;
}

function readLine(text: string): Line | null {
    const parts = /^( *)(- +)?(.*)$/.exec(text);
    if (!parts) return null;
    const rest = parts[3];
    if (!parts[2] && (rest.trim() === "" || rest.startsWith("#") || rest === "---")) return null;
    const pair = /^([A-Za-z0-9_.]+) *:(?: +(.*))?$/.exec(rest);
    const value = (pair?.[2] ?? "").replace(/(^| )#.*$/, "").trim();
    return { column: parts[1].length + (parts[2]?.length ?? 0), dash: !!parts[2], key: pair?.[1] ?? "", open: !!pair && value === "" };
}

// The mapping whose keys are at `column`, looked at from line `index` upwards:
// the key it is the value of, and whether it is an item of a list there.
function parentOf(lines: (Line | null)[], index: number, column: number): { parent: (Line & { index: number }) | null; listed: boolean } {
    let listed = false;
    for (let i = index; i >= 0; i--) {
        const line = lines[i];
        if (!line) continue;
        if (line.column === column && line.dash) listed = true;
        if (line.column < column) return { parent: { ...line, index: i }, listed };
    }
    return { parent: null, listed };
}

function scopeOf(lines: (Line | null)[], index: number, column: number, path: string, depth = 0): Scope | { module: string } | null {
    const { parent, listed } = parentOf(lines, index, column);
    if (!parent) {
        if (!listed) return null;
        // The file itself is a list: of tasks in a role, of plays otherwise.
        return /(^|\/)(tasks|handlers)\//.test(path) ? "task" : "play";
    }
    if (!parent.open) return null;
    if (listed) return TASK_LISTS.has(parent.key) ? "task" : parent.key === "roles" ? "role" : null;
    if (depth > 0 || TASK_KEYWORDS.has(parent.key)) return null;
    // A key of a task that is not a keyword is the module the task calls.
    return scopeOf(lines, parent.index, parent.column, path, 1) === "task" ? { module: parent.key } : null;
}

// Where the cursor is, given the text before it and the path of the file.
export function placeAt(before: string, path: string): Place | null {
    const texts = before.split("\n");
    const current = texts[texts.length - 1];
    const typed = /^( *)(- +)?(?:([A-Za-z0-9_.]+) *: +)?([A-Za-z0-9_.]*)$/.exec(current);
    if (!typed) return null;
    const column = typed[1].length + (typed[2]?.length ?? 0);
    const lines = texts.map(readLine);
    // The line being typed is not a key yet, but it says where one would be.
    lines[lines.length - 1] = { column, dash: !!typed[2], key: "", open: false };
    const scope = scopeOf(lines, lines.length - 1, column, path);
    if (!scope) return null;
    const base = typeof scope === "string" ? { scope, module: "" } : { scope: "args" as const, module: scope.module };
    return { ...base, kind: typed[3] ? "value" : "key", key: typed[3] ?? "", prefix: typed[4] };
}

// The name a module is installed under, for one written in full or in short.
export function resolveModule(written: string, modules: Record<string, string>): string | null {
    const known = (name: string) => Object.prototype.hasOwnProperty.call(modules, name);
    if (known(written)) return written;
    const builtin = `ansible.builtin.${written}`;
    return known(builtin) ? builtin : null;
}

function about(option: ModuleOption): string {
    const parts = [option.type];
    if (option.required) parts.push("required");
    else if (option.default !== null && option.default !== undefined) parts.push(`= ${JSON.stringify(option.default)}`);
    return parts.join(", ");
}

// What fits at a place. `modules` and `doc` are what the backend knows; with
// neither, only the keywords are suggested.
export function suggest(place: Place, modules: Record<string, string> = {}, doc: ModuleDoc | null = null): Suggestion[] {
    if (place.kind === "value") {
        const option = place.scope === "args" ? doc?.options.find(candidate => candidate.name === place.key || candidate.aliases.includes(place.key)) : undefined;
        const values = option
            ? (option.choices.length ? option.choices : option.type === "bool" ? ["true", "false"] : [])
            : place.scope !== "args" && BOOLEANS.has(place.key) ? ["true", "false"] : [];
        return values.map(label => ({ label, kind: "value" }));
    }
    if (place.scope === "args") {
        return (doc?.options ?? []).map(option => ({
            label: option.name, kind: "option", detail: about(option), info: option.description,
            apply: `${option.name}: `, first: option.required,
        }));
    }
    const keywords: Suggestion[] = KEYWORDS[place.scope].map(label => ({ label, kind: "keyword", apply: `${label}: `, first: true }));
    if (place.scope !== "task") return keywords;
    return [...keywords, ...Object.entries(modules).map(([label, info]): Suggestion => ({ label, kind: "module", info, apply: `${label}:` }))];
}

// How far a new line goes in after `previous`, in YAML: under the key of a
// list item, and one step further when the line opens a mapping or a list.
export function indentAfter(previous: string, step = 2): number {
    const parts = /^( *)((?:- +)*)(.*)$/.exec(previous)!;
    const rest = parts[3].replace(/(^| )#.*$/, "").trimEnd();
    const column = parts[1].length + parts[2].length;
    return /(^|[^:]):( [|>][+-]?)?$/.test(rest) || rest === "-" ? column + step : column;
}
