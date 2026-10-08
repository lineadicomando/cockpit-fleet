// Picks the interface language from what the Cockpit session offers, most
// specific source first. Region suffixes are ignored: "it-IT" and "it_IT"
// both select "it".
export function pickLanguage(
    candidates: (string | null | undefined)[],
    supported: readonly string[],
    fallback: string,
): string {
    for (const candidate of candidates) {
        const base = candidate?.trim().toLowerCase().split(/[-_]/)[0];
        if (base && supported.includes(base)) return base;
    }
    return fallback;
}

export function cookieValue(cookies: string, name: string): string | undefined {
    for (const part of cookies.split(";")) {
        const [key, ...value] = part.trim().split("=");
        if (key === name) return decodeURIComponent(value.join("="));
    }
    return undefined;
}
