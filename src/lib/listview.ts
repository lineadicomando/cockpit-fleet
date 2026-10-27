// What the long lists of the interface share: filters kept in the browser,
// items in groups that open and close.

export interface Group<T> {
    // What the items have in common: an id, a category, a state; "" when not grouped.
    key: string;
    // The name to show for it, when the key is an id.
    name: string;
    items: T[];
}

// The items in groups, in the order the groups are first met.
export function groupItems<T>(items: T[], keyOf: (item: T) => string, nameOf: (item: T) => string = () => ""): Group<T>[] {
    const groups = new Map<string, Group<T>>();
    for (const item of items) {
        const key = keyOf(item);
        const group = groups.get(key) ?? { key, name: nameOf(item), items: [] };
        group.items.push(item);
        groups.set(key, group);
    }
    return [...groups.values()];
}

export function byGroupName<T>(groups: Group<T>[]): Group<T>[] {
    return [...groups].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
}

// Groups whose keys are listed in `order` in that order, any other after them.
export function byGroupOrder<T>(groups: Group<T>[], order: readonly string[]): Group<T>[] {
    const rank = (key: string) => order.includes(key) ? order.indexOf(key) : order.length;
    return [...groups].sort((a, b) => rank(a.key) - rank(b.key));
}

export function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
    return allowed.includes(value as T) ? value as T : fallback;
}

export function readText(value: unknown, max = 200): string {
    return typeof value === "string" ? value.slice(0, max) : "";
}

// The stored object, or {} for anything else.
export function readObject(text: string | null): Record<string, unknown> {
    try {
        const parsed: unknown = text ? JSON.parse(text) : {};
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
    } catch {
        // nothing usable
    }
    return {};
}

// At most this many groups remembered, the latest kept.
const OPEN_KEPT = 200;

// Groups opened or closed by hand, by "groupBy:key".
export function readOpen(value: unknown): Record<string, boolean> {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    const entries = Object.entries(value).filter(([key, open]) => key.length <= 300 && typeof open === "boolean");
    return Object.fromEntries(entries.slice(-OPEN_KEPT));
}

// Whether a group is open: as the user left it, or else `fallback`.
export function isOpen(open: Record<string, boolean>, groupBy: string, key: string, fallback: boolean): boolean {
    return open[`${groupBy}:${key}`] ?? fallback;
}

// The groups opened, or closed, by hand.
export function withOpen(open: Record<string, boolean>, groupBy: string, keys: string[], value: boolean): Record<string, boolean> {
    return { ...open, ...Object.fromEntries(keys.map(key => [`${groupBy}:${key}`, value])) };
}
