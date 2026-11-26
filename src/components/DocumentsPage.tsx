import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Breadcrumb,
    BreadcrumbItem,
    Content,
    ExpandableSection,
    Flex,
    FlexItem,
    FormGroup,
    FormHelperText,
    HelperText,
    HelperTextItem,
    Label,
    ModalBody,
    ModalHeader,
    ModalVariant,
    TextInput,
    Toolbar,
    ToolbarContent,
    ToolbarGroup,
    ToolbarItem,
} from "@patternfly/react-core";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatBytes } from "../lib/checks.ts";
import { basename, dirname } from "../lib/files.ts";
import { formatTime } from "../lib/format.ts";
import { useCockpitLocation, useLoad } from "../lib/hooks.ts";
import { saveBlob } from "../lib/cmdb.ts";
import { useCan } from "../lib/session.tsx";
import {
    DOCUMENT_CSS, documentLocation, documentPage, exportName, isText, linkTarget,
    type DocCommit, type DocFile, type DocList, type DocText, type Rendered,
} from "../lib/documents.ts";
import { downloadUrl, joinPath, save, uploadToStore } from "../lib/store.ts";
import { shellLine } from "../lib/terminal.ts";
import { AtTerminal, ConfirmDelete, FormModal, ListState, SectionHeader, message, openHost, openRun } from "./common.tsx";
import { CodeEditor } from "./CodeEditor.tsx";
import { FileManager, type FileOps, type FileTools } from "./FileManager.tsx";
import { Diff } from "./RunsPage.tsx";
import { Alert, Button, Modal, Spinner } from "./pf.tsx";

// Reports, studies, analyses in Markdown, written by people and by the
// assistant, with the files they show; the texts keep a history. A folder
// is a list as the store's; a text is shown as the backend renders it, in a
// frame where nothing runs.
export function DocumentsPage({ path }: { path: string }) {
    if (path && isText(path)) return <DocumentView key={path} path={path} />;
    return <DocumentsFolder directory={path} />;
}

function getDocument(path: string) {
    save(downloadUrl(path, "doc.get"), basename(path));
}

function DocumentsFolder({ directory }: { directory: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<DocList>("doc.list"));
    const tools = useRef<FileTools | null>(null);
    const [creating, setCreating] = useState(false);
    const canWrite = can("doc.write");
    const canUpload = can("doc.put");
    const go = (path: string) => cockpit.location.go(documentLocation(path));

    const ops: FileOps = {
        move: async moves => { await call("doc.move", { moves }); },
        copy: async copies => {
            for (const copy of copies) await call("doc.copy", copy);
            return { made: copies.map(copy => copy.to) };
        },
        remove: async paths => { await call("doc.delete", { paths }); },
        mkdir: async path => { await call("doc.mkdir", { path }); },
        upload: async (file, path, replace, onProgress, still) => (still() ? uploadToStore(file, path, { command: "doc.put", replace, onProgress }) : null),
        download: async path => getDocument(path),
    };

    return (
        <>
            <SectionHeader title={t("Documents")}>
                {canUpload && <Button variant="secondary" onClick={() => tools.current?.newFolder()}>{t("New folder")}</Button>}
                {canUpload && <Button variant="secondary" onClick={() => tools.current?.pick(directory, false)}>{t("Upload files")}</Button>}
                {canWrite && <Button variant="primary" onClick={() => setCreating(true)}>{t("New document")}</Button>}
            </SectionHeader>
            <Content component="p">
                {t("Reports of interventions, studies and analyses, written here or by the assistant, with the images and files they show. Every change to a text is kept with its author, and an earlier version can be restored. Runs never see them; do not write secrets here.")}
            </Content>
            {list.data && (
                <Content component="p">
                    {t("{{used}} used of {{limit}}.", { used: formatBytes(list.data.used), limit: formatBytes(list.data.limit) })}
                </Content>
            )}
            <FileManager<DocFile> kind="documents" directory={directory} go={go} list={list} data={list.data} reload={list.reload}
                can={{ upload: canUpload, move: can("doc.move"), copy: can("doc.copy"), delete: can("doc.delete") }}
                ops={ops} tools={tools}
                words={{
                    root: t("Documents"), top: t("the top of the documents"), searchAll: t("Search all the documents"), inAll: t("In all the documents"),
                    listLabel: t("Documents"), empty: t("No documents yet"), emptyFolder: t("This folder is empty"),
                    emptyBody: canWrite ? t("Write one, upload files or drop them here, or ask the assistant for a report.") : t("Documents are written by operators and by the assistant."),
                    missing: t("There is no folder {{path}} among the documents.", { path: directory }),
                    hint: t("Drop files or folders from your computer here or onto a folder; drag entries onto a folder, or onto the path above, to move them, holding Ctrl to copy them. Keys: Ctrl+A, Ctrl+X or Ctrl+C and Ctrl+V, Delete, F2, / to search; the menu key or Shift+F10 for an entry's menu."),
                    incomplete: t("There are more documents than can be listed here: only part of them is shown."),
                    clashTitle: t("Names already among the documents"), clashIntro: t("These are already among the documents:"),
                }}
                columns={[
                    { title: t("Size"), sort: "size", cell: entry => (entry.file ? formatBytes(entry.file.size) : "") },
                    { title: t("Kind"), cell: entry => entry.file && <DocKind file={entry.file} /> },
                    { title: t("Updated"), sort: "updated", cell: entry => (entry.file ? formatTime(entry.file.updated_at, i18n.language) : "") },
                ]}
                openFile={path => (isText(path) ? go(path) : getDocument(path))}
                fileActions={entry => (isText(entry.path) ? [{ title: t("Open"), onClick: () => go(entry.path) }] : [])}
                deleteText={paths => paths.length > 1 || !isText(paths[0])
                    ? t("What is deleted can be restored from the history if it is a text; an attachment cannot.")
                    : t("The text can be restored from the history afterwards.")} />
            {canUpload && <AtTerminal text={shellLine(["fleet", "doc", "put", "--file", "FILE", "--path", joinPath(directory, "FILE")])} />}
            {creating && <NewDocumentModal directory={directory} onClose={() => setCreating(false)}
                taken={new Set(list.data?.files.map(file => file.path) ?? [])} />}
        </>
    );
}

function DocKind({ file }: { file: DocFile }) {
    const { t } = useTranslation();
    if (file.text) return <>{t("Text")}</>;
    const by = file.created_by ? ` (${file.created_by})` : "";
    if (file.source) return <span title={file.source + by}>{file.origin === "export" ? t("Export of {{name}}", { name: basename(file.source) }) : t("Attachment of {{name}}", { name: basename(file.source) })}</span>;
    return <>{t("Attachment")}</>;
}

// The name of a new text; it is written when it is first saved.
function NewDocumentModal({ directory, taken, onClose }: { directory: string; taken: Set<string>; onClose: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState("");
    const trimmed = name.trim().replace(/^\/+|\/+$/g, "");
    const path = trimmed ? joinPath(directory, isText(trimmed) ? trimmed : `${trimmed}.md`) : "";
    const problem = !trimmed ? null : trimmed.split("/").some(part => !part || part === "." || part === "..") ? t("Not a usable name.")
        : taken.has(path) ? t("There is a document with this name already.") : null;
    return (
        <FormModal title={t("New document")} submitLabel={t("Write it")} variant={ModalVariant.small} onClose={onClose}
            isDisabled={!trimmed || !!problem}
            onSubmit={async () => cockpit.location.go(documentLocation(path), { mode: "edit", new: "1" })}>
            <FormGroup label={t("Name")} isRequired fieldId="document-name">
                <TextInput id="document-name" value={name} onChange={(_e, value) => setName(value)} autoFocus spellCheck={false} />
                <FormHelperText><HelperText><HelperTextItem variant={problem ? "error" : "default"}>
                    {problem ?? (directory ? t("In {{path}}. It ends in .md; a / makes folders within folders.", { path: directory }) : t("It ends in .md; a / makes folders within folders."))}
                </HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

// The trail from the top of the documents to a text.
function DocumentCrumbs({ path }: { path: string }) {
    const { t } = useTranslation();
    const parts = path.split("/");
    return (
        <Breadcrumb aria-label={t("Folders")} style={{ marginBottom: "0.5rem" }}>
            <BreadcrumbItem>
                <Button variant="link" isInline onClick={() => cockpit.location.go(documentLocation(""))}>{t("Documents")}</Button>
            </BreadcrumbItem>
            {parts.map((part, at) => {
                const here = parts.slice(0, at + 1).join("/");
                const last = at === parts.length - 1;
                return (
                    <BreadcrumbItem key={here} isActive={last}>
                        {last ? part : <Button variant="link" isInline onClick={() => cockpit.location.go(documentLocation(here))}>{part}</Button>}
                    </BreadcrumbItem>
                );
            })}
        </Breadcrumb>
    );
}

// What a click on a link of a document does: another document, a run, a
// host, a page of Fleet, or a page outside in a tab of its own.
function follow(href: string, frame: HTMLIFrameElement | null) {
    const target = linkTarget(href);
    if (!target) return;
    if (target.kind === "document") cockpit.location.go(documentLocation(target.path), target.fragment ? { at: target.fragment } : {});
    else if (target.kind === "fragment") frame?.contentDocument?.getElementById(target.id)?.scrollIntoView();
    else if (target.kind === "run") openRun(target.id);
    else if (target.kind === "host") openHost(target.id);
    else if (target.kind === "section") cockpit.location.go(target.id && target.section === "projects" ? [target.section, target.id] : [target.section]);
    else window.open(target.url, "_blank", "noopener,noreferrer");
}

// Whether the page is in the dark theme (index.tsx mirrors Cockpit's on it).
function useDarkTheme(): boolean {
    const root = document.documentElement;
    const [dark, setDark] = useState(() => root.classList.contains("pf-v6-theme-dark"));
    useEffect(() => {
        const observer = new MutationObserver(() => setDark(root.classList.contains("pf-v6-theme-dark")));
        observer.observe(root, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, [root]);
    return dark;
}

// A rendered document, in a frame where nothing can run: no scripts, and a
// policy that lets in only the images it carries. Its style is adopted into
// it from here, and its links are followed from here.
function DocumentFrame({ html, title, label, fill, at, frameRef }: {
    html: string;
    title: string;
    label: string;
    // Fills the box it is in and scrolls inside, beside the editor; else as tall as the document.
    fill?: boolean;
    // An id to scroll to once it is shown.
    at?: string;
    frameRef?: React.MutableRefObject<HTMLIFrameElement | null>;
}) {
    const own = useRef<HTMLIFrameElement | null>(null);
    const [height, setHeight] = useState(400);
    const dark = useDarkTheme();

    useEffect(() => {
        own.current?.contentDocument?.documentElement.classList.toggle("dark", dark);
    }, [dark]);

    function loaded() {
        const frame = own.current;
        const inside = frame?.contentDocument;
        const view = frame?.contentWindow as (Window & typeof globalThis) | null;
        if (!frame || !inside || !view) return;
        const sheet = new view.CSSStyleSheet();
        sheet.replaceSync(DOCUMENT_CSS);
        inside.adoptedStyleSheets = [sheet];
        inside.documentElement.classList.toggle("dark", dark);
        inside.addEventListener("click", event => {
            const link = (event.target as Element | null)?.closest?.("a");
            if (!link) return;
            event.preventDefault();
            follow(link.getAttribute("href") ?? "", frame);
        });
        if (!fill) setHeight(Math.max(200, inside.documentElement.scrollHeight + 4));
        if (at) inside.getElementById(at)?.scrollIntoView();
    }

    return (
        <iframe ref={element => { own.current = element; if (frameRef) frameRef.current = element; }}
            className="fleet-document-frame" title={label} sandbox="allow-same-origin allow-modals"
            srcDoc={documentPage(html, title, false)} onLoad={loaded}
            style={fill ? undefined : { blockSize: `${height}px` }} />
    );
}

function DocumentView({ path }: { path: string }) {
    const { t } = useTranslation();
    const can = useCan();
    const location = useCockpitLocation();
    const options = location.options as Record<string, string | undefined>;
    const editing = options.mode === "edit";
    const fresh = options.new === "1";
    const rendered = useLoad(() => (editing ? Promise.resolve(null) : call<Rendered>("doc.render", { path })), [editing]);
    const frame = useRef<HTMLIFrameElement | null>(null);
    const [dialog, setDialog] = useState<"history" | "delete" | null>(null);
    const [error, setError] = useState<string | null>(null);
    const folder = dirname(path);

    const edit = (on: boolean) => cockpit.location.go(documentLocation(path), on ? { mode: "edit" } : {});

    if (editing) {
        return (
            <>
                <DocumentCrumbs path={path} />
                <DocumentEditor path={path} fresh={fresh} onDone={() => edit(false)}
                    onCancel={() => (fresh ? cockpit.location.go(documentLocation(folder)) : edit(false))} />
            </>
        );
    }

    async function saveHtml() {
        const data = rendered.data;
        if (!data) return;
        saveBlob(new Blob([documentPage(data.html, data.title || basename(path), true)], { type: "text/html" }), exportName(path, ".html"));
    }

    return (
        <>
            <DocumentCrumbs path={path} />
            <Toolbar inset={{ default: "insetNone" }}>
                <ToolbarContent>
                    <ToolbarGroup>
                        {can("doc.write") && <ToolbarItem><Button variant="primary" onClick={() => edit(true)}>{t("Edit")}</Button></ToolbarItem>}
                        <ToolbarItem><Button variant="secondary" onClick={() => setDialog("history")}>{t("History")}</Button></ToolbarItem>
                    </ToolbarGroup>
                    <ToolbarGroup>
                        <ToolbarItem><Button variant="secondary" isDisabled={!rendered.data} onClick={() => frame.current?.contentWindow?.print()}>{t("Print or save as PDF")}</Button></ToolbarItem>
                        <ToolbarItem><Button variant="secondary" onClick={() => getDocument(path)}>{t("Download the text")}</Button></ToolbarItem>
                        <ToolbarItem><Button variant="secondary" isDisabled={!rendered.data} onClick={saveHtml}>{t("Download as a web page")}</Button></ToolbarItem>
                    </ToolbarGroup>
                    {can("doc.delete") && (
                        <ToolbarItem align={{ default: "alignEnd" }}>
                            <Button variant="secondary" isDanger onClick={() => setDialog("delete")}>{t("Delete")}</Button>
                        </ToolbarItem>
                    )}
                </ToolbarContent>
            </Toolbar>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {rendered.error
                ? <Alert variant="danger" isInline title={rendered.error.message} />
                : !rendered.data
                    ? <Spinner aria-label={t("Loading")} />
                    : (
                        <>
                            {rendered.data.refused.length > 0 && (
                                <Alert variant="info" isInline style={{ marginBottom: "1rem" }}
                                    title={t("images_refused_count", { count: rendered.data.refused.length })}>
                                    {t("A document shows only images among the documents, of at most 4 MB each and 16 MB in all; those from elsewhere are not fetched.")}
                                </Alert>
                            )}
                            {Object.keys(rendered.data.meta).length > 0 && (
                                <Flex spaceItems={{ default: "spaceItemsSm" }} style={{ marginBottom: "0.5rem" }}>
                                    {Object.entries(rendered.data.meta).filter(([key]) => key !== "title").map(([key, value]) => (
                                        <FlexItem key={key}><Label isCompact>{`${key}: ${value}`}</Label></FlexItem>
                                    ))}
                                </Flex>
                            )}
                            <DocumentFrame html={rendered.data.html} title={rendered.data.title || basename(path)} at={options.at}
                                label={t("The document {{name}}", { name: basename(path) })} frameRef={frame} />
                        </>
                    )}
            {dialog === "history" && (
                <DocumentHistory path={path} editable={can("doc.restore")} onClose={() => setDialog(null)}
                    onRestored={() => { setDialog(null); rendered.reload(); }} />
            )}
            {dialog === "delete" && (
                <ConfirmDelete name={basename(path)} what={t("The text can be restored from the history afterwards.")}
                    onClose={() => setDialog(null)}
                    onDelete={async () => {
                        setDialog(null);
                        try {
                            await call("doc.delete", { path });
                            cockpit.location.go(documentLocation(folder));
                        } catch (ex) {
                            setError(message(ex));
                        }
                    }} />
            )}
        </>
    );
}

// The text beside what it will look like, shown again a moment after each change.
function DocumentEditor({ path, fresh, onDone, onCancel }: { path: string; fresh: boolean; onDone: () => void; onCancel: () => void }) {
    const { t } = useTranslation();
    const loaded = useLoad(() => (fresh ? Promise.resolve({ path, content: "", sha256: "", size: 0 }) : call<DocText>("doc.read", { path })));
    const [text, setText] = useState<string | null>(null);
    const [summary, setSummary] = useState("");
    const [preview, setPreview] = useState<Rendered | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [leaving, setLeaving] = useState(false);
    const base = loaded.data;
    const current = text ?? base?.content ?? "";
    const dirty = !!base && current !== base.content;

    useEffect(() => {
        if (!base) return;
        const timer = setTimeout(() => {
            call<Rendered>("doc.render", { path, content: current }).then(setPreview, () => null);
        }, 400);
        return () => clearTimeout(timer);
    }, [current, base, path]);

    async function store() {
        if (!base || saving) return;
        setSaving(true);
        setProblem(null);
        try {
            await call("doc.write", { path, content: current, expected: base.sha256, message: summary.trim() });
            onDone();
        } catch (ex) {
            setProblem(message(ex));
            setSaving(false);
        }
    }

    if (loaded.error) return <Alert variant="danger" isInline title={loaded.error.message} />;
    if (!base) return <Spinner aria-label={t("Loading")} />;
    return (
        <>
            <Toolbar inset={{ default: "insetNone" }}>
                <ToolbarContent>
                    <ToolbarItem><Button variant="primary" isDisabled={saving || (!dirty && !fresh)} isLoading={saving} onClick={store}>{t("Save")}</Button></ToolbarItem>
                    <ToolbarItem><Button variant="link" onClick={() => (dirty ? setLeaving(true) : onCancel())}>{t("Cancel")}</Button></ToolbarItem>
                    <ToolbarItem style={{ flex: "1 1 20rem" }}>
                        <TextInput aria-label={t("What changed")} placeholder={t("What changed (optional, for the history)")} value={summary}
                            onChange={(_e, value) => setSummary(value)} maxLength={80} />
                    </ToolbarItem>
                </ToolbarContent>
            </Toolbar>
            {problem && <Alert variant="danger" isInline title={problem} style={{ marginBottom: "1rem" }} />}
            <div className="fleet-document-edit">
                <CodeEditor className="fleet-editor" label={t("The text of {{name}}", { name: basename(path) })} path={path} value={current}
                    onChange={setText} onSave={store} placeholder={t("# Title\n\nWrite in Markdown.")} />
                {preview
                    ? <DocumentFrame html={preview.html} title={preview.title} fill label={t("Preview")} />
                    : <div className="fleet-document-frame"><Spinner size="md" aria-label={t("Loading")} /></div>}
            </div>
            <Content component="small">{t("Ctrl+S saves. Raw HTML is shown as text; images come from the documents only.")}</Content>
            {leaving && (
                <FormModal title={t("Give up the changes?")} submitLabel={t("Give them up")} isDanger variant={ModalVariant.small}
                    onClose={() => setLeaving(false)} onSubmit={async () => onCancel()}>
                    <Content component="p">{t("What was written since the last save is lost.")}</Content>
                </FormModal>
            )}
        </>
    );
}

function CommitEntry({ path, commit, onRestore }: { path: string; commit: DocCommit; onRestore?: (commit: DocCommit) => void }) {
    const { t, i18n } = useTranslation();
    const [open, setOpen] = useState(false);
    // Moved or renamed since: its own name at that time.
    const named = commit.paths.length === 1 ? commit.paths[0] : path;
    return (
        <ExpandableSection isExpanded={open} onToggle={(_event, expanded) => setOpen(expanded)}
            toggleContent={(
                <>
                    {commit.subject}
                    <Content component="small" style={{ display: "block" }}>
                        {commit.author} · {formatTime(commit.time, i18n.language)} · <code>{commit.revision.slice(0, 10)}</code>
                    </Content>
                </>
            )}>
            {onRestore && (
                <Button variant="secondary" size="sm" style={{ marginBlockEnd: "0.5rem" }} onClick={() => onRestore({ ...commit, paths: [named] })}>
                    {t("Put it back as it was after this change")}
                </Button>
            )}
            {open && <CommitDiff path={path} revision={commit.revision} />}
        </ExpandableSection>
    );
}

function CommitDiff({ path, revision }: { path: string; revision: string }) {
    const { t } = useTranslation();
    const diff = useLoad(() => call<{ text: string; cut: boolean }>("doc.diff", { revision }));
    if (diff.error) return <Alert variant="danger" isInline title={diff.error.message} />;
    if (!diff.data) return <Spinner size="md" aria-label={t("Loading")} />;
    return (
        <>
            {diff.data.text ? <Diff text={diff.data.text} /> : <Content component="p">{t("Nothing changed in {{path}}.", { path })}</Content>}
            {diff.data.cut && <Content component="small">{t("This is only the beginning of the change.")}</Content>}
        </>
    );
}

function DocumentHistory({ path, editable, onRestored, onClose }: {
    path: string;
    editable: boolean;
    onRestored: () => void;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const history = useLoad(() => call<{ commits: DocCommit[] }>("doc.history", { path }));
    const [problem, setProblem] = useState<string | null>(null);
    const restore = async (commit: DocCommit) => {
        setProblem(null);
        try {
            await call("doc.restore", { path: commit.paths[0], revision: commit.revision, ...(commit.paths[0] !== path ? { to: path } : {}) });
            onRestored();
        } catch (ex) {
            setProblem(message(ex));
        }
    };
    return (
        <Modal variant={ModalVariant.large} isOpen onClose={onClose}>
            <ModalHeader title={t("Changes to {{path}}", { path })} />
            <ModalBody>
                {problem && <Alert variant="danger" isInline title={problem} style={{ marginBottom: "1rem" }} />}
                <ListState loaded={history} isEmpty={data => data.commits.length === 0}
                    emptyTitle={t("No changes yet")}
                    emptyBody={t("Every change saved here is recorded with its author.")}>
                    {data => data.commits.map((commit, index) => (
                        <CommitEntry key={commit.revision} path={path} commit={commit}
                            onRestore={editable && index > 0 ? restore : undefined} />
                    ))}
                </ListState>
            </ModalBody>
        </Modal>
    );
}
