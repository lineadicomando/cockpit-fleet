import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GROUPS, SECTIONS, detailLocation, groupOf, originPath, runLocation, visibleGroups } from "../src/lib/sections.ts";

test("every section is in exactly one group", () => {
    assert.equal(new Set(SECTIONS).size, SECTIONS.length);
    assert.equal(SECTIONS[0], "overview");
});

test("a group of one section is keyed by it", () => {
    for (const group of GROUPS.filter(item => item.sections.length === 1)) {
        assert.equal(group.key, group.sections[0]);
    }
});

test("every section is drawn by the page", () => {
    const app = readFileSync(new URL("../src/app.tsx", import.meta.url), "utf8");
    for (const section of SECTIONS) {
        assert.ok(app.includes(`section === "${section}"`), section);
    }
});

test("sections a user may not see leave their group, and an empty group goes", () => {
    const groups = visibleGroups(section => section !== "audit" && section !== "chat");
    assert.deepEqual(groups.find(group => group.key === "administration")?.sections, ["notifications", "system"]);
    assert.equal(groups.find(group => group.key === "chat"), undefined);
});

test("a section is found in its group, an unknown one falls back to the first", () => {
    const groups = visibleGroups(() => true);
    assert.equal(groupOf(groups, "workflows").key, "automation");
    assert.equal(groupOf(groups, "hostkeys").key, "nodes");
    assert.equal(groupOf(groups, "nothing").key, "overview");
});

test("the manifest's keywords lead to sections that exist", () => {
    const manifest = JSON.parse(readFileSync(new URL("../src/manifest.json", import.meta.url), "utf8")) as {
        menu: { index: { keywords: { matches: string[]; goto?: string }[] } };
    };
    for (const keyword of manifest.menu.index.keywords) {
        for (const match of keyword.matches) assert.equal(match, match.toLowerCase());
        // Without a leading slash a goto is the hash of the page: #runs.
        if (keyword.goto !== undefined) assert.ok(SECTIONS.includes(keyword.goto), keyword.goto);
    }
});

test("a run's page knows where it was opened from", () => {
    assert.deepEqual(runLocation("r1", ["templates"]), { path: ["runs", "r1"], options: { from: "templates" } });
    assert.deepEqual(runLocation("r1", ["hosts", "h1"]), { path: ["runs", "r1"], options: { from: "hosts/h1" } });
    assert.deepEqual(runLocation("r1", []), { path: ["runs", "r1"], options: { from: "overview" } });
    assert.deepEqual(runLocation("r1", ["runs"]), { path: ["runs", "r1"], options: {} });
});

test("a host's page knows where it was opened from", () => {
    assert.deepEqual(detailLocation("hosts", "h1", ["assets"]), { path: ["hosts", "h1"], options: { from: "assets" } });
    assert.deepEqual(detailLocation("hosts", "h1", ["hosts"]), { path: ["hosts", "h1"], options: {} });
    assert.deepEqual(detailLocation("hosts", "h1", ["runs", "r1"]), { path: ["hosts", "h1"], options: { from: "runs/r1" } });
    assert.deepEqual(originPath("assets", "hosts"), ["assets"]);
    assert.equal(originPath("hosts", "hosts"), null);
    assert.deepEqual(originPath("runs", "hosts"), ["runs"]);
});

test("only a known section is gone back to", () => {
    assert.deepEqual(originPath("templates"), ["templates"]);
    assert.deepEqual(originPath("projects/p1/drafts/d1"), ["projects", "p1", "drafts", "d1"]);
    assert.deepEqual(originPath("runs/r0"), ["runs", "r0"]);
    assert.deepEqual(originPath("overview"), []);
    assert.equal(originPath("runs"), null);
    assert.equal(originPath(undefined), null);
    assert.equal(originPath(""), null);
    assert.equal(originPath("elsewhere"), null);
});
