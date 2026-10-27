// CHANGELOG.md follows https://keepachangelog.com: an "## [Unreleased]"
// section collects the changes until a release turns it into
// "## [X.Y.Z] - YYYY-MM-DD". After cockpit-samba-dc's scripts/changelog.mjs.

const HEADING = /^## \[([^\]]+)\]/;

function sections(text) {
    const lines = text.split("\n");
    const found = [];
    lines.forEach((line, i) => {
        const m = HEADING.exec(line);
        if (m) found.push({ name: m[1], start: i });
    });
    return { lines, found };
}

// Body of a section, without its heading and surrounding blank lines;
// null when the section does not exist.
export function changelogSection(text, name) {
    const { lines, found } = sections(text);
    const idx = found.findIndex((s) => s.name === name);
    if (idx < 0) return null;
    const end = idx + 1 < found.length ? found[idx + 1].start : lines.length;
    let body = lines.slice(found[idx].start + 1, end);
    // Link reference definitions at the end of the file are not part of it.
    if (idx + 1 === found.length) {
        const firstRef = body.findIndex((l) => /^\[[^\]]+\]: /.test(l));
        if (firstRef >= 0) body = body.slice(0, firstRef);
    }
    return body.join("\n").trim();
}

// Moves the Unreleased changes under a new version heading, leaves an empty
// Unreleased section above it, and points the links at the end of the file
// at the new tag: "[Unreleased]: BASE/compare/vX...main" becomes a compare
// from the new version, followed by "[X.Y.Z]: BASE/releases/tag/vX.Y.Z".
export function releaseChangelog(text, version, date) {
    if (changelogSection(text, version) !== null) {
        throw new Error(`CHANGELOG.md already has a section for ${version}`);
    }
    const unreleased = changelogSection(text, "Unreleased");
    if (unreleased === null) throw new Error("CHANGELOG.md has no [Unreleased] section");
    if (unreleased === "") throw new Error("The [Unreleased] section of CHANGELOG.md is empty");
    const link = /^\[Unreleased\]: (\S+)\/compare\/v[^.\s]+\.[^.\s]+\.[^.\s]+\.\.\.(\S+)$/m.exec(text);
    if (!link) throw new Error("CHANGELOG.md has no link \"[Unreleased]: …/compare/vX.Y.Z...main\" at its end");
    const [, base, branch] = link;
    return text
        .replace(/^## \[Unreleased\][^\n]*\n/m, `## [Unreleased]\n\n## [${version}] - ${date}\n`)
        .replace(link[0], `[Unreleased]: ${base}/compare/v${version}...${branch}\n` +
            `[${version}]: ${base}/releases/tag/v${version}`);
}

// Kinds of change (the "### Kind" headings of a section) and the part of the
// version each one raises. "Breaking" marks a change that asks the
// administrator to act on upgrade (CLAUDE.md, "Versions and releases").
const CHANGE_KINDS = {
    Breaking: "breaking",
    Added: "minor",
    Changed: "minor",
    Deprecated: "minor",
    Removed: "breaking",
    Fixed: "patch",
    Security: "patch",
};

// The "### Kind" headings of a section body that have at least one entry.
export function changeKinds(body) {
    const kinds = new Set();
    let current = null;
    for (const line of body.split("\n")) {
        const heading = /^### (.+?)\s*$/.exec(line);
        if (heading) {
            current = heading[1];
            if (!(current in CHANGE_KINDS)) {
                throw new Error(`CHANGELOG.md: unknown kind of change "### ${current}" (expected one of ${Object.keys(CHANGE_KINDS).join(", ")})`);
            }
        } else if (/^\s*[-*] \S/.test(line)) {
            if (!current) throw new Error("CHANGELOG.md: an entry under [Unreleased] has no \"### Kind\" heading");
            kinds.add(current);
        }
    }
    return kinds;
}

// The version that follows `current` given the kinds of change: while the
// major number is 0, breaking changes and features raise the minor number and
// fixes the patch number; from 1.0.0 breaking changes raise the major number.
export function nextVersion(current, kinds) {
    const m = /^(\d+)\.(\d+)\.(\d+)/.exec(current);
    if (!m) throw new Error(`"${current}" is not a version`);
    const [major, minor, patch] = m.slice(1).map(Number);
    const parts = new Set([...kinds].map((k) => CHANGE_KINDS[k]));
    if (parts.size === 0) throw new Error("The [Unreleased] section of CHANGELOG.md is empty");
    if (parts.has("breaking") && major > 0) return `${major + 1}.0.0`;
    if (parts.has("breaking") || parts.has("minor")) return `${major}.${minor + 1}.0`;
    return `${major}.${minor}.${patch + 1}`;
}

export function compareVersions(a, b) {
    const [x, y] = [a, b].map((v) => v.split(/[-+]/)[0].split(".").map(Number));
    for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i];
    return 0;
}
