// The steps of a workflow in columns, the job templates a step may take, and
// the list of workflows: filters, groups and order, from what workflow.list,
// template.list and schedule.list return.

import { byGroupName, byGroupOrder, isOpen, oneOf, readObject, readOpen, readText, withOpen, type Group } from "./listview.ts";
import type { SearchGroup } from "./search.ts";
import { OUTCOMES, outcomeOfRun, type Outcome } from "./templates.ts";
import type { ApprovalMode, StepCondition, Template, Workflow, WorkflowEdge, WorkflowRun } from "./types.ts";

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
// names its inventory; those with no default inventory are under noDefault.
export function templateGroups(offered: Template[], scope: StepScope, inventories: Map<string, string>, projects: Map<string, string>,
    noDefault = ""): SearchGroup[] {
    const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });
    const groups = new Map<string | null, Template[]>();
    for (const item of offered) {
        const key = scope.inventory_id ? "" : item.inventory_id;
        groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return [...groups.entries()]
        .map(([id, items]) => ({
            label: id === null ? noDefault : id ? inventories.get(id) ?? id : "",
            options: [...items].sort((a, b) => byName(a.name, b.name)).map(item => ({
                id: item.id,
                label: item.name,
                description: [projects.get(item.project_id) ?? "", item.playbook].filter(Boolean).join(" · "),
            })),
        }))
        .sort((a, b) => byName(a.label, b.label));
}

// A step as the list shows it: how it is reached, when not only by success.
export interface StepCell {
    id: string;
    name: string;
    // failure: only when a step before it fails; always: whatever they did; "" otherwise.
    branch: "" | "failure" | "always";
    // How it waits for a person before it starts, "none" if it does not.
    approval: ApprovalMode;
}

// The steps in the columns of layers(), each marked by the links that reach it:
// steps of a column may start together, or be alternatives, one on failure.
export function stepColumns<N extends { id: string }>(nodes: N[], edges: WorkflowEdge[], name: (node: N) => string,
    approval: (node: N) => ApprovalMode): StepCell[][] {
    const byId = new Map(nodes.map(node => [node.id, node]));
    const branchOf = (id: string): StepCell["branch"] => {
        const conditions = new Set<StepCondition>(edges.filter(edge => edge.to === id && byId.has(edge.from)).map(edge => edge.condition));
        if (conditions.size !== 1) return "";
        const [only] = conditions;
        return only === "success" ? "" : only;
    };
    return layers(nodes.map(node => node.id), edges).map(column => column.map(id => {
        const node = byId.get(id)!;
        return { id, name: name(node), branch: branchOf(id), approval: approval(node) };
    }));
}

// How a step waits for a person: by its own mode or its template's, whichever asks more.
export function stepApproval(node: { approval: ApprovalMode; template_id: string }, template: (id: string) => Template | undefined): ApprovalMode {
    const modes: ApprovalMode[] = ["none", "confirm", "other"];
    const theirs = template(node.template_id)?.approval ?? "none";
    return modes.indexOf(theirs) > modes.indexOf(node.approval) ? theirs : node.approval;
}

// Whether a step waits for a person.
export function stepAsksApproval(node: { approval: ApprovalMode; template_id: string }, template: (id: string) => Template | undefined): boolean {
    return stepApproval(node, template) !== "none";
}

// --- the list of workflows ---------------------------------------------------

// The group of the runs on each step's own inventory, among the inventories.
export const EACH_STEPS_OWN = "";

export type WorkflowTrait = "" | "approval" | "choose" | "scheduled" | "unscheduled";
export type WorkflowGroupBy = "inventory" | "project" | "outcome" | "none";
export type WorkflowSort = "name" | "last_run";

export interface WorkflowsView {
    search: string;
    project: string;
    inventory: string;
    trait: WorkflowTrait;
    outcome: "" | Outcome;
    groupBy: WorkflowGroupBy;
    sort: WorkflowSort;
    descending: boolean;
    // Groups opened or closed by hand, by "groupBy:key"; the others are open.
    open: Record<string, boolean>;
}

export const DEFAULT_WORKFLOWS_VIEW: WorkflowsView = {
    search: "", project: "", inventory: "", trait: "", outcome: "", groupBy: "inventory", sort: "name", descending: false, open: {},
};

export function readWorkflowsView(text: string | null): WorkflowsView {
    const raw = readObject(text);
    return {
        search: readText(raw.search),
        project: readText(raw.project, 64),
        inventory: readText(raw.inventory, 64),
        trait: oneOf(raw.trait, ["", "approval", "choose", "scheduled", "unscheduled"], ""),
        outcome: oneOf(raw.outcome, ["", ...OUTCOMES], ""),
        groupBy: oneOf(raw.groupBy, ["inventory", "project", "outcome", "none"], DEFAULT_WORKFLOWS_VIEW.groupBy),
        sort: oneOf(raw.sort, ["name", "last_run"], "name"),
        descending: raw.descending === true,
        open: readOpen(raw.open),
    };
}

export function withoutWorkflowFilters(view: WorkflowsView): WorkflowsView {
    return { ...view, search: "", project: "", inventory: "", trait: "", outcome: "" };
}

// What the page knows: the templates, the names of projects and inventories
// ("" names each step's own), how many schedules launch each workflow, the
// inventories runs may be on (those kept as starters left out).
export interface WorkflowNames {
    template: (id: string) => Template | undefined;
    project: (id: string) => string;
    inventory: (id: string) => string;
    schedules: (id: string) => number;
    inventories: string[];
}

export const stepName = (node: { id: string; name: string; template_id: string }, names: Pick<WorkflowNames, "template">) =>
    node.name || names.template(node.template_id)?.name || node.id;

// Where a workflow may run: its inventory, else those a launch may choose,
// and each step's own when it may be launched without one.
export function workflowInventories(workflow: Workflow, usable: string[]): string[] {
    if (workflow.inventory_id) return [workflow.inventory_id];
    const chosen = workflow.inventory_ids === null ? usable : workflow.inventory_ids.filter(id => usable.includes(id));
    return workflow.needs_inventory ? [...chosen] : [...chosen, EACH_STEPS_OWN];
}

// Its project, else those of the templates of its steps.
export function workflowProjects(workflow: Workflow, names: Pick<WorkflowNames, "template">): string[] {
    if (workflow.project_id) return [workflow.project_id];
    return [...new Set(workflow.nodes.map(node => names.template(node.template_id)?.project_id).filter((id): id is string => !!id))];
}

export function lastWorkflowRun(workflow: Workflow, where?: string): WorkflowRun | null {
    return where === undefined ? workflow.last_run : workflow.last_runs?.[where] ?? null;
}

export function filterWorkflows(workflows: Workflow[], view: WorkflowsView, names: WorkflowNames): Workflow[] {
    const needle = view.search.trim().toLowerCase();
    return workflows.filter(workflow => {
        if (view.project && !workflowProjects(workflow, names).includes(view.project)) return false;
        if (view.inventory && !workflowInventories(workflow, names.inventories).includes(view.inventory)) return false;
        if (view.outcome && outcomeOfRun(workflow.last_run) !== view.outcome) return false;
        if (view.trait === "approval" && !workflow.nodes.some(node => stepAsksApproval(node, names.template))) return false;
        if (view.trait === "choose" && workflow.inventory_id) return false;
        if (view.trait === "scheduled" && names.schedules(workflow.id) === 0) return false;
        if (view.trait === "unscheduled" && names.schedules(workflow.id) > 0) return false;
        if (needle) {
            const text = [workflow.name, workflow.description, ...workflow.nodes.map(node => stepName(node, names)),
                ...workflow.nodes.map(node => names.template(node.template_id)?.name ?? ""),
                ...workflowProjects(workflow, names).map(names.project),
                ...workflowInventories(workflow, names.inventories).filter(Boolean).map(names.inventory)].join("\n").toLowerCase();
            if (!text.includes(needle)) return false;
        }
        return true;
    });
}

const compareText = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

// By last run (on the inventory `where`, if given): the oldest first, those never launched before any.
export function sortWorkflows(workflows: Workflow[], column: WorkflowSort, descending: boolean, where?: string): Workflow[] {
    const byName = (a: Workflow, b: Workflow) => compareText(a.name, b.name);
    const compare: Record<WorkflowSort, (a: Workflow, b: Workflow) => number> = {
        name: byName,
        last_run: (a, b) => (lastWorkflowRun(a, where)?.created_at ?? 0) - (lastWorkflowRun(b, where)?.created_at ?? 0) || byName(a, b),
    };
    const sorted = [...workflows].sort(compare[column]);
    return descending ? sorted.reverse() : sorted;
}

export type WorkflowGroup = Group<Workflow>;

// Under each key a workflow has, by name; each step's own after the inventories.
function groupUnder(workflows: Workflow[], keys: (workflow: Workflow) => string[], name: (key: string) => string): WorkflowGroup[] {
    const groups = new Map<string, WorkflowGroup>();
    for (const workflow of workflows) {
        for (const key of keys(workflow)) {
            const group = groups.get(key) ?? { key, name: name(key), items: [] };
            group.items.push(workflow);
            groups.set(key, group);
        }
    }
    const named = byGroupName([...groups.values()].filter(group => group.key !== EACH_STEPS_OWN));
    const own = groups.get(EACH_STEPS_OWN);
    return own ? [...named, own] : named;
}

// The groups the page shows and how many workflows are in them. Under an
// inventory a row speaks of that inventory: its last run there decides the
// filter by outcome and the order by last run.
export function listWorkflows(workflows: Workflow[], view: WorkflowsView, names: WorkflowNames): { groups: WorkflowGroup[]; shown: number } {
    if (view.groupBy === "none" || view.groupBy === "outcome" || view.groupBy === "project") {
        const shown = sortWorkflows(filterWorkflows(workflows, view, names), view.sort, view.descending);
        const groups = view.groupBy === "none" ? [{ key: "", name: "", items: shown }]
            : view.groupBy === "project" ? groupUnder(shown, workflow => workflowProjects(workflow, names), names.project)
            : byGroupOrder(groupUnder(shown, workflow => [outcomeOfRun(workflow.last_run)], () => ""), OUTCOMES);
        return { groups, shown: shown.length };
    }
    const kept = filterWorkflows(workflows, { ...view, outcome: "" }, names);
    const groups = groupUnder(kept, workflow => workflowInventories(workflow, names.inventories), names.inventory)
        .map(group => ({
            ...group,
            items: sortWorkflows(group.items.filter(item => !view.outcome || outcomeOfRun(lastWorkflowRun(item, group.key)) === view.outcome),
                view.sort, view.descending, group.key),
        }))
        .filter(group => group.items.length > 0);
    return { groups, shown: new Set(groups.flatMap(group => group.items)).size };
}

// Workflows are there to be launched: every group starts open.
export function isWorkflowGroupOpen(view: WorkflowsView, group: WorkflowGroup): boolean {
    return isOpen(view.open, view.groupBy, group.key, true);
}

export function withWorkflowGroupsOpen(view: WorkflowsView, groups: WorkflowGroup[], open: boolean): WorkflowsView {
    return { ...view, open: withOpen(view.open, view.groupBy, groups.map(group => group.key), open) };
}
