import { test } from "node:test";
import assert from "node:assert/strict";
import {
    bySeverity, formatBytes, formatInterval, formatMetric, fromLocalInput, linesOf, ownThresholds,
    thresholdFields, thresholdsValid, toLocalInput, worstState,
} from "../src/lib/checks.ts";

const DEFAULTS = { used_pct: { warning: 85, critical: 95 }, inodes_used_pct: { warning: 85, critical: 95 } };

test("only the thresholds that differ from the catalog's are sent", () => {
    const fields = thresholdFields(DEFAULTS);
    assert.deepEqual(fields.used_pct, { warning: "85", critical: "95" });
    assert.deepEqual(ownThresholds(DEFAULTS, fields), {});
    fields.used_pct = { warning: "70,5", critical: "" };
    assert.deepEqual(ownThresholds(DEFAULTS, fields), { used_pct: { warning: 70.5, critical: null } });
    assert.equal(thresholdsValid(fields), true);
    fields.inodes_used_pct = { warning: "lots", critical: "95" };
    assert.equal(thresholdsValid(fields), false);
});

test("the worst state of a host comes first", () => {
    assert.equal(worstState({ ok: 3, warning: 1, critical: 0, unknown: 1, pending: 0 }), "unknown");
    assert.equal(worstState({ ok: 0, warning: 0, critical: 0, unknown: 0, pending: 2 }), null);
    const sorted = bySeverity([{ state: "ok" as const }, { state: null }, { state: "critical" as const }, { state: "warning" as const }]);
    assert.deepEqual(sorted.map(item => item.state), ["critical", "warning", "ok", null]);
});

test("metrics read as a person would write them", () => {
    assert.equal(formatBytes(512), "512 B");
    assert.equal(formatBytes(16 * 1024 * 1024), "16.0 MiB");
    assert.equal(formatBytes(195892707328), "182 GiB");
    assert.equal(formatMetric({ name: "used_pct", value: 61.3, unit: "%" }), "61.3%");
    assert.equal(formatMetric({ name: "uptime", value: 7260, unit: "s" }), "2 h 01 min");
    assert.equal(formatMetric({ name: "offset", value: 1.8, unit: "s" }), "1.8 s");
    assert.equal(formatMetric({ name: "days_left", value: 3, unit: "d" }), "3 d");
    assert.equal(formatMetric({ name: "not_running", value: 1, unit: "" }), "1");
});

test("intervals, parameters and dates of the forms", () => {
    assert.equal(formatInterval(300), "5 min");
    assert.equal(formatInterval(5400), "1 h 30 min");
    assert.equal(formatInterval(3600), "1 h");
    assert.equal(formatInterval(86400), "1 d");
    assert.deepEqual(linesOf(" ssh \n\n cron\n"), ["ssh", "cron"]);
    const now = Math.floor(Date.now() / 1000 / 60) * 60;
    assert.equal(fromLocalInput(toLocalInput(now)), now);
    assert.equal(fromLocalInput(""), null);
});
