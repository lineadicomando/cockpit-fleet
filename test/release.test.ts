import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { changelogSection, changeKinds, compareVersions, nextVersion, releaseChangelog } from "../scripts/changelog.mjs";
import { DEBIAN, GALAXY, INIT, LOCK, PACKAGE, SPEC, debianDate, readVersions, rpmDate, setVersions } from "../scripts/versions.mjs";

const CHANGELOG = `# Changelog

Intro.

## [Unreleased]

### Fixed

- A fix

## [0.1.0] - 2026-10-25

### Packaging

- First release

[Unreleased]: https://example.invalid/r/compare/v0.1.0...main
[0.1.0]: https://example.invalid/r/releases/tag/v0.1.0
`;

describe("CHANGELOG.md", () => {
    it("reads a section without the links at the end", () => {
        assert.equal(changelogSection(CHANGELOG, "Unreleased"), "### Fixed\n\n- A fix");
        assert.equal(changelogSection(CHANGELOG, "0.1.0"), "### Packaging\n\n- First release");
        assert.equal(changelogSection(CHANGELOG, "9.9.9"), null);
    });

    it("moves the unreleased changes under the version and points the links at it", () => {
        const out = releaseChangelog(CHANGELOG, "0.1.1", "2026-10-27");
        assert.equal(changelogSection(out, "Unreleased"), "");
        assert.equal(changelogSection(out, "0.1.1"), "### Fixed\n\n- A fix");
        assert.match(out, /^## \[0\.1\.1\] - 2026-10-27$/m);
        assert.match(out, /^\[Unreleased\]: https:\/\/example\.invalid\/r\/compare\/v0\.1\.1\.\.\.main\n\[0\.1\.1\]: https:\/\/example\.invalid\/r\/releases\/tag\/v0\.1\.1\n\[0\.1\.0\]: /m);
    });

    it("refuses a release with nothing in it, twice the same, or no link to move", () => {
        const empty = releaseChangelog(CHANGELOG, "0.1.1", "2026-10-27");
        assert.throws(() => releaseChangelog(empty, "0.1.2", "2026-10-28"), /is empty/);
        assert.throws(() => releaseChangelog(CHANGELOG, "0.1.0", "2026-10-27"), /already has/);
        assert.throws(() => releaseChangelog(CHANGELOG.replace(/^\[Unreleased\]:.*\n/m, ""), "0.1.1", "x"), /no link/);
    });

    it("computes the next version from the kinds of change", () => {
        assert.equal(nextVersion("0.1.0", changeKinds("### Fixed\n\n- a")), "0.1.1");
        assert.equal(nextVersion("0.1.3", changeKinds("### Fixed\n\n- a\n\n### Changed\n\n- b")), "0.2.0");
        assert.equal(nextVersion("0.1.3", changeKinds("### Breaking\n\n- a")), "0.2.0");
        assert.equal(nextVersion("1.2.3", changeKinds("### Removed\n\n- a")), "2.0.0");
        assert.equal(nextVersion("1.2.3", changeKinds("### Security\n\n- a")), "1.2.4");
        // A heading with no entry under it counts for nothing.
        assert.throws(() => nextVersion("0.1.0", changeKinds("### Added\n")), /empty/);
        assert.throws(() => changeKinds("### Improved\n\n- a"), /unknown kind/);
        assert.throws(() => changeKinds("- a"), /no "### Kind"/);
    });

    it("compares versions by number", () => {
        assert.ok(compareVersions("0.10.0", "0.9.0") > 0);
        assert.equal(compareVersions("0.1.0", "0.1.0"), 0);
    });
});

const TREE: Record<string, string> = {
    [INIT]: '"""Backend."""\n\n__version__ = "0.1.0"\n',
    [PACKAGE]: JSON.stringify({ name: "cockpit-fleet", version: "0.1.0", private: true }, null, 2) + "\n",
    [LOCK]: JSON.stringify({ name: "cockpit-fleet", version: "0.1.0", packages: { "": { version: "0.1.0" } } }, null, 2) + "\n",
    [GALAXY]: "namespace: lineadicomando\nversion: 0.1.0\nreadme: README.md\n",
    [SPEC]: "Name: cockpit-fleet\nVersion:        0.1.0\nRelease:        3%{?dist}\n\n%changelog\n* Sun Oct 25 2026 A <a@x> - 0.1.0-1\n- First.\n",
    [DEBIAN]: "cockpit-fleet (0.1.0) unstable; urgency=medium\n\n  * First.\n\n -- A <a@x>  Sun, 25 Oct 2026 19:00:00 +0100\n",
};
const read = (file: string) => TREE[file];

describe("the files with the version", () => {
    it("are read", () => {
        assert.deepEqual(new Set(Object.values(readVersions(read))), new Set(["0.1.0"]));
    });

    it("are all written, with an entry in the changelogs of the packages", () => {
        const date = new Date(2026, 9, 27, 8, 5, 9);
        const out: Record<string, string> = setVersions(read, "0.2.0", "B <b@x>", date);
        assert.deepEqual(new Set(Object.values(readVersions((file: string) => out[file]))), new Set(["0.2.0"]));
        assert.match(out[SPEC], /^Release: +1%\{\?dist\}$/m);
        assert.match(out[SPEC], /^%changelog\n\* Tue Oct 27 2026 B <b@x> - 0\.2\.0-1\n- Release 0\.2\.0\. See CHANGELOG\.md\.\n\n\* Sun Oct 25/m);
        assert.ok(out[DEBIAN].startsWith("cockpit-fleet (0.2.0) unstable; urgency=medium\n\n  * Release 0.2.0. See CHANGELOG.md.\n\n -- B <b@x>  Tue, 27 Oct 2026 08:05:09 "));
        assert.ok(out[DEBIAN].endsWith(TREE[DEBIAN]));
        assert.equal(JSON.parse(out[LOCK]).packages[""].version, "0.2.0");
    });

    it("formats the dates of the two changelogs", () => {
        const date = new Date(2026, 0, 4, 21, 0, 0);
        assert.equal(rpmDate(date), "Sun Jan 04 2026");
        assert.match(debianDate(date), /^Sun, 04 Jan 2026 21:00:00 [+-]\d{4}$/);
    });
});

describe("scripts/version.sh", () => {
    const version = (form: string, describe: string) =>
        execFileSync("sh", ["scripts/version.sh", form], { encoding: "utf8", env: { ...process.env, FLEET_DESCRIBE: describe } }).trim();

    it("is the release at its tag", () => {
        for (const form of ["plain", "deb", "rpm"]) assert.equal(version(form, "v0.2.0"), "0.2.0");
    });

    it("says how far from the last release a build is, in a form each package takes", () => {
        assert.equal(version("plain", "v0.1.0-3-gabc1234-dirty"), "0.1.0-3-gabc1234-dirty");
        assert.equal(version("deb", "v0.1.0-3-gabc1234-dirty"), "0.1.0+3.gabc1234.dirty");
        assert.equal(version("rpm", "v0.1.0-3-gabc1234"), "0.1.0^3.gabc1234");
        assert.equal(version("deb", "v0.1.0-dirty"), "0.1.0+dirty");
        assert.equal(version("rpm", "v0.1.0-gabc1234"), "0.1.0^gabc1234");
    });

    it("refuses what does not begin with a version", () => {
        assert.throws(() => execFileSync("sh", ["scripts/version.sh", "deb"],
            { stdio: "ignore", env: { ...process.env, FLEET_DESCRIBE: "abc1234" } }));
    });
});
