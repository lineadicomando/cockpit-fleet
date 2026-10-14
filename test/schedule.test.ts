import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_SHAPE, calendarOf, shapeOf } from "../src/lib/schedule.ts";
import type { Shape } from "../src/lib/schedule.ts";

test("each shape of the builder becomes an OnCalendar expression", () => {
    const shape = (extra: Partial<Shape>): Shape => ({ ...DEFAULT_SHAPE, ...extra });
    assert.equal(calendarOf(shape({ repeat: "minutes", every: 15 })), "*-*-* *:00/15:00");
    assert.equal(calendarOf(shape({ repeat: "hourly", minute: 5 })), "*-*-* *:05:00");
    assert.equal(calendarOf(shape({ repeat: "daily", time: "2:30" })), "*-*-* 02:30:00");
    assert.equal(calendarOf(shape({ repeat: "weekdays", time: "06:00" })), "Mon..Fri *-*-* 06:00:00");
    assert.equal(calendarOf(shape({ repeat: "weekly", weekday: "Sun", time: "23:59" })), "Sun *-*-* 23:59:00");
    assert.equal(calendarOf(shape({ repeat: "monthly", day: 1, time: "00:00" })), "*-*-01 00:00:00");
});

test("a shape with a part that is not valid gives no expression", () => {
    assert.equal(calendarOf({ ...DEFAULT_SHAPE, time: "24:00" }), null);
    assert.equal(calendarOf({ ...DEFAULT_SHAPE, time: "noon" }), null);
    assert.equal(calendarOf({ ...DEFAULT_SHAPE, repeat: "hourly", minute: 60 }), null);
    assert.equal(calendarOf({ ...DEFAULT_SHAPE, repeat: "monthly", day: 31 }), null);
    assert.equal(calendarOf({ ...DEFAULT_SHAPE, repeat: "custom" }), null);
});

test("an expression made by the builder opens it where it was left", () => {
    for (const shape of [
        { ...DEFAULT_SHAPE, repeat: "minutes", every: 30 },
        { ...DEFAULT_SHAPE, repeat: "hourly", minute: 45 },
        { ...DEFAULT_SHAPE, repeat: "daily", time: "02:00" },
        { ...DEFAULT_SHAPE, repeat: "weekdays", time: "06:30" },
        { ...DEFAULT_SHAPE, repeat: "weekly", weekday: "Sat", time: "12:00" },
        { ...DEFAULT_SHAPE, repeat: "monthly", day: 15, time: "03:00" },
    ] as Shape[]) {
        assert.deepEqual(shapeOf(calendarOf(shape)!), shape);
    }
    assert.equal(shapeOf("Mon,Wed *-*-* 02:00").repeat, "custom");
    assert.equal(shapeOf("daily").repeat, "custom");
    assert.equal(shapeOf("*-*-* *:00/7:00").repeat, "custom");
});
