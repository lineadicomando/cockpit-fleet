// The sections of the page, in groups: a tab for each group, and under it a
// row of tabs for its sections. The URL names the section alone (#/runs),
// so links and bookmarks do not depend on how sections are grouped. A group
// of one section is keyed by it: its tab then names the section's panel.
export const GROUPS: { key: string; sections: string[] }[] = [
    { key: "overview", sections: ["overview"] },
    { key: "nodes", sections: ["hosts", "assets", "checks", "hostkeys"] },
    { key: "automation", sections: ["runs", "templates", "workflows", "schedules"] },
    { key: "resources", sections: ["projects", "inventories", "store", "variables", "credentials"] },
    { key: "administration", sections: ["notifications", "system", "audit"] },
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
