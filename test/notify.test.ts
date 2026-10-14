import { test } from "node:test";
import assert from "node:assert/strict";
import { EMPTY_FORM, destination, formOf, settingsOf, splitRecipients } from "../src/lib/notify.ts";
import type { Channel } from "../src/lib/types.ts";

test("recipients may be one per line or separated by commas", () => {
    assert.deepEqual(splitRecipients("a@x.org, b@x.org\n c@x.org;;"), ["a@x.org", "b@x.org", "c@x.org"]);
    assert.deepEqual(splitRecipients("  "), []);
});

test("each kind takes only its own settings", () => {
    const form = { ...EMPTY_FORM, chat_id: " -100 ", url: "https://h/x", host: "mail", recipients: "a@b.c", port: "" };
    assert.deepEqual(settingsOf("telegram", form), { chat_id: "-100", server: "https://api.telegram.org" });
    assert.deepEqual(settingsOf("webhook", form), { url: "https://h/x", method: "POST", template: '{"text": "$text"}', header: "Authorization" });
    assert.deepEqual(settingsOf("smtp", form), { host: "mail", security: "starttls", username: "", sender: "", recipients: ["a@b.c"] });
    assert.equal(settingsOf("smtp", { ...form, port: "2525" }).port, 2525);
    assert.deepEqual(settingsOf("matrix", { ...form, homeserver: "https://m", room_id: "!r:m" }), { homeserver: "https://m", room_id: "!r:m" });
});

test("a saved channel opens in the form as it was saved", () => {
    const channel = {
        kind: "smtp",
        settings: { host: "mail", port: 465, security: "tls", username: "", sender: "f@x.org", recipients: ["a@x.org", "b@x.org"] },
    } as unknown as Channel;
    const form = formOf(channel);
    assert.equal(form.port, "465");
    assert.equal(form.recipients, "a@x.org\nb@x.org");
    assert.deepEqual(settingsOf("smtp", form), channel.settings);
    assert.equal(destination(channel), "a@x.org, b@x.org");
});
