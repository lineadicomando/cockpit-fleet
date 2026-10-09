// The survey of a job template: what the forms hold while it is edited and
// answered, and what goes to the backend. The backend validates all of it
// again; the checks here only save a round trip.

import type { SurveyField, SurveyType, SurveyValue } from "./types.ts";

// Text inputs hold strings, also for numbers; a checkbox holds a boolean.
export type Answers = Record<string, string | boolean>;

export type AnswerProblem = "required" | "number" | "min" | "max" | "short" | "long";

export function initialAnswers(survey: SurveyField[]): Answers {
    const answers: Answers = {};
    for (const field of survey) {
        if (field.type === "boolean") answers[field.name] = field.default === true;
        else answers[field.name] = field.default === undefined ? "" : String(field.default);
    }
    return answers;
}

export function answerProblem(field: SurveyField, value: string | boolean | undefined): AnswerProblem | null {
    if (field.type === "boolean") return null;
    const text = typeof value === "string" ? value : "";
    if (field.type === "secret") return field.required && text === "" ? "required" : null;
    if (text.trim() === "") {
        // The backend falls back to the default when there is one.
        return field.required && field.default === undefined ? "required" : null;
    }
    if (field.type === "number") {
        const number = Number(text);
        if (!Number.isFinite(number)) return "number";
        if (field.min !== undefined && number < field.min) return "min";
        if (field.max !== undefined && number > field.max) return "max";
    }
    if (field.type === "text") {
        if (text.length < (field.min_length ?? 0)) return "short";
        if (field.max_length !== undefined && text.length > field.max_length) return "long";
    }
    return null;
}

// What run.start takes: a field left empty is left out. Secret answers are not
// here: each goes to fleet-secret first, and its ticket is added afterwards.
export function collectAnswers(survey: SurveyField[], answers: Answers): Record<string, SurveyValue> {
    const result: Record<string, SurveyValue> = {};
    for (const field of survey) {
        const value = answers[field.name];
        if (field.type === "secret") continue;
        if (field.type === "boolean") result[field.name] = value === true;
        else if (typeof value === "string" && value.trim() !== "")
            result[field.name] = field.type === "number" ? Number(value) : value;
    }
    return result;
}

// The secret answers that were given, by variable, exactly as typed.
export function secretAnswers(survey: SurveyField[], answers: Answers): Record<string, string> {
    const result: Record<string, string> = {};
    for (const field of survey) {
        const value = answers[field.name];
        if (field.type === "secret" && typeof value === "string" && value !== "") result[field.name] = value;
    }
    return result;
}

// One field while an administrator edits it: every input is a string.
export interface FieldDraft {
    key: number;
    name: string;
    label: string;
    help: string;
    type: SurveyType;
    required: boolean;
    default: string;
    // One choice per line.
    choices: string;
    // Smallest and largest value of a number, or length of a text.
    low: string;
    high: string;
}

let nextKey = 1;

export function newDraft(): FieldDraft {
    return { key: nextKey++, name: "", label: "", help: "", type: "text", required: false, default: "", choices: "", low: "", high: "" };
}

export function toDraft(field: SurveyField): FieldDraft {
    const text = (value: unknown) => value === undefined || value === null ? "" : String(value);
    const [low, high] = field.type === "number"
        ? [field.min, field.max]
        : [field.min_length || undefined, field.max_length];
    return {
        key: nextKey++,
        name: field.name,
        label: field.label ?? "",
        help: field.help ?? "",
        type: field.type,
        required: field.required ?? false,
        default: text(field.default),
        choices: (field.choices ?? []).join("\n"),
        low: text(low),
        high: text(high),
    };
}

export type DraftProblem = "name" | "reserved" | "duplicate" | "number" | "choices" | "default";

const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const lines = (text: string) => text.split("\n").map(line => line.trim()).filter(Boolean);
const isNumber = (text: string) => text.trim() !== "" && Number.isFinite(Number(text));

export function draftProblem(draft: FieldDraft, all: FieldDraft[]): DraftProblem | null {
    const name = draft.name.trim();
    if (!NAME.test(name)) return "name";
    if (name.toLowerCase().startsWith("ansible_")) return "reserved";
    if (all.some(other => other !== draft && other.name.trim() === name)) return "duplicate";
    if (draft.type === "number" || draft.type === "text") {
        if ([draft.low, draft.high].some(value => value.trim() !== "" && !isNumber(value))) return "number";
    }
    if (draft.type === "number" && draft.default.trim() !== "" && !isNumber(draft.default)) return "default";
    if (draft.type === "choice") {
        const choices = lines(draft.choices);
        if (choices.length === 0 || new Set(choices).size !== choices.length) return "choices";
        if (draft.default !== "" && !choices.includes(draft.default)) return "default";
    }
    return null;
}

export function fromDraft(draft: FieldDraft): SurveyField {
    const field: SurveyField = {
        name: draft.name.trim(),
        label: draft.label.trim(),
        help: draft.help.trim(),
        type: draft.type,
        required: draft.required,
    };
    const number = (text: string) => text.trim() === "" ? undefined : Number(text);
    if (draft.type === "text") {
        field.min_length = number(draft.low);
        field.max_length = number(draft.high);
        if (draft.default !== "") field.default = draft.default;
    } else if (draft.type === "number") {
        field.min = number(draft.low);
        field.max = number(draft.high);
        field.default = number(draft.default);
    } else if (draft.type === "choice") {
        field.choices = lines(draft.choices);
        if (draft.default !== "") field.default = draft.default;
    } else if (draft.type === "boolean" && draft.default !== "") {
        field.default = draft.default === "true";
    }
    return field;
}
