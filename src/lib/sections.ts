// The sections of the page, in groups: a tab for each group, and under it a
// row of tabs for its sections. The URL names the section alone (#/runs),
// so links and bookmarks do not depend on how sections are grouped. A group
// of one section is keyed by it: its tab then names the section's panel.
export const GROUPS: { key: string; sections: string[] }[] = [
    { key: "overview", sections: ["overview"] },
    { key: "nodes", sections: ["assets", "checks", "hosts", "hostkeys", "network"] },
    { key: "automation", sections: ["templates", "workflows", "schedules", "runs"] },
    { key: "resources", sections: ["projects", "inventories", "store", "documents", "variables", "credentials"] },
    { key: "administration", sections: ["system", "notifications", "audit"] },
    { key: "chat", sections: ["chat"] },
];

export const SECTIONS = GROUPS.flatMap(group => group.sections);

// The groups with at least one section the user may see, each with only
// those sections.
export function visibleGroups(visible: (section: string) => boolean): { key: string; sections: string[] }[] {
    return GROUPS
        .map(group => ({ key: group.key, sections: group.sections.filter(visible) }))
        .filter(group => group.sections.length > 0);
}

export function groupOf(groups: { key: string; sections: string[] }[], section: string): { key: string; sections: string[] } {
    return groups.find(group => group.sections.includes(section)) ?? groups[0];
}

// A run's or a host's page carries the page it was opened from
// (#/runs/ID?from=templates), for a link that goes back there. Its own
// list needs none: the page has a link to it anyway.
export function detailLocation(section: string, id: string, from: string[]): { path: string[]; options: Record<string, string> } {
    const origin = from.length === 0 ? "overview" : from.join("/");
    // Its own list, or the page itself when it is opened twice in a row.
    const none = origin === section || origin === `${section}/${id}`;
    return { path: [section, id], options: none ? {} : { from: origin } };
}

export function runLocation(id: string, from: string[]): { path: string[]; options: Record<string, string> } {
    return detailLocation("runs", id, from);
}

// The path a detail page of section goes back to, or null when there is
// none to offer. It is read from the URL, so only a known section is taken.
export function originPath(from: string | undefined, section = "runs"): string[] | null {
    const path = (from ?? "").split("/").filter(part => part !== "");
    if (path.length === 0 || !SECTIONS.includes(path[0])) return null;
    if (path.length === 1 && path[0] === section) return null;
    return path[0] === "overview" ? [] : path;
}
