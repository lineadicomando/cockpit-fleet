import type { VariableSet } from "./types.ts";

// One extra variable of a run, and where it comes from: the name of a set,
// or null for the template's own.
export interface EffectiveVariable {
    name: string;
    value: unknown;
    from: string | null;
    // The sets before that also had it, and lost.
    overrides: string[];
}

// What a run of a template gets as extra variables, as the backend merges
// them: the sets in order, a later one winning, then the template's own.
export function effectiveVariables(sets: VariableSet[], ids: string[], own: Record<string, unknown>): EffectiveVariable[] {
    const result = new Map<string, EffectiveVariable>();
    const take = (variables: Record<string, unknown>, from: string | null) => {
        for (const [name, value] of Object.entries(variables)) {
            const before = result.get(name);
            const overrides = before ? [...before.overrides, before.from ?? ""] : [];
            result.set(name, { name, value, from, overrides });
        }
    };
    for (const id of ids) {
        const set = sets.find(candidate => candidate.id === id);
        if (set) take(set.variables, set.name);
    }
    take(own, null);
    return [...result.values()].sort((a, b) => a.name.localeCompare(b.name));
}

// The text of an object of variables typed in an editor: {} when empty,
// null when it is not a JSON object.
export function parseVariables(text: string): Record<string, unknown> | null {
    if (!text.trim()) return {};
    try {
        const value: unknown = JSON.parse(text);
        return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
    } catch {
        return null;
    }
}

// Moves the item at index by step (-1 up, +1 down), within the list.
export function moved<T>(items: T[], index: number, step: number): T[] {
    const target = index + step;
    if (target < 0 || target >= items.length) return items;
    const next = [...items];
    [next[index], next[target]] = [next[target], next[index]];
    return next;
}
