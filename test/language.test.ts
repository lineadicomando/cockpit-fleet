import { test } from "node:test";
import assert from "node:assert/strict";
import { cookieValue, pickLanguage } from "../src/lib/language.ts";

const supported = ["en", "it"];

test("pickLanguage takes the first supported candidate", () => {
    assert.equal(pickLanguage([undefined, "it", "en"], supported, "en"), "it");
    assert.equal(pickLanguage(["de", "it"], supported, "en"), "it");
});

test("pickLanguage ignores region and case", () => {
    assert.equal(pickLanguage(["it-IT"], supported, "en"), "it");
    assert.equal(pickLanguage(["IT_it"], supported, "en"), "it");
});

test("pickLanguage falls back when nothing matches", () => {
    assert.equal(pickLanguage([null, "", "de-DE"], supported, "en"), "en");
});

test("cookieValue finds one cookie among several", () => {
    assert.equal(cookieValue("a=1; CockpitLang=it; b=2", "CockpitLang"), "it");
    assert.equal(cookieValue("a=1; b=x%3Dy", "b"), "x=y");
    assert.equal(cookieValue("a=1", "CockpitLang"), undefined);
    assert.equal(cookieValue("", "CockpitLang"), undefined);
});
