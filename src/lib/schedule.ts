// The builder of a schedule's calendar: a few common shapes, each turned into
// an OnCalendar expression. The backend checks the expression with
// systemd-analyze; anything else is typed as it is.

export type Repeat = "custom" | "minutes" | "hourly" | "daily" | "weekdays" | "weekly" | "monthly";

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export const EVERY_MINUTES = [5, 10, 15, 20, 30] as const;

export interface Shape {
    repeat: Repeat;
    // "HH:MM" for daily, weekdays, weekly and monthly; the minute for hourly.
    time: string;
    minute: number;
    every: number;
    weekday: string;
    day: number;
}

export const DEFAULT_SHAPE: Shape = { repeat: "daily", time: "02:00", minute: 0, every: 15, weekday: "Mon", day: 1 };

const two = (value: number) => String(value).padStart(2, "0");

function clock(time: string): string | null {
    const match = /^(\d{1,2}):(\d{2})$/.exec(time.trim());
    if (!match) return null;
    const [hour, minute] = [Number(match[1]), Number(match[2])];
    return hour < 24 && minute < 60 ? `${two(hour)}:${two(minute)}:00` : null;
}

// The expression of a shape, or null when a part of it is not valid.
export function calendarOf(shape: Shape): string | null {
    switch (shape.repeat) {
    case "minutes":
        return `*-*-* *:00/${shape.every}:00`;
    case "hourly":
        return Number.isInteger(shape.minute) && shape.minute >= 0 && shape.minute < 60 ? `*-*-* *:${two(shape.minute)}:00` : null;
    case "custom":
        return null;
    }
    const time = clock(shape.time);
    if (time === null) return null;
    switch (shape.repeat) {
    case "daily": return `*-*-* ${time}`;
    case "weekdays": return `Mon..Fri *-*-* ${time}`;
    case "weekly": return `${shape.weekday} *-*-* ${time}`;
    case "monthly": return Number.isInteger(shape.day) && shape.day >= 1 && shape.day <= 28 ? `*-*-${two(shape.day)} ${time}` : null;
    }
    return null;
}

// The shape an expression was built from, so that editing a schedule opens
// the builder where it was left; "custom" for anything else.
export function shapeOf(calendar: string): Shape {
    const text = calendar.trim();
    let match = /^\*-\*-\* \*:00\/(\d+):00$/.exec(text);
    if (match && (EVERY_MINUTES as readonly number[]).includes(Number(match[1])))
        return { ...DEFAULT_SHAPE, repeat: "minutes", every: Number(match[1]) };
    match = /^\*-\*-\* \*:(\d{2}):00$/.exec(text);
    if (match) return { ...DEFAULT_SHAPE, repeat: "hourly", minute: Number(match[1]) };
    match = /^(?:(Mon\.\.Fri|Mon|Tue|Wed|Thu|Fri|Sat|Sun) )?\*-\*-(\*|\d{2}) (\d{2}:\d{2}):00$/.exec(text);
    if (match) {
        const [, days, day, time] = match;
        if (day !== "*" && !days && Number(day) >= 1 && Number(day) <= 28)
            return { ...DEFAULT_SHAPE, repeat: "monthly", day: Number(day), time };
        if (day === "*" && days === "Mon..Fri") return { ...DEFAULT_SHAPE, repeat: "weekdays", time };
        if (day === "*" && days) return { ...DEFAULT_SHAPE, repeat: "weekly", weekday: days, time };
        if (day === "*") return { ...DEFAULT_SHAPE, repeat: "daily", time };
    }
    return { ...DEFAULT_SHAPE, repeat: "custom" };
}
