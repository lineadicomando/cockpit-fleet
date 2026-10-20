import { test } from "node:test";
import assert from "node:assert/strict";
import { chartsOf, formatPointTime, formatValue, valueDomain, valueTicks, withGaps } from "../src/lib/metrics.ts";
import type { MetricQuery } from "../src/lib/metrics.ts";

test("a missing stretch of values breaks the line", () => {
    const data = withGaps([[0, 1], [300, 2], [600, 3], [2400, 4], [2700, 5]], "raw");
    assert.deepEqual(data.map(datum => datum.y), [1, 2, 3, null, 4, 5]);
    assert.equal(data[3].x, 900_000);
    assert.deepEqual(withGaps([[0, 1], [400, 2], [700, 3]], "raw").map(datum => datum.y), [1, 2, 3]);
});

test("aggregates keep their lowest and highest, and break by their bucket", () => {
    const data = withGaps([[0, 5, 1, 9], [3600, 6, 2, 8], [3 * 3600 + 3600, 7, 7, 7]], "hour");
    assert.deepEqual(data[0], { x: 0, y: 5, low: 1, high: 9 });
    assert.deepEqual(data.map(datum => datum.y), [5, 6, null, 7]);
});

test("one chart per metric, one line per instance", () => {
    const query: MetricQuery = {
        resolution: "raw", since: 0, until: 1000,
        series: [
            { check_id: "c", check: "disk", name: "used_pct", instance: "/", unit: "%", points: [[0, 40]], truncated: false },
            { check_id: "c", check: "disk", name: "used_pct", instance: "/home", unit: "%", points: [[0, 70]], truncated: true },
            { check_id: "c", check: "disk", name: "free", instance: "/", unit: "B", points: [[0, 1024]], truncated: false },
        ],
    };
    const charts = chartsOf(query);
    assert.deepEqual(charts.map(chart => [chart.name, chart.lines.map(line => line.instance), chart.truncated]),
        [["used_pct", ["/", "/home"], true], ["free", ["/"], false]]);
    assert.deepEqual(valueDomain(charts[0]), [0, 100]);
    assert.deepEqual(valueDomain(charts[1]), [0, 1024]);
});

test("the values axis starts from zero unless values go below it", () => {
    const chart = (ys: number[]) => ({ name: "m", unit: "", truncated: false, lines: [{ instance: "", data: ys.map((y, x) => ({ x, y })) }] });
    assert.deepEqual(valueDomain(chart([3, 5])), [0, 5]);
    assert.deepEqual(valueDomain(chart([-2, 5])), [-2, 5]);
    assert.deepEqual(valueDomain(chart([0, 0])), [0, 1]);
    assert.deepEqual(valueDomain({ ...chart([]), lines: [] }), [0, 1]);
});

test("values and times read short", () => {
    assert.equal(formatValue(61.333, "%"), "61.3%");
    assert.equal(formatValue(1.234, "%"), "1.23%");
    assert.equal(formatValue(2 * 1024 ** 3, "B"), "2.0 GiB");
    assert.equal(formatValue(7200, "s"), "2 h");
    assert.equal(formatValue(3, "d"), "3 d");
    assert.equal(formatValue(1234.5, ""), "1235");
    assert.equal(formatPointTime(Date.UTC(2026, 9, 20), "day", "en"), "Oct 20, 2026");
});

test("byte ticks fall on round binary sizes", () => {
    const GiB = 1024 ** 3;
    assert.deepEqual(valueTicks([0, 27.9 * GiB], "B"), [0, 10 * GiB, 20 * GiB]);
    assert.deepEqual(valueTicks([0, 3000], "B"), [0, 1024, 2048]);
    assert.deepEqual(valueTicks([0, 3 * 1024 ** 2], "B"), [0, 1024 ** 2, 2 * 1024 ** 2, 3 * 1024 ** 2]);
    assert.equal(valueTicks([0, 100], "%"), undefined);
});
