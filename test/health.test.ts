import { test } from "node:test";
import assert from "node:assert/strict";
import { keeping, keepingTone, share } from "../src/lib/health.ts";
import type { HealthGroup } from "../src/lib/types.ts";

const MB = 1024 * 1024;

function group(extra: Partial<HealthGroup> = {}): HealthGroup {
    return {
        inventory: "lab", inventory_id: "i1", group: "", runs: "checks", name: "", interval: 300, checks: 2,
        cycles: 10, unsuccessful: 0, duration_median: 20, duration_max: 40, memory_peak: 100 * MB, skipped: 0,
        last: null, active: 0, behind: null, unprepared: null, sizing: { forks: 20, shard_size: 50, basis: "measured" }, ...extra,
    };
}

test("keeping says the worst of a group", () => {
    assert.equal(keeping(group(), 200 * MB), "ok");
    assert.equal(keeping(group({ cycles: 0 }), 200 * MB), "idle");
    assert.equal(keeping(group({ cycles: 0, active: 1 }), 200 * MB), "ok");
    // A first cycle still under way that already made its checks skip.
    assert.equal(keeping(group({ cycles: 0, active: 1, skipped: 1, duration_max: null }), 200 * MB), "late");
    assert.equal(keeping(group({ unsuccessful: 1 }), 200 * MB), "failing");
    assert.equal(keeping(group({ memory_peak: 300 * MB, unsuccessful: 1 }), 200 * MB), "memory");
    assert.equal(keeping(group({ memory_peak: 300 * MB }), 0), "ok");
    assert.equal(keeping(group({ duration_median: 151, memory_peak: 300 * MB }), 200 * MB), "slow");
    assert.equal(keeping(group({ duration_max: 301 }), 200 * MB), "late");
    assert.equal(keeping(group({ skipped: 2 }), 200 * MB), "late");
    assert.equal(keeping(group({ skipped: 2, behind: { streak: 3, since: 0, told: true } }), 200 * MB), "behind");
    assert.equal(keeping(group({ cycles: 0, behind: { streak: 1, since: 0, told: false } }), 200 * MB), "behind");
    // Its cycles cannot even start: worse than any of the above.
    assert.equal(keeping(group({ behind: { streak: 3, since: 0, told: true }, unprepared: { reason: "x", since: 0 } }), 200 * MB), "unprepared");
});

test("keepingTone and share", () => {
    assert.deepEqual(["ok", "idle", "slow", "behind", "unprepared"].map(state => keepingTone(state as never)), ["good", null, "warning", "bad", "bad"]);
    assert.equal(share(50, 200), 25);
    assert.equal(share(null, 200), null);
    assert.equal(share(10, 0), null);
});
