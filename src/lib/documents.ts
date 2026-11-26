// Documents: texts in Markdown and their attachments, kept on the controller
// with a history of the texts (documents.py). The backend renders them
// (docrender.py); the page shows what it gets in a frame of its own, and
// prints or saves it from there.

import type { Listed, Listing } from "./store.ts";

export interface DocFile extends Listed {
    // A text (.md), with a history; any other file is an attachment.
    text: boolean;
    origin?: "upload" | "export";
    // The document an attachment belongs to, or what it was made from.
    source?: string;
    created_by?: string | null;
    sha256?: string;
}

export interface DocList extends Listing<DocFile> {
    used: number;
    limit: number;
}

export interface DocText {
    path: string;
    content: string;
    sha256: string;
    size: number;
}

export interface Rendered {
    path: string;
    html: string;
    meta: Record<string, string>;
    title: string;
    headings: { level: number; text: string; id: string }[];
    images: number;
    refused: string[];
    sha256: string;
}

export interface DocCommit {
    revision: string;
    author: string;
    time: number;
    subject: string;
    paths: string[];
}

export function isText(path: string): boolean {
    return /\.md$/i.test(path);
}

// The place of a document, or of a folder of documents, in the page.
export function documentLocation(path: string): string[] {
    return ["documents", ...(path ? path.split("/") : [])];
}

// Where a link of a rendered document goes. The renderer lets through only
// http(s), mailto, fleet: and #fragments; anything else is refused here too.
export type LinkTarget =
    | { kind: "document"; path: string; fragment: string }
    | { kind: "fragment"; id: string }
    | { kind: "run"; id: string }
    | { kind: "host"; id: string }
    | { kind: "section"; section: string; id: string }
    | { kind: "outside"; url: string };

// Kinds of fleet: links and the section of the page each opens.
const SECTIONS: Record<string, string> = {
    template: "templates", inventory: "inventories", project: "projects", workflow: "workflows",
    schedule: "schedules", check: "checks", credential: "credentials", store: "store",
};

export function linkTarget(href: string): LinkTarget | null {
    if (href.startsWith("#")) return { kind: "fragment", id: decodeURIComponent(href.slice(1)) };
    if (/^(https?:\/\/|mailto:)/i.test(href)) return { kind: "outside", url: href };
    const found = /^fleet:([a-z]+)(?:\/([^#]*))?(?:#(.*))?$/.exec(href);
    if (!found) return null;
    const [, kind, rest = "", fragment = ""] = found;
    let target: string;
    try {
        target = decodeURIComponent(rest);
    } catch {
        return null;
    }
    if (kind === "doc") return target && !target.split("/").includes("..") ? { kind: "document", path: target, fragment } : null;
    if (kind === "run" && target) return { kind: "run", id: target };
    if (kind === "host" && target) return { kind: "host", id: target };
    if (SECTIONS[kind]) return { kind: "section", section: SECTIONS[kind], id: target };
    return null;
}

function escape(text: string): string {
    return text.replace(/[&<>"']/g, ch => `&#${ch.charCodeAt(0)};`);
}

// The colours of a document in the dark theme: set on the frame's root by
// the page as Cockpit's theme changes, and taken from the reader's system
// by a saved page. On paper a document is always light.
const DARK = `
  color-scheme: dark; --text: #e0e0e0; --page: #1b1d21; --muted: #a3a3a3; --rule: #4d4d4d; --rule-light: #3c3f42;
  --code: #2b2e33; --pre: #26292d; --head: #2b2e33; --link: #92c5f9; --refused: #a3a3a3;`;

// What a document looks like, on the page and on paper.
export const DOCUMENT_CSS = `
:root { color-scheme: light; --text: #1f1f1f; --page: #fff; --muted: #4d4d4d; --rule: #d2d2d2; --rule-light: #e0e0e0;
  --code: #f2f2f2; --pre: #f5f5f5; --head: #f5f5f5; --link: #0066cc; --refused: #6a6e73; }
:root.dark {${DARK} }
body { margin: 0; padding: 1.5rem 2rem; color: var(--text); background: var(--page);
  font: 15px/1.6 "Red Hat Text", "RedHatText", "Noto Sans", "Helvetica Neue", Arial, sans-serif; overflow-wrap: break-word; }
h1, h2, h3, h4, h5, h6 { font-family: "Red Hat Display", "RedHatDisplay", "Noto Sans", Arial, sans-serif; line-height: 1.25; margin: 1.4em 0 0.5em; }
h1 { font-size: 1.9em; border-bottom: 1px solid var(--rule); padding-bottom: 0.2em; }
h2 { font-size: 1.45em; border-bottom: 1px solid var(--rule-light); padding-bottom: 0.15em; }
h3 { font-size: 1.2em; } h4 { font-size: 1.05em; } h5, h6 { font-size: 1em; }
body > :first-child { margin-top: 0; }
p, ul, ol, blockquote, pre, table { margin: 0 0 1em; }
a { color: var(--link); }
code { font-family: "Red Hat Mono", "RedHatMono", "Liberation Mono", Menlo, monospace; font-size: 0.9em; background: var(--code); border-radius: 3px; padding: 0.1em 0.3em; }
pre { background: var(--pre); border: 1px solid var(--rule-light); border-radius: 4px; padding: 0.75em 1em; overflow-x: auto; }
pre code { background: none; padding: 0; font-size: 0.85em; }
blockquote { margin-left: 0; padding: 0 1em; color: var(--muted); border-left: 4px solid var(--rule); }
ul, ol { padding-left: 2em; }
li > ul, li > ol { margin-bottom: 0; }
li.task { list-style: none; margin-left: -1.4em; }
li.task > input { margin-right: 0.4em; }
table { border-collapse: collapse; display: block; overflow-x: auto; max-width: 100%; }
th, td { border: 1px solid var(--rule); padding: 0.35em 0.7em; }
th { background: var(--head); font-weight: 600; }
.align-left { text-align: left; } .align-center { text-align: center; } .align-right { text-align: right; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 1.5em 0; }
img { max-width: 100%; }
.image-refused { color: var(--refused); font-style: italic; }
@page { margin: 18mm 16mm; }
@media print {
  :root, :root.dark { color-scheme: light; --text: #1f1f1f; --page: #fff; --muted: #4d4d4d; --rule: #d2d2d2; --rule-light: #e0e0e0;
    --code: #f2f2f2; --pre: #f5f5f5; --head: #f5f5f5; --link: #0066cc; --refused: #6a6e73; }
  body { padding: 0; font-size: 11pt; }
  a { color: inherit; }
  pre, blockquote, tr, img { break-inside: avoid; }
  h1, h2, h3, h4 { break-after: avoid; }
  table { display: table; }
  pre { white-space: pre-wrap; }
}
`;

// A saved page follows the theme of the system it is opened on.
export const SAVED_CSS = `@media screen and (prefers-color-scheme: dark) { :root {${DARK} } }\n`;

// The page a rendered document is shown in. In the frame of the page no
// style can be written in it (Cockpit's policy, which the frame inherits,
// refuses inline styles): the page adopts DOCUMENT_CSS into it instead.
// Saved as a file it carries the sheet itself, and a policy of its own
// that lets in nothing but it and the images it holds.
export function documentPage(html: string, title: string, standalone: boolean): string {
    const policy = standalone ? "default-src 'none'; img-src data:; style-src 'unsafe-inline'" : "default-src 'none'; img-src data:";
    return [
        "<!DOCTYPE html>",
        '<html><head><meta charset="utf-8">',
        `<meta http-equiv="Content-Security-Policy" content="${policy}">`,
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        `<title>${escape(title)}</title>`,
        standalone ? `<style>${DOCUMENT_CSS}${SAVED_CSS}</style>` : "",
        "</head><body>",
        html,
        "</body></html>",
    ].join("\n");
}

// The name a document is saved under with another extension.
export function exportName(path: string, extension: string): string {
    const name = path.split("/").pop() || "document.md";
    return name.replace(/\.md$/i, "") + extension;
}

// The text an agent's call wrote, for a link to it in the chat: of a write,
// a restore, a copy or a move that went through.
export function writtenDocument(call: { tool: string; status: string; input: unknown }): string | null {
    if (call.status !== "completed" || !call.input || typeof call.input !== "object") return null;
    const input = call.input as Record<string, unknown>;
    const field = call.tool === "doc_write" ? input.path
        : call.tool === "doc_restore" ? input.to || input.path
        : call.tool === "doc_copy" || call.tool === "doc_move" ? input.to
        : null;
    return typeof field === "string" && isText(field) ? field : null;
}
