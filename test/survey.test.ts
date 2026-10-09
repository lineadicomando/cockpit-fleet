import { test } from "node:test";
import assert from "node:assert/strict";
import { answerProblem, collectAnswers, draftProblem, fromDraft, initialAnswers, newDraft, secretAnswers, toDraft } from "../src/lib/survey.ts";
import type { Answers } from "../src/lib/survey.ts";
import type { SurveyField } from "../src/lib/types.ts";

const SURVEY: SurveyField[] = [
    { name: "version", type: "text", required: true, min_length: 0, max_length: 5 },
    { name: "replicas", type: "number", min: 1, max: 10, default: 2 },
    { name: "channel", type: "choice", choices: ["stable", "beta"], default: "stable" },
    { name: "restart", type: "boolean" },
    { name: "note", type: "text" },
];

test("the launch form starts from the defaults", () => {
    assert.deepEqual(initialAnswers(SURVEY), { version: "", replicas: "2", channel: "stable", restart: false, note: "" });
});

test("answers are sent with their type and empty ones are left out", () => {
    const answers = { version: "1.2", replicas: "3", channel: "", restart: false, note: "  " };
    assert.deepEqual(collectAnswers(SURVEY, answers), { version: "1.2", replicas: 3, restart: false });
    assert.deepEqual(collectAnswers(SURVEY, { ...answers, replicas: "0.5", restart: true }), { version: "1.2", replicas: 0.5, restart: true });
});

test("problems of an answer are found before asking the backend", () => {
    const [version, replicas, , restart] = SURVEY;
    assert.equal(answerProblem(version, ""), "required");
    assert.equal(answerProblem(version, "1.2"), null);
    assert.equal(answerProblem(version, "1.2.3-rc1"), "long");
    assert.equal(answerProblem({ ...version, min_length: 3 }, "1"), "short");
    // Required, but the default answers for it.
    assert.equal(answerProblem({ ...replicas, required: true }, ""), null);
    assert.equal(answerProblem(replicas, "three"), "number");
    assert.equal(answerProblem(replicas, "0"), "min");
    assert.equal(answerProblem(replicas, "11"), "max");
    assert.equal(answerProblem(replicas, "10"), null);
    assert.equal(answerProblem(restart, false), null);
});

test("a secret answer is kept apart and sent as typed", () => {
    const survey: SurveyField[] = [...SURVEY, { name: "token", type: "secret", required: true }, { name: "pin", type: "secret" }];
    const answers: Answers = { ...initialAnswers(survey), version: "1", token: " hunter2 " };
    assert.equal(answers.pin, "");
    assert.equal("token" in collectAnswers(survey, answers), false);
    assert.deepEqual(secretAnswers(survey, answers), { token: " hunter2 " });
    assert.equal(answerProblem(survey[5], ""), "required");
    assert.equal(answerProblem(survey[5], " "), null);
    assert.equal(answerProblem(survey[6], ""), null);
    // A default typed before the type was changed is not saved with it.
    assert.equal(fromDraft({ ...newDraft(), name: "token", type: "secret", default: "x" }).default, undefined);
});

test("a field survives being edited", () => {
    const stored: SurveyField[] = [
        { name: "version", label: "Version", help: "As in the tag", type: "text", required: true, min_length: 0, max_length: 20, default: "1.0" },
        { name: "replicas", label: "", help: "", type: "number", required: false, min: 1, max: 10, default: 0 },
        { name: "channel", label: "", help: "", type: "choice", required: false, choices: ["stable", "beta"], default: "beta" },
        { name: "restart", label: "", help: "", type: "boolean", required: false, default: false },
        { name: "dry", label: "", help: "", type: "boolean", required: false },
    ];
    // What comes back differs only where undefined stands for "not set".
    const saved = JSON.parse(JSON.stringify(stored.map(field => fromDraft(toDraft(field)))));
    assert.deepEqual(saved, [
        { ...stored[0], min_length: undefined },
        stored[1], stored[2], stored[3], stored[4],
    ].map(field => JSON.parse(JSON.stringify(field))));
});

test("a draft is checked before it is saved", () => {
    const draft = (changes: object) => ({ ...newDraft(), name: "x", ...changes });
    const alone = (changes: object) => { const d = draft(changes); return draftProblem(d, [d]); };
    assert.equal(alone({}), null);
    assert.equal(alone({ name: "" }), "name");
    assert.equal(alone({ name: "my-var" }), "name");
    assert.equal(alone({ name: "ansible_user" }), "reserved");
    assert.equal(alone({ type: "number", low: "one" }), "number");
    assert.equal(alone({ type: "number", default: "many" }), "default");
    assert.equal(alone({ type: "choice", choices: " \n" }), "choices");
    assert.equal(alone({ type: "choice", choices: "a\na" }), "choices");
    assert.equal(alone({ type: "choice", choices: "a\nb", default: "c" }), "default");
    assert.equal(alone({ type: "choice", choices: "a\nb", default: "b" }), null);
    const first = draft({});
    const second = draft({});
    assert.equal(draftProblem(second, [first, second]), "duplicate");
});
