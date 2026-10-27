// Releases (CLAUDE.md, "Versions and releases").
//
//   node scripts/release.mjs next [X.Y.Z]      (make next-version)
//       The version of the next release, computed from the kinds of change
//       under [Unreleased] in CHANGELOG.md.
//   node scripts/release.mjs prepare [X.Y.Z]   (make release [VERSION=X.Y.Z])
//       On a clean main: run the checks, date the Unreleased changes, set
//       the version in every file of scripts/versions.mjs (the changelogs of
//       the packages included), commit "Release X.Y.Z", tag vX.Y.Z. Nothing
//       is pushed. X.Y.Z, when given, may only be higher than the computed
//       version (1.0.0, say).
//   node scripts/release.mjs verify vX.Y.Z
//       The files and CHANGELOG.md agree with the tag.
//   node scripts/release.mjs notes X.Y.Z
//       The CHANGELOG.md section of a version (release notes).
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { changelogSection, changeKinds, compareVersions, nextVersion, releaseChangelog } from "./changelog.mjs";
import { FILES, readVersions, setVersions } from "./versions.mjs";

const CHANGELOG = "CHANGELOG.md";
const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

function fail(message) {
    console.error(`release: ${message}`);
    process.exit(1);
}

function run(cmd, args, opts = {}) {
    return execFileSync(cmd, args, { encoding: "utf8", ...opts });
}

const git = (...args) => run("git", args).trim();
const read = (file) => readFileSync(file, "utf8");

function released() {
    return git("tag", "--list", "v[0-9]*").split("\n").filter(Boolean).map((t) => t.slice(1))
        .filter((v) => SEMVER.test(v)).sort(compareVersions);
}

// { current, computed, version, problems }: a release goes ahead only with
// no problems.
function plan(requested) {
    const problems = [];
    const tags = released();
    const current = tags.at(-1) ?? "0.0.0";
    let computed = null;
    try {
        const unreleased = changelogSection(read(CHANGELOG), "Unreleased");
        if (unreleased === null) throw new Error("CHANGELOG.md has no [Unreleased] section");
        computed = nextVersion(current, changeKinds(unreleased));
    } catch (e) {
        problems.push(e.message);
    }
    if (requested && !SEMVER.test(requested)) {
        problems.push(`"${requested}" is not a version of the form X.Y.Z`);
        return { current, computed, version: null, problems };
    }
    const version = requested || computed;
    if (version && tags.includes(version)) problems.push(`v${version} is already released`);
    if (version && computed && compareVersions(version, computed) < 0) {
        problems.push(`CHANGELOG.md asks for at least v${computed}, not v${version}`);
    }
    return { current, computed, version, problems };
}

function next(requested) {
    const p = plan(requested);
    console.log(`last release: v${p.current}`);
    console.log(`CHANGELOG.md: ${p.computed ? `v${p.computed}` : "-"}`);
    if (p.problems.length) {
        console.log("not ready:");
        for (const problem of p.problems) console.log(`  - ${problem}`);
        process.exit(1);
    }
    console.log(`ready: make release${requested ? ` VERSION=${requested}` : ""} (v${p.version})`);
}

function mismatches(version) {
    const found = readVersions(read);
    return FILES.filter((file) => found[file] !== version).map((file) => `${file} has ${found[file]}`);
}

function prepare(requested) {
    const p = plan(requested);
    if (p.problems.length) fail(`not ready for a release:\n  - ${p.problems.join("\n  - ")}`);
    const { version } = p;
    const tag = `v${version}`;
    if (git("rev-parse", "--abbrev-ref", "HEAD") !== "main") fail("releases are tagged on main");
    if (git("status", "--porcelain")) fail("the work tree is not clean");

    const now = new Date();
    const author = `${git("config", "user.name")} <${git("config", "user.email")}>`;
    let changelog, files;
    try {
        changelog = releaseChangelog(read(CHANGELOG), version, now.toISOString().slice(0, 10));
        files = setVersions(read, version, author, now);
    } catch (e) {
        fail(e.message);
    }

    console.log("release: type check and tests");
    run("npx", ["tsc", "--noEmit"], { stdio: "inherit" });
    run("make", ["check"], { stdio: "inherit" });

    writeFileSync(CHANGELOG, changelog);
    for (const [file, text] of Object.entries(files)) writeFileSync(file, text);
    const left = mismatches(version);
    if (left.length) fail(`the version was not written everywhere:\n  - ${left.join("\n  - ")}`);
    run("make", ["check-py"], { stdio: "inherit" });

    run("git", ["add", CHANGELOG, ...FILES]);
    run("git", ["commit", "--quiet", "-m", `Release ${version}`]);
    run("git", ["tag", "--annotate", tag, "-m", `cockpit-fleet ${version}`]);
    console.log(`release: committed and tagged ${tag}. Publish with:\n  git push origin main ${tag}`);
}

function verify(tag) {
    if (!tag?.startsWith("v") || !SEMVER.test(tag.slice(1))) fail(`"${tag ?? ""}" is not a release tag vX.Y.Z`);
    const version = tag.slice(1);
    const problems = mismatches(version);
    if (!changelogSection(read(CHANGELOG), version)) problems.push(`CHANGELOG.md has no entries for ${version}`);
    if (problems.length) fail(`${tag}:\n  - ${problems.join("\n  - ")}`);
    console.log(`release: ${tag} agrees with ${CHANGELOG} and ${FILES.join(", ")}`);
}

function notes(version) {
    const body = changelogSection(read(CHANGELOG), version);
    if (!body) fail(`CHANGELOG.md has no entries for ${version}`);
    process.stdout.write(body + "\n");
}

const [command, arg] = process.argv.slice(2);
switch (command) {
    case "next": next(arg); break;
    case "prepare": prepare(arg); break;
    case "verify": verify(arg); break;
    case "notes": notes(arg); break;
    default: fail("usage: release.mjs next [X.Y.Z] | prepare [X.Y.Z] | verify vX.Y.Z | notes X.Y.Z");
}
