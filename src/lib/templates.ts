// The list of job templates: filters, groups and order, from what
// template.list and schedule.list return.

import { byGroupName, byGroupOrder, groupItems, isOpen, oneOf, readObject, readOpen, readText, withOpen, type Group } from "./listview.ts";
import type { Overrides, RunStatus, Template, TemplateRun } from "./types.ts";

// How the last run of a template went, in the order its groups are shown.
export type Outcome = "failed" | "active" | "canceled" | "successful" | "never";
export const OUTCOMES: Outcome[] = ["failed", "active", "canceled", "successful", "never"];

const OUTCOME_OF: Record<RunStatus, Outcome> = {
    failed: "failed", error: "failed",
    queued: "active", running: "active", pending_approval: "active",
    canceled: "canceled", rejected: "canceled", expired: "canceled",
    successful: "successful",
};

// The last run of a template on the inventory `where`, or on any when none is given.
export function lastRunOf(template: Template, where?: string): TemplateRun | null {
    return where === undefined ? template.last_run : template.last_runs?.[where] ?? null;
}

export function outcomeOf(template: Template, where?: string): Outcome {
    const run = lastRunOf(template, where);
    return run ? OUTCOME_OF[run.status] ?? "failed" : "never";
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
    search: "", project: "", inventory: "", trait: "", outcome: "", groupBy: "inventory", sort: "name", descending: false, open: {},
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

// The names the page knows the ids by, how many schedules launch each
// template, and the inventories runs may be on (those kept as starters left out).
export interface TemplateNames {
    project: (id: string) => string;
    inventory: (id: string) => string;
    schedules: (id: string) => number;
    inventories: string[];
}

// The inventories a template may run on, its default first, if it has one.
export function allowedInventories(template: Template, inventories: string[]): string[] {
    const others = template.any_inventory ? inventories : template.inventory_ids;
    const first = template.inventory_id;
    return first === null ? [...others] : [first, ...others.filter(id => id !== first)];
}

export function filterTemplates(templates: Template[], view: TemplatesView, names: TemplateNames): Template[] {
    const needle = view.search.trim().toLowerCase();
    return templates.filter(template => {
        if (view.project && template.project_id !== view.project) return false;
        if (view.inventory && !allowedInventories(template, names.inventories).includes(view.inventory)) return false;
        if (view.outcome && outcomeOf(template) !== view.outcome) return false;
        if (view.trait === "approval" && template.approval === "none") return false;
        if (view.trait === "survey" && template.survey.length === 0) return false;
        if (view.trait === "scheduled" && names.schedules(template.id) === 0) return false;
        if (view.trait === "unscheduled" && names.schedules(template.id) > 0) return false;
        if (needle) {
            const text = [template.name, template.playbook, names.project(template.project_id),
                ...allowedInventories(template, names.inventories).map(names.inventory),
                ...template.tags, ...template.skip_tags].join("\n").toLowerCase();
            if (!text.includes(needle)) return false;
        }
        return true;
    });
}

const compareText = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });

// Those with no default inventory sort before the others.
const defaultName = (template: Template, names: TemplateNames) =>
    template.inventory_id === null ? "" : names.inventory(template.inventory_id);

// By last run (on the inventory `where`, if given): the oldest first, those never launched before any.
export function sortTemplates(templates: Template[], column: TemplateSort, descending: boolean, names: TemplateNames, where?: string): Template[] {
    const byName = (a: Template, b: Template) => compareText(a.name, b.name);
    const compare: Record<TemplateSort, (a: Template, b: Template) => number> = {
        name: byName,
        project: (a, b) => compareText(names.project(a.project_id), names.project(b.project_id)) || byName(a, b),
        inventory: (a, b) => compareText(defaultName(a, names), defaultName(b, names)) || byName(a, b),
        last_run: (a, b) => (lastRunOf(a, where)?.created_at ?? 0) - (lastRunOf(b, where)?.created_at ?? 0) || byName(a, b),
    };
    const sorted = [...templates].sort(compare[column]);
    return descending ? sorted.reverse() : sorted;
}

export type TemplateGroup = Group<Template>;

export function groupTemplates(templates: Template[], by: TemplateGroupBy, names: TemplateNames): TemplateGroup[] {
    if (by === "none") return [{ key: "", name: "", items: templates }];
    if (by === "project") return byGroupName(groupItems(templates, item => item.project_id, item => names.project(item.project_id)));
    if (by === "inventory") {
        // Under each inventory it may run on: what can be launched there is all in one place.
        const groups = new Map<string, TemplateGroup>();
        for (const template of templates) {
            for (const id of allowedInventories(template, names.inventories)) {
                const group = groups.get(id) ?? { key: id, name: names.inventory(id), items: [] };
                group.items.push(template);
                groups.set(id, group);
            }
        }
        return byGroupName([...groups.values()]);
    }
    return byGroupOrder(groupItems(templates, outcomeOf), OUTCOMES);
}

// The groups the page shows and how many templates are in them. Under an
// inventory a row speaks of that inventory: its last run there decides the
// filter by outcome and the order by last run.
export function listTemplates(templates: Template[], view: TemplatesView, names: TemplateNames): { groups: TemplateGroup[]; shown: number } {
    if (view.groupBy !== "inventory") {
        const shown = filterTemplates(templates, view, names);
        return { groups: groupTemplates(sortTemplates(shown, view.sort, view.descending, names), view.groupBy, names), shown: shown.length };
    }
    const kept = filterTemplates(templates, { ...view, outcome: "" }, names);
    const groups = groupTemplates(kept, "inventory", names)
        .map(group => ({
            ...group,
            items: sortTemplates(group.items.filter(item => !view.outcome || outcomeOf(item, group.key) === view.outcome),
                view.sort, view.descending, names, group.key),
        }))
        .filter(group => group.items.length > 0);
    return { groups, shown: new Set(groups.flatMap(group => group.items)).size };
}

// Templates are there to be launched: every group starts open.
export function isTemplateGroupOpen(view: TemplatesView, group: TemplateGroup): boolean {
    return isOpen(view.open, view.groupBy, group.key, true);
}

export function withTemplateGroupsOpen(view: TemplatesView, groups: TemplateGroup[], open: boolean): TemplatesView {
    return { ...view, open: withOpen(view.open, view.groupBy, groups.map(group => group.key), open) };
}

// The values of a template a launch may replace, as the fields of a form show
// them: those of the template, or what replaced them.
export interface OverrideFields {
    tags: string;
    skipTags: string;
    forks: string;
    verbosity: string;
}

const tagWords = (text: string) => text.split(/[\s,]+/).filter(Boolean);
const sameWords = (a: string[], b: string[]) => a.length === b.length && a.every((word, index) => word === b[index]);

export function overrideFields(template: Template | undefined, overrides: Overrides = {}): OverrideFields {
    const forks = overrides.forks ?? template?.forks;
    return {
        tags: (overrides.tags ?? template?.tags ?? []).join(", "),
        skipTags: (overrides.skip_tags ?? template?.skip_tags ?? []).join(", "),
        forks: forks ? String(forks) : "",
        verbosity: String(overrides.verbosity ?? template?.verbosity ?? 0),
    };
}

// What the fields replace of the template: what differs from it. Forks left
// empty keep the template's.
export function overridesOf(template: Template | undefined, fields: OverrideFields): Overrides {
    const out: Overrides = {};
    const tags = tagWords(fields.tags);
    const skipTags = tagWords(fields.skipTags);
    if (!sameWords(tags, template?.tags ?? [])) out.tags = tags;
    if (!sameWords(skipTags, template?.skip_tags ?? [])) out.skip_tags = skipTags;
    const forks = Number(fields.forks);
    if (fields.forks.trim() && forks !== (template?.forks ?? null)) out.forks = forks;
    const verbosity = Number(fields.verbosity);
    if (verbosity !== (template?.verbosity ?? 0)) out.verbosity = verbosity;
    return out;
}
