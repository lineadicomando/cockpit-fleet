// The files of a project, as the page that edits them shows them.

export interface FileNode {
    name: string;
    path: string;
    // Present on a directory, even an empty one.
    children?: FileNode[];
}

// The flat list of paths as a tree: directories first, each level by name.
// EMPTY are directories to show even with no file in them.
export function buildTree(paths: string[], empty: string[] = []): FileNode[] {
    const root: FileNode = { name: "", path: "", children: [] };
    const directories = new Map<string, FileNode>([["", root]]);
    const directory = (path: string): FileNode => {
        let node = directories.get(path);
        if (!node) {
            const parent = directory(dirname(path));
            node = { name: basename(path), path, children: [] };
            directories.set(path, node);
            parent.children!.push(node);
        }
        return node;
    };
    for (const path of empty) directory(path);
    for (const path of paths) directory(dirname(path)).children!.push({ name: basename(path), path });
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

export function basename(path: string): string {
    return path.slice(path.lastIndexOf("/") + 1);
}

// The directory a path is in, "" at the top.
export function dirname(path: string): string {
    const at = path.lastIndexOf("/");
    return at < 0 ? "" : path.slice(0, at);
}

// Whether PATH is DIRECTORY or inside it; everything is inside "" (the top).
export function isInside(path: string, directory: string): boolean {
    return directory === "" || path === directory || path.startsWith(directory + "/");
}

// The paths that are not inside another of them: moving a directory moves
// what is in it.
export function topmost(paths: string[]): string[] {
    const unique = [...new Set(paths)];
    return unique.filter(path => !unique.some(other => other !== path && isInside(path, other)));
}

// Where each of PATHS goes when it is moved or copied into the directory
// INTO, leaving out those already there. Null if one would go into itself.
export function relocate(paths: string[], into: string): { path: string; to: string }[] | null {
    const moves = [];
    for (const path of topmost(paths)) {
        if (into !== "" && isInside(into, path)) return null;
        const to = into ? `${into}/${basename(path)}` : basename(path);
        if (to !== path) moves.push({ path, to });
    }
    return moves;
}

// The new path of OLD after PATH became TO, or null if it was not in it.
export function renamed(old: string, path: string, to: string): string | null {
    if (old === path) return to;
    return old.startsWith(path + "/") ? to + old.slice(path.length) : null;
}

// The files whose path has QUERY in it (ignoring case), and the directories
// to open to show them.
export function filterFiles(files: string[], query: string): { files: string[]; open: Set<string> } {
    const wanted = query.trim().toLowerCase();
    if (!wanted) return { files, open: new Set() };
    const shown = files.filter(path => path.toLowerCase().includes(wanted));
    return { files: shown, open: new Set(shown.flatMap(ancestors)) };
}

// The nodes of a tree in the order they are shown, with those inside
// directories that are not open left out: what the arrow keys go through.
export function visibleNodes(nodes: FileNode[], open: Set<string>, level = 1): { node: FileNode; level: number }[] {
    return nodes.flatMap(node => [
        { node, level },
        ...(node.children && open.has(node.path) ? visibleNodes(node.children, open, level + 1) : []),
    ]);
}
