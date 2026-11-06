// The list of job templates: filters, groups and order, from what
// template.list and schedule.list return.

import { byGroupName, byGroupOrder, groupItems, isOpen, oneOf, readObject, readOpen, readText, withOpen, type Group } from "./listview.ts";
import type { RunStatus, Template } from "./types.ts";

// How the last run of a template went, in the order its groups are shown.
export type Outcome = "failed" | "active" | "canceled" | "successful" | "never";
export const OUTCOMES: Outcome[] = ["failed", "active", "canceled", "successful", "never"];

const OUTCOME_OF: Record<RunStatus, Outcome> = {
    failed: "failed", error: "failed",
    queued: "active", running: "active", pending_approval: "active",
    canceled: "canceled", rejected: "canceled", expired: "canceled",
    successful: "successful",
};

export function outcomeOf(template: Template): Outcome {
    return template.last_run ? OUTCOME_OF[template.last_run.status] ?? "failed" : "never";
}

export type Trait = "" | "approval" | "survey" | "scheduled" | "unscheduled";
export type TemplateGroupBy = "project" | "inventory" | "outcome" | "none";
export type TemplateSort = "name" | "project" | "inventory" | "last_run";

export interface TemplatesView {
    search: string;
    project: string;
    inventory: string;
    trait: Trait;
    outcome: "" | Outcome;
    groupBy: TemplateGroupBy;
    sort: TemplateSort;
    descending: boolean;
    // Groups opened or closed by hand, by "groupBy:key"; the others are open.
    open: Record<string, boolean>;
}

export const DEFAULT_TEMPLATES_VIEW: TemplatesView = {
    search: "", project: "", inventory: "", trait: "", outcome: "", groupBy: "project", sort: "name", descending: false, open: {},
};

export function readTemplatesView(text: string | null): TemplatesView {
    const raw = readObject(text);
    return {
        search: readText(raw.search),
        project: readText(raw.project, 64),
        inventory: readText(raw.inventory, 64),
        trait: oneOf(raw.trait, ["", "approval", "survey", "scheduled", "unscheduled"], ""),
        outcome: oneOf(raw.outcome, ["", ...OUTCOMES], ""),
        groupBy: oneOf(raw.groupBy, ["project", "inventory", "outcome", "none"], DEFAULT_TEMPLATES_VIEW.groupBy),
        sort: oneOf(raw.sort, ["name", "project", "inventory", "last_run"], "name"),
        descending: raw.descending === true,
        open: readOpen(raw.open),
    };
}

export function withoutTemplateFilters(view: TemplatesView): TemplatesView {
    return { ...view, search: "", project: "", inventory: "", trait: "", outcome: "" };
}

// The names the page knows the ids by, and how many schedules launch each template.
export interface TemplateNames {
    project: (id: string) => string;
    inventory: (id: string) => string;
    schedules: (id: string) => number;
}

export function filterTemplates(templates: Template[], view: TemplatesView, names: TemplateNames): Template[] {
    const needle = view.search.trim().toLowerCase();
    return templates.filter(template => {
        if (view.project && template.project_id !== view.project) return false;
        if (view.inventory && template.inventory_id !== view.inventory) return false;
        if (view.outcome && outcomeOf(template) !== view.outcome) return false;
        if (view.trait === "approval" && template.approval === "none") return false;
        if (view.trait === "survey" && template.survey.length === 0) return false;
        if (view.trait === "scheduled" && names.schedules(template.id) === 0) return false;
        if (view.trait === "unscheduled" && names.schedules(template.id) > 0) return false;
        if (needle) {
            const text = [template.name, template.playbook, names.project(template.project_id), names.inventory(template.inventory_id),
                ...template.tags, ...template.skip_tags].join("\n").toLowerCase();
            if (!text.includes(needle)) return false;
        }
        return true;
    });
}

const compareText = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

// By last run: the oldest first, those never launched before any.
export function sortTemplates(templates: Template[], column: TemplateSort, descending: boolean, names: TemplateNames): Template[] {
    const byName = (a: Template, b: Template) => compareText(a.name, b.name);
    const compare: Record<TemplateSort, (a: Template, b: Template) => number> = {
        name: byName,
        project: (a, b) => compareText(names.project(a.project_id), names.project(b.project_id)) || byName(a, b),
        inventory: (a, b) => compareText(names.inventory(a.inventory_id), names.inventory(b.inventory_id)) || byName(a, b),
        last_run: (a, b) => (a.last_run?.created_at ?? 0) - (b.last_run?.created_at ?? 0) || byName(a, b),
    };
    const sorted = [...templates].sort(compare[column]);
    return descending ? sorted.reverse() : sorted;
}

export type TemplateGroup = Group<Template>;

export function groupTemplates(templates: Template[], by: TemplateGroupBy, names: TemplateNames): TemplateGroup[] {
    if (by === "none") return [{ key: "", name: "", items: templates }];
    if (by === "project") return byGroupName(groupItems(templates, item => item.project_id, item => names.project(item.project_id)));
    if (by === "inventory") return byGroupName(groupItems(templates, item => item.inventory_id, item => names.inventory(item.inventory_id)));
    return byGroupOrder(groupItems(templates, outcomeOf), OUTCOMES);
}

// Templates are there to be launched: every group starts open.
export function isTemplateGroupOpen(view: TemplatesView, group: TemplateGroup): boolean {
    return isOpen(view.open, view.groupBy, group.key, true);
}

export function withTemplateGroupsOpen(view: TemplatesView, groups: TemplateGroup[], open: boolean): TemplatesView {
    return { ...view, open: withOpen(view.open, view.groupBy, groups.map(group => group.key), open) };
}
