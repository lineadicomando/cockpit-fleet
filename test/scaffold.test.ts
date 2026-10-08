import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

function readJson(path: string): Record<string, unknown> {
    return JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
}

// Italian has a plural form English lacks ("_many"), so plural keys are
// compared without their suffix.
const base = (key: string) => key.replace(/_(one|many|other)$/, "");

test("every Italian string translates an existing English key", () => {
    const en = new Set(Object.keys(readJson("../src/locales/en/translation.json")).map(base));
    const it = readJson("../src/locales/it/translation.json");
    const orphans = Object.keys(it).filter(key => !en.has(base(key)));
    assert.deepEqual(orphans, []);
});

test("every English string has an Italian translation", () => {
    const en = readJson("../src/locales/en/translation.json");
    const it = new Set(Object.keys(readJson("../src/locales/it/translation.json")).map(base));
    const missing = Object.keys(en).filter(key => !it.has(base(key)));
    assert.deepEqual(missing, []);
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
