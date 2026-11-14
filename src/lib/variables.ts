import type { VariableSet } from "./types.ts";

// One extra variable of a run, and where it comes from: the name of a set,
// or null for the template's own. A secret one has no value here.
export interface EffectiveVariable {
    name: string;
    value: unknown;
    secret: boolean;
    from: string | null;
    // Whether the set it comes from is the inventory's.
    ofInventory: boolean;
    // The sets before that also had it, and lost.
    overrides: string[];
}

// What a run of a template gets as extra variables, as the backend merges
// them: the sets of its inventory, then its own sets, in order, a later one
// winning, then the template's own variables.
export function effectiveVariables(sets: VariableSet[], ids: string[], own: Record<string, unknown>,
    inventoryIds: string[] = []): EffectiveVariable[] {
    const result = new Map<string, EffectiveVariable>();
    const put = (name: string, value: unknown, secret: boolean, from: string | null, ofInventory: boolean) => {
        const before = result.get(name);
        const overrides = before ? [...before.overrides, before.from ?? ""] : [];
        result.set(name, { name, value, secret, from, ofInventory, overrides });
    };
    const takeSet = (id: string, ofInventory: boolean) => {
        const set = sets.find(candidate => candidate.id === id);
        if (!set) return;
        for (const [name, value] of Object.entries(set.variables)) put(name, value, false, set.name, ofInventory);
        for (const secret of set.secrets ?? []) put(secret.name, undefined, true, set.name, ofInventory);
    };
    inventoryIds.forEach(id => takeSet(id, true));
    ids.forEach(id => takeSet(id, false));
    for (const [name, value] of Object.entries(own)) put(name, value, false, null, false);
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
