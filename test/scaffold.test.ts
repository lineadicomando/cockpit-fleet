import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

function readJson(path: string): Record<string, unknown> {
    return JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
}

test("every Italian string translates an existing English key", () => {
    const en = readJson("../src/locales/en/translation.json");
    const it = readJson("../src/locales/it/translation.json");
    const orphans = Object.keys(it).filter(key => !(key in en));
    assert.deepEqual(orphans, []);
});

test("no Italian translation is left empty", () => {
    const it = readJson("../src/locales/it/translation.json");
    const empty = Object.entries(it).filter(([, value]) => value === "").map(([key]) => key);
    assert.deepEqual(empty, []);
});

test("manifest declares the menu entry and the minimum Cockpit version", () => {
    const manifest = readJson("../src/manifest.json") as {
        requires: { cockpit: string };
        menu: { index: { label: string } };
    };
    assert.equal(manifest.menu.index.label, "Fleet");
    assert.ok(Number(manifest.requires.cockpit) >= 337);
});
