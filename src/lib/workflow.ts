import type { WorkflowEdge } from "./types.ts";

// The steps in columns: each one after the longest chain of steps it follows.
export function layers(nodes: string[], edges: WorkflowEdge[]): string[][] {
    const depth = new Map<string, number>();
    const visit = (node: string, seen: Set<string>): number => {
        const known = depth.get(node);
        if (known !== undefined) return known;
        if (seen.has(node)) return 0; // a loop: the backend refuses them
        seen.add(node);
        const parents = edges.filter(edge => edge.to === node).map(edge => edge.from).filter(parent => nodes.includes(parent));
        const value = parents.length ? Math.max(...parents.map(parent => visit(parent, seen))) + 1 : 0;
        depth.set(node, value);
        return value;
    };
    const columns: string[][] = [];
    for (const node of nodes) {
        const at = visit(node, new Set());
        (columns[at] ??= []).push(node);
    }
    return columns.filter(column => column);
}

// An id for a new step: s1, s2 … the first one not taken.
export function nextStepId(taken: string[]): string {
    for (let n = 1; ; n++) {
        const id = `s${n}`;
        if (!taken.includes(id)) return id;
    }
}
