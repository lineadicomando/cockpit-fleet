// What a select with a search offers: options in groups, a group with no label
// being the options of a list with no groups.
export interface SearchOption {
    id: string;
    label: string;
    // A line under the label, searched too.
    description?: string;
    // Searched, not shown.
    keywords?: string[];
}

export interface SearchGroup {
    label: string;
    options: SearchOption[];
}

// Lower case, with no accents: "Perché" is found by "perche".
export function fold(text: string): string {
    return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

// The options every word typed is in, in any order: in the label, the
// description, the keywords or the label of the group. At most `limit` of them,
// in the order given; `more` says how many matched beyond.
export function searchGroups(groups: SearchGroup[], typed: string, limit: number): { groups: SearchGroup[]; more: number } {
    const words = fold(typed).split(/\s+/).filter(Boolean);
    const out: SearchGroup[] = [];
    let shown = 0;
    let more = 0;
    for (const group of groups) {
        const options = group.options.filter(option => {
            const text = fold([group.label, option.label, option.description ?? "", ...(option.keywords ?? [])].join(" "));
            return words.every(word => text.includes(word));
        });
        const room = Math.max(0, limit - shown);
        more += Math.max(0, options.length - room);
        if (room > 0 && options.length > 0) {
            out.push({ label: group.label, options: options.slice(0, room) });
            shown += Math.min(room, options.length);
        }
    }
    return { groups: out, more };
}

// The options of the groups in the order they are shown, to move through them
// with the keyboard.
export function flatOptions(groups: SearchGroup[]): SearchOption[] {
    return groups.flatMap(group => group.options);
}
