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
