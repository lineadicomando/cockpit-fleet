import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { GROUPS, SECTIONS, groupOf, visibleGroups } from "../src/lib/sections.ts";

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
