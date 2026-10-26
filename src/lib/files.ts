// The files of a project, as the page that edits them shows them.

export interface FileNode {
    name: string;
    path: string;
    // Present on a directory, even an empty one.
    children?: FileNode[];
}

// The flat list of paths as a tree: directories first, each level by name.
export function buildTree(paths: string[]): FileNode[] {
    const root: FileNode = { name: "", path: "", children: [] };
    const directories = new Map<string, FileNode>([["", root]]);
    for (const path of paths) {
        const parts = path.split("/");
        let parent = root;
        for (let depth = 1; depth < parts.length; depth++) {
            const here = parts.slice(0, depth).join("/");
            let directory = directories.get(here);
            if (!directory) {
                directory = { name: parts[depth - 1], path: here, children: [] };
                directories.set(here, directory);
                parent.children!.push(directory);
            }
            parent = directory;
        }
        parent.children!.push({ name: parts[parts.length - 1], path });
    }
    const sort = (nodes: FileNode[]) => {
        nodes.sort((a, b) => Number(!a.children) - Number(!b.children) || a.name.localeCompare(b.name));
        for (const node of nodes) if (node.children) sort(node.children);
    };
    sort(root.children!);
    return root.children!;
}

// The directories a file is in, outermost first: "a/b/c.yml" gives "a", "a/b".
export function ancestors(path: string): string[] {
    const parts = path.split("/").slice(0, -1);
    return parts.map((_part, index) => parts.slice(0, index + 1).join("/"));
}

export function isYaml(path: string): boolean {
    return /\.ya?ml$/.test(path);
}

// What can be told about a typed path before asking the backend, which
// checks it again and more strictly.
export function isUsablePath(path: string): boolean {
    const parts = path.split("/");
    return path.trim() === path && parts.every(part => part !== "" && part !== "." && part !== ".." && part.toLowerCase() !== ".git");
}

// A path to offer in a field that takes one from a project.
export interface PathChoice {
    path: string;
    directory: boolean;
}

// Variables beside an inventory or a playbook, never one themselves.
function inVars(path: string): boolean {
    return path.split("/").some(part => part === "group_vars" || part === "host_vars");
}

// An inventory is a file or a directory of sources.
export function inventoryChoices(files: string[], directories: string[]): PathChoice[] {
    return [
        ...directories.filter(path => !inVars(path)).map(path => ({ path, directory: true })),
        ...files.filter(path => !inVars(path)).map(path => ({ path, directory: false })),
    ].sort((a, b) => a.path.localeCompare(b.path));
}

export function playbookChoices(files: string[]): PathChoice[] {
    return files.filter(path => isYaml(path) && !inVars(path)).map(path => ({ path, directory: false }));
}

// The choices that match what was typed, best first: those that start with
// it, then those with a directory or a name that does, then any that has it.
export function matchPaths(choices: PathChoice[], typed: string, limit: number): { shown: PathChoice[]; more: number } {
    const query = typed.trim().toLowerCase();
    const rank = (path: string) => {
        const lower = path.toLowerCase();
        if (lower.startsWith(query)) return 0;
        if (lower.includes("/" + query)) return 1;
        return lower.includes(query) ? 2 : -1;
    };
    const matching = choices
        .map((choice, index) => ({ choice, index, rank: rank(choice.path) }))
        .filter(entry => entry.rank >= 0)
        .sort((a, b) => a.rank - b.rank || a.index - b.index)
        .map(entry => entry.choice);
    return { shown: matching.slice(0, limit), more: Math.max(0, matching.length - limit) };
}
