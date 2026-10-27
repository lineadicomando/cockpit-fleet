// The files that carry the version of a release, read and written as text:
// backend/tests/test_version.py checks that they agree, scripts/release.mjs
// sets them.

export const INIT = "backend/cockpit_fleet/__init__.py";
export const PACKAGE = "package.json";
export const LOCK = "package-lock.json";
export const GALAXY = "collection/ansible_collections/lineadicomando/fleet/galaxy.yml";
export const SPEC = "packaging/cockpit-fleet.spec";
export const DEBIAN = "debian/changelog";
export const FILES = [INIT, PACKAGE, LOCK, GALAXY, SPEC, DEBIAN];

const INIT_LINE = /^__version__ = "([^"]*)"$/m;
const GALAXY_LINE = /^version:[ \t]*"?([^"\s]*)"?[ \t]*$/m;
const SPEC_LINE = /^Version:[ \t]*(\S+)[ \t]*$/m;
const DEBIAN_LINE = /^cockpit-fleet \(([^)]+)\)/;

function found(re, text, file) {
    const m = re.exec(text);
    if (!m) throw new Error(`${file} has no version line`);
    return m[1];
}

// The version each file says: { file: version }.
export function readVersions(read) {
    const pkg = JSON.parse(read(PACKAGE));
    const lock = JSON.parse(read(LOCK));
    return {
        [INIT]: found(INIT_LINE, read(INIT), INIT),
        [PACKAGE]: pkg.version,
        [LOCK]: lock.version === lock.packages?.[""]?.version ? lock.version : `${lock.version} and ${lock.packages?.[""]?.version}`,
        [GALAXY]: found(GALAXY_LINE, read(GALAXY), GALAXY),
        [SPEC]: found(SPEC_LINE, read(SPEC), SPEC),
        [DEBIAN]: found(DEBIAN_LINE, read(DEBIAN), DEBIAN),
    };
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const two = (n) => String(n).padStart(2, "0");

// "Tue, 27 Oct 2026 08:00:00 +0100", in the time zone of the machine.
export function debianDate(d) {
    const offset = -d.getTimezoneOffset();
    const sign = offset < 0 ? "-" : "+";
    const zone = `${sign}${two(Math.floor(Math.abs(offset) / 60))}${two(Math.abs(offset) % 60)}`;
    return `${DAYS[d.getDay()]}, ${two(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ` +
        `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())} ${zone}`;
}

// "Tue Oct 27 2026".
export function rpmDate(d) {
    return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${two(d.getDate())} ${d.getFullYear()}`;
}

function json(text, set) {
    const value = JSON.parse(text);
    set(value);
    // As npm writes it.
    return JSON.stringify(value, null, 2) + "\n";
}

// Every file of FILES with the version of a release: { file: new text }.
// author is "Name <email>", date a Date.
export function setVersions(read, version, author, date) {
    const note = `Release ${version}. See CHANGELOG.md.`;
    const spec = read(SPEC)
        .replace(SPEC_LINE, `Version:        ${version}`)
        .replace(/^Release:[ \t]*\S+[ \t]*$/m, "Release:        1%{?dist}")
        .replace(/^%changelog\n/m, `%changelog\n* ${rpmDate(date)} ${author} - ${version}-1\n- ${note}\n\n`);
    const debian = `cockpit-fleet (${version}) unstable; urgency=medium\n\n  * ${note}\n\n` +
        ` -- ${author}  ${debianDate(date)}\n\n` + read(DEBIAN);
    for (const [file, text] of [[SPEC, spec], [DEBIAN, debian]]) {
        if (!text.includes(version)) throw new Error(`${file}: the version could not be written`);
    }
    return {
        [INIT]: read(INIT).replace(INIT_LINE, `__version__ = "${version}"`),
        [PACKAGE]: json(read(PACKAGE), (p) => { p.version = version; }),
        [LOCK]: json(read(LOCK), (p) => { p.version = version; p.packages[""].version = version; }),
        [GALAXY]: read(GALAXY).replace(GALAXY_LINE, `version: ${version}`),
        [SPEC]: spec,
        [DEBIAN]: debian,
    };
}
