// The fleet command that does at a terminal what a page does, to be copied:
// built from what the page knows, quoted for a POSIX shell.

import type { Overrides } from "./types.ts";

// One word for the shell: as it is when it needs no quoting, else in single quotes.
export function shellWord(word: string): string {
    if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(word)) return word;
    return "'" + word.replace(/'/g, "'\\''") + "'";
}

export function shellLine(words: string[]): string {
    return words.map(shellWord).join(" ");
}

// fleet launch, with the answers a person may type: never those of secret
// questions, which fleet asks for at the terminal.
// inventory: the name of the one chosen, "" for the template's default.
export function launchCommand(template: string, answers: Record<string, unknown>, limit: string, check: boolean, serial = false, overrides: Overrides = {}, inventory = ""): string {
    // A name that begins with "-" would be read as an option: it goes last, after "--".
    const dashed = template.startsWith("-");
    const words = dashed ? ["fleet", "launch"] : ["fleet", "launch", template];
    for (const [name, value] of Object.entries(answers)) {
        if (value === undefined || value === null || value === "") continue;
        words.push("-a", `${name}=${Array.isArray(value) ? value.join(",") : String(value)}`);
    }
    if (inventory) words.push("--inventory", inventory);
    if (limit.trim()) words.push("--limit", limit.trim());
    if (check) words.push("--check");
    if (serial) words.push("--serial");
    if (overrides.tags) words.push("--tags", overrides.tags.join(","));
    if (overrides.skip_tags) words.push("--skip-tags", overrides.skip_tags.join(","));
    if (overrides.forks !== undefined) words.push("--forks", String(overrides.forks));
    if (overrides.verbosity !== undefined) words.push("--verbosity", String(overrides.verbosity));
    if (dashed) words.push("--", template);
    return shellLine(words);
}

// Following a run that goes on, or how it went once it ended.
export function runCommand(id: string, active: boolean): string {
    return shellLine(active ? ["fleet", "log", "-f", id] : ["fleet", "result", id]);
}

export function uploadCommand(project: string, file: string): string {
    return shellLine(["fleet", "project", "upload", project, "--archive", file || "ARCHIVE.tar.gz"]);
}
