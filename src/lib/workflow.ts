import type { SearchGroup } from "./search.ts";
import type { Template, WorkflowEdge } from "./types.ts";

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

// The project and the inventory a workflow takes its steps from: "" is any.
export interface StepScope {
    project_id: string;
    inventory_id: string;
}

// Whether a template may be a step of a workflow of this project and inventory:
// of the project, and allowing the inventory, where every step runs.
export function inScope(template: Pick<Template, "project_id" | "inventory_id" | "inventory_ids" | "any_inventory">, scope: StepScope): boolean {
    return (!scope.project_id || template.project_id === scope.project_id)
        && (!scope.inventory_id || template.any_inventory || template.inventory_id === scope.inventory_id
            || (template.inventory_ids ?? []).includes(scope.inventory_id));
}

// The job templates a step may take, by inventory unless the workflow names one, by name in each,
// each with its project and playbook under it. A group with no label is the one of a workflow that
// names its inventory.
export function templateGroups(offered: Template[], scope: StepScope, inventories: Map<string, string>, projects: Map<string, string>): SearchGroup[] {
    const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });
    const groups = new Map<string, Template[]>();
    for (const item of offered) {
        const key = scope.inventory_id ? "" : item.inventory_id;
        groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()]
        .map(([id, items]) => ({
            label: id ? inventories.get(id) ?? id : "",
            options: [...items].sort((a, b) => byName(a.name, b.name)).map(item => ({
                id: item.id,
                label: item.name,
                description: [projects.get(item.project_id) ?? "", item.playbook].filter(Boolean).join(" · "),
            })),
        }))
        .sort((a, b) => byName(a.label, b.label));
}
