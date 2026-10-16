// The fleet command that does at a terminal what a page does, to be copied:
// built from what the page knows, quoted for a POSIX shell.

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
export function launchCommand(template: string, answers: Record<string, unknown>, limit: string, check: boolean): string {
    const words = ["fleet", "launch", template];
    for (const [name, value] of Object.entries(answers)) {
        if (value === undefined || value === null || value === "") continue;
        words.push("-a", `${name}=${Array.isArray(value) ? value.join(",") : String(value)}`);
    }
    if (limit.trim()) words.push("--limit", limit.trim());
    if (check) words.push("--check");
    return shellLine(words);
}

// Following a run that goes on, or how it went once it ended.
export function runCommand(id: string, active: boolean): string {
    return shellLine(active ? ["fleet", "log", "-f", id] : ["fleet", "result", id]);
}

export function uploadCommand(project: string, file: string): string {
    return shellLine(["fleet", "project", "upload", project, "--archive", file || "ARCHIVE.tar.gz"]);
}
