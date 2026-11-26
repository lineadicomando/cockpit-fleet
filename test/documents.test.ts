import { test } from "node:test";
import assert from "node:assert/strict";
import { channels } from "./mocks/cockpit.ts";
import { DOCUMENT_CSS, SAVED_CSS, documentLocation, documentPage, exportName, isText, linkTarget, skillOf, writtenDocument } from "../src/lib/documents.ts";
import { uploadToStore } from "../src/lib/store.ts";

test("the links of a rendered document go where the renderer said, and nowhere else", () => {
    assert.deepEqual(linkTarget("fleet:doc/reports/a%20b.md#cause"), { kind: "document", path: "reports/a b.md", fragment: "cause" });
    assert.deepEqual(linkTarget("#cause"), { kind: "fragment", id: "cause" });
    assert.deepEqual(linkTarget("fleet:run/42"), { kind: "run", id: "42" });
    assert.deepEqual(linkTarget("fleet:host/web01"), { kind: "host", id: "web01" });
    assert.deepEqual(linkTarget("fleet:template/t1"), { kind: "section", section: "templates", id: "t1" });
    assert.deepEqual(linkTarget("https://example.org/x"), { kind: "outside", url: "https://example.org/x" });
    assert.deepEqual(linkTarget("mailto:a@b.c"), { kind: "outside", url: "mailto:a@b.c" });
    for (const href of ["javascript:alert(1)", "fleet:doc/../x.md", "fleet:doc/", "fleet:nothing/1", "data:text/html,x", "fleet:doc/%E0", "//evil.org"]) {
        assert.equal(linkTarget(href), null, href);
    }
});

test("a document's page: in the frame no style of its own, saved with its sheet and a policy", () => {
    const shown = documentPage("<p>x</p>", "A <b> title", false);
    assert.ok(!shown.includes("<style>"));
    assert.ok(shown.includes(`content="default-src 'none'; img-src data:"`));
    assert.ok(shown.includes("<title>A &#60;b&#62; title</title>"));
    const saved = documentPage("<p>x</p>", "T", true);
    assert.ok(saved.includes(`<style>${DOCUMENT_CSS}${SAVED_CSS}</style>`));
    assert.ok(saved.includes("style-src 'unsafe-inline'"));
    assert.ok(saved.includes("<p>x</p>"));
});

test("names and places", () => {
    assert.equal(isText("a/B.MD"), true);
    assert.equal(isText("a.png"), false);
    assert.deepEqual(documentLocation("a/b.md"), ["documents", "a", "b.md"]);
    assert.deepEqual(documentLocation(""), ["documents"]);
    assert.equal(exportName("reports/web01.md", ".html"), "web01.html");
});

test("an attachment is uploaded through doc.put, with no offset", async () => {
    channels.length = 0;
    const upload = uploadToStore(new Blob([new Uint8Array([1, 2, 3])]), "r/graph.png",
        { command: "doc.put", params: { source: "r/r.md" } });
    const channel = channels[0];
    assert.deepEqual((channel.options.spawn as string[]).slice(-1), ["doc.put"]);
    await new Promise(resolve => setTimeout(resolve, 0));
    const sent = channel.bytes();
    const line = sent.indexOf(10);
    assert.deepEqual(JSON.parse(new TextDecoder().decode(sent.subarray(0, line))),
        { path: "r/graph.png", size: 3, replace: false, progress: true, source: "r/r.md" });
    upload.cancel();
    await upload.done.catch(() => null);
});

test("the chat links to the text an agent wrote", () => {
    const done = (tool: string, input: unknown) => writtenDocument({ tool, status: "completed", input });
    assert.equal(done("doc_write", { path: "r/a.md", content: "x" }), "r/a.md");
    assert.equal(done("doc_restore", { path: "a.md", revision: "abc", to: "b.md" }), "b.md");
    assert.equal(done("doc_move", { path: "a.md", to: "x/a.md" }), "x/a.md");
    assert.equal(done("doc_write", { path: "a.png" }), null);
    assert.equal(done("doc_delete", { path: "a.md" }), null);
    assert.equal(writtenDocument({ tool: "doc_write", status: "failed", input: { path: "a.md" } }), null);
});

test("the skill a path of the documents is in", () => {
    assert.equal(skillOf("skills/patching/SKILL.md"), "patching");
    assert.equal(skillOf("skills/patching"), "patching");
    assert.equal(skillOf("skills"), null);
    assert.equal(skillOf("reports/skills/a.md"), null);
});
