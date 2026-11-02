import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    AlertActionLink,
    Button,
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
    Tab,
    TabTitleText,
    TextInput,
} from "@patternfly/react-core";
import type { IAction } from "@patternfly/react-table";
import cockpit from "cockpit";
import { ansibleSource } from "../lib/ansible-source.ts";
import {
    ancestors, basename, dirname, filterFiles, isInside, isUsablePath, isYaml, relocate, renamed, topmost, type FileNode, type PathChoice,
} from "../lib/files.ts";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Chunk, Commit, FileContent, Inventory, Project, ProjectFiles, Run } from "../lib/types.ts";
import { fileBase64, importArchive } from "../lib/upload.ts";
import { CodeEditor } from "./CodeEditor.tsx";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { FileTree } from "./FileTree.tsx";
import { ModuleHelpButton } from "./ModuleHelp.tsx";
import { PathInput } from "./PathInput.tsx";
import { Diff, Output } from "./RunsPage.tsx";
import { Alert, FileUpload, Modal, SearchInput, Spinner, TabPanel, Tabs } from "./pf.tsx";

// Up to this many files the tree starts open: there is nothing to look for.
const OPEN_ALL = 40;
// What the backend keeps of one file.
const MAX_FILE = 1024 * 1024;

interface ChangeResult {
    revision: string;
    files: { path: string; sha256: string }[];
    check_run_ids: string[];
    inventory_run_ids: string[];
}

// A file open in a tab: what was read, and what is in the editor now.
interface OpenFile {
    path: string;
    file: FileContent | null;
    text: string;
    error: string | null;
}

// The open files of each project or inventory, kept while the page is left
// for another tab of Fleet; gone when the browser's tab is reloaded.
const kept = new Map<string, { tabs: OpenFile[]; current: string | null; empty: string[] }>();

const isDirty = (tab: OpenFile) => tab.file !== null && !tab.file.binary && tab.text !== tab.file.content;

// What file.download gives, saved by the browser.
function saveDownload(name: string, data: string) {
    const binary = atob(data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
    const url = URL.createObjectURL(new Blob([bytes]));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// A job that follows a change (a syntax check, an import), until it ends.
function useRunEnd(id: string) {
    const run = useLoad(() => call<Run>("run.get", { id }));
    const active = !run.data || ["pending_approval", "queued", "running"].includes(run.data.status);
    usePolling(run.reload, 1000, active && !run.error);
    return { run, active };
}

async function wholeLog(id: string): Promise<string> {
    let text = "";
    for (let offset = 0, more = true; more;) {
        const chunk = await call<Chunk>("run.log", { id, offset });
        text += chunk.text;
        offset = chunk.offset;
        more = chunk.more;
    }
    return text;
}

// The outcome of a syntax check, which is a job: it is followed until it ends.
function CheckResult({ id }: { id: string }) {
    const { t } = useTranslation();
    const { run, active } = useRunEnd(id);
    const [log, setLog] = useState("");

    useEffect(() => {
        if (active) return;
        let stale = false;
        wholeLog(id).then(text => { if (!stale) setLog(text); }).catch(() => { /* the alert still says how it went */ });
        return () => { stale = true; };
    }, [active]);

    if (run.error) return <Alert variant="danger" isInline title={run.error.message} />;
    if (active || !run.data) return <Alert variant="info" isInline customIcon={<Spinner size="md" aria-hidden />} title={t("Checking the syntax…")} />;

    const { status, options, error } = run.data;
    const open = <AlertActionLink onClick={() => cockpit.location.go(["runs", id])}>{t("Open the run")}</AlertActionLink>;
    if (status === "successful" && options.lint !== "findings") {
        return <Alert variant="success" isInline title={options.lint === "clean" ? t("The syntax is correct, and ansible-lint has no remarks") : t("The syntax is correct")} />;
    }
    if (status === "successful") {
        return <Alert variant="warning" isInline title={t("The syntax is correct; ansible-lint has remarks")} actionLinks={open}><Output text={log} /></Alert>;
    }
    if (status === "failed") {
        return <Alert variant="danger" isInline title={t("The syntax check failed")} actionLinks={open}><Output text={log} /></Alert>;
    }
    return <Alert variant="danger" isInline title={error || t("The check did not run")} actionLinks={open} />;
}

// The outcome of an archive imported into the files, which is a job too.
function ImportResult({ id, onDone }: { id: string; onDone: () => void }) {
    const { t } = useTranslation();
    const { run, active } = useRunEnd(id);
    useEffect(() => { if (!active) onDone(); }, [active]);
    if (run.error) return <Alert variant="danger" isInline title={run.error.message} />;
    if (active || !run.data) return <Alert variant="info" isInline customIcon={<Spinner size="md" aria-hidden />} title={t("Importing the archive…")} />;
    const open = <AlertActionLink onClick={() => cockpit.location.go(["runs", id])}>{t("Open the run")}</AlertActionLink>;
    if (run.data.status === "successful") return <Alert variant="success" isInline title={t("The archive was imported")} />;
    return <Alert variant="danger" isInline title={run.data.error || t("The archive could not be imported")} actionLinks={open} />;
}

function CommitDiff({ id, revision }: { id: string; revision: string }) {
    const { t } = useTranslation();
    const diff = useLoad(() => call<{ text: string; cut: boolean }>("file.diff", { id, revision }));
    if (diff.error) return <Alert variant="danger" isInline title={diff.error.message} />;
    if (!diff.data) return <Spinner size="md" aria-label={t("Loading")} />;
    return (
        <>
            <Diff text={diff.data.text} />
            {diff.data.cut && <Content component="small">{t("This is only the beginning of the change.")}</Content>}
        </>
    );
}

function CommitEntry({ id, commit, onRestore }: { id: string; commit: Commit; onRestore?: (commit: Commit) => void }) {
    const { t, i18n } = useTranslation();
    const [open, setOpen] = useState(false);
    return (
        <ExpandableSection
            isExpanded={open}
            onToggle={(_event, expanded) => setOpen(expanded)}
            toggleContent={(
                <>
                    {commit.subject}
                    <Content component="small" style={{ display: "block" }}>
                        {commit.author} · {formatTime(commit.time, i18n.language)} · <code>{commit.revision.slice(0, 10)}</code>
                    </Content>
                </>
            )}
        >
            {onRestore && (
                <Button variant="secondary" size="sm" style={{ marginBlockEnd: "0.5rem" }} onClick={() => onRestore(commit)}>
                    {t("Put it back as it was after this change")}
                </Button>
            )}
            {/* Asked for only when opened: most changes are never looked at. */}
            {open && <CommitDiff id={id} revision={commit.revision} />}
        </ExpandableSection>
    );
}

function HistoryModal({ id, path, editable, onRestored, onClose }: {
    id: string;
    path?: string;
    editable: boolean;
    onRestored: () => void;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const history = useLoad(() => call<{ commits: Commit[] }>("file.history", path ? { id, path } : { id }));
    const [problem, setProblem] = useState<string | null>(null);
    const restore = async (commit: Commit) => {
        setProblem(null);
        try {
            await call("file.restore", { id, path, revision: commit.revision });
            onRestored();
            onClose();
        } catch (ex) {
            setProblem(message(ex));
        }
    };
    return (
        <Modal variant={ModalVariant.large} isOpen onClose={onClose}>
            <ModalHeader title={path ? t("Changes to {{path}}", { path }) : t("Changes")} />
            <ModalBody>
                {problem && <Alert variant="danger" isInline title={problem} style={{ marginBottom: "1rem" }} />}
                <ListState loaded={history} isEmpty={data => data.commits.length === 0}
                    emptyTitle={t("No changes yet")}
                    emptyBody={t("Every change saved here is recorded with its author.")}>
                    {data => data.commits.map(commit => (
                        <CommitEntry key={commit.revision} id={id} commit={commit}
                            onRestore={path && editable ? restore : undefined} />
                    ))}
                </ListState>
            </ModalBody>
        </Modal>
    );
}

// Asks for a path: of a new file or folder, or the new path of one.
function PathModal({ title, submitLabel, initial, help, onSubmit, onClose }: {
    title: string;
    submitLabel: string;
    initial: string;
    help: string;
    onSubmit: (path: string) => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [value, setValue] = useState(initial);
    return (
        <FormModal title={title} submitLabel={submitLabel} onSubmit={() => onSubmit(value)} onClose={onClose}
            isDisabled={!isUsablePath(value) || value === initial} variant={ModalVariant.small}>
            <FormGroup label={t("Path")} isRequired fieldId="file-path">
                <TextInput id="file-path" value={value} onChange={(_e, next) => setValue(next)} spellCheck={false} autoFocus />
                <FormHelperText><HelperText><HelperTextItem>{help}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

// Moves or copies files and folders into a folder chosen, or typed.
function RelocateModal({ paths, copy, directories, onSubmit, onClose }: {
    paths: string[];
    copy: boolean;
    directories: string[];
    onSubmit: (into: string) => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [into, setInto] = useState("");
    const target = into.trim().replace(/\/+$/, "");
    const moves = target === "" || isUsablePath(target) ? relocate(paths, target) : null;
    const choices: PathChoice[] = directories.filter(path => !paths.some(moved => isInside(path, moved))).map(path => ({ path, directory: true }));
    const what = paths.length === 1 ? paths[0] : t("files_count", { count: paths.length });
    return (
        <FormModal title={copy ? t("Copy {{what}}", { what }) : t("Move {{what}}", { what })} submitLabel={copy ? t("Copy") : t("Move")}
            onSubmit={() => onSubmit(target)} onClose={onClose} isDisabled={!moves || moves.length === 0} variant={ModalVariant.small}>
            <FormGroup label={t("To the folder")} fieldId="relocate-into">
                <PathInput id="relocate-into" label={t("To the folder")} value={into} onChange={setInto} choices={choices} placeholder={t("The top")} />
                <FormHelperText><HelperText><HelperTextItem variant={moves === null ? "error" : "default"}>
                    {moves === null ? t("A folder cannot go into itself.") : t("Empty for the top. A folder that does not exist is created.")}
                </HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

function ImportModal({ id, into, onStarted, onClose }: { id: string; into: string; onStarted: (run: string) => void; onClose: () => void }) {
    const { t } = useTranslation();
    const [file, setFile] = useState<File | null>(null);
    return (
        <FormModal title={into ? t("Import an archive into {{path}}", { path: into }) : t("Import an archive")} submitLabel={t("Import")}
            onSubmit={async () => { if (file) { onStarted((await importArchive(id, into, file)).run_id); onClose(); } }}
            onClose={onClose} isDisabled={!file || file.size === 0} variant={ModalVariant.small}>
            <Content component="p">{t("Its files are added, as one change; a file of the same path is replaced. A folder that holds everything, like project-1.0/, is left out.")}</Content>
            <FormGroup label={t("Archive")} isRequired fieldId="import-archive" role="group">
                <FileUpload id="import-archive" filename={file?.name ?? ""} filenamePlaceholder={t("Choose a file")} browseButtonText={t("Browse…")} clearButtonText={t("Clear")}
                    hideDefaultPreview onFileInputChange={(_e, chosen) => setFile(chosen)} onClearClick={() => setFile(null)} />
                <FormHelperText><HelperText><HelperTextItem>{t("A tar archive, compressed or not, or a zip. Links in it are refused, and files larger than 1 MB.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

type Dialog =
    | { kind: "new-file" | "new-folder"; in: string }
    | { kind: "rename"; path: string }
    | { kind: "move" | "copy" | "delete"; paths: string[] }
    | { kind: "history"; path?: string }
    | { kind: "replace"; files: File[]; into: string; existing: string[] }
    | { kind: "import"; into: string }
    | { kind: "discard"; paths: string[]; then: () => void };

// The files of one project: shown for any project, edited when the project is
// written here. Also those of an inventory written here, whose id starts with i.
export function FilesPage({ id }: { id: string }) {
    const { t } = useTranslation();
    const can = useCan();
    const ofInventory = id.startsWith("i");
    // Whose files they are: a project, or an inventory with no kind of project.
    const owners = useLoad<{ id: string; name: string; kind: Project["kind"] | Inventory["kind"] }[]>(() => ofInventory
        ? call<{ inventories: Inventory[] }>("inventory.list").then(data => data.inventories)
        : call<{ projects: Project[] }>("project.list").then(data => data.projects));
    const list = useLoad(() => call<ProjectFiles>("file.list", { id }));
    const saved = kept.get(id);
    const [tabs, setTabs] = useState<OpenFile[]>(saved?.tabs ?? []);
    const [current, setCurrent] = useState<string | null>(saved?.current ?? null);
    // Folders made here that hold no file yet: git keeps none, so neither does the backend.
    const [empty, setEmpty] = useState<string[]>(saved?.empty ?? []);
    const [open, setOpen] = useState<Set<string> | null>(null);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [query, setQuery] = useState("");
    const [note, setNote] = useState("");
    const [problem, setProblem] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [checkId, setCheckId] = useState<string | null>(null);
    const [importId, setImportId] = useState<string | null>(null);
    const [dialog, setDialog] = useState<Dialog | null>(null);
    const uploader = useRef<HTMLInputElement>(null);
    const [uploadInto, setUploadInto] = useState("");

    const project = owners.data?.find(candidate => candidate.id === id);
    const editable = list.data?.editable ?? false;
    const files = useMemo(() => (list.data?.files ?? []).map(entry => entry.path), [list.data]);
    const fileSet = useMemo(() => new Set(files), [files]);
    const directories = useMemo(() => {
        const all = new Set([...(list.data?.directories ?? []), ...empty.flatMap(path => [...ancestors(path), path])]);
        return [...all].sort();
    }, [list.data, empty]);
    const tab = tabs.find(candidate => candidate.path === current) ?? null;
    const anyDirty = tabs.some(isDirty);

    useEffect(() => { kept.set(id, { tabs, current, empty }); }, [id, tabs, current, empty]);

    // Leaving the page with changes not saved asks first.
    useEffect(() => {
        if (!anyDirty) return;
        const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [anyDirty]);

    // What starts open: everything when there is little, else the way to the open file.
    useEffect(() => {
        if (!list.data || open !== null) return;
        setOpen(new Set(list.data.files.length <= OPEN_ALL ? list.data.directories : current ? ancestors(current) : []));
    }, [list.data]);

    // A folder made here is no longer needed once a file is in it.
    useEffect(() => {
        setEmpty(paths => paths.filter(path => !files.some(file => isInside(file, path))));
    }, [files]);

    const filtered = useMemo(() => filterFiles(files, query), [files, query]);
    const shownOpen = useMemo(() => new Set([...(open ?? []), ...filtered.open]), [open, filtered]);

    function patchTab(path: string, patch: Partial<OpenFile>) {
        setTabs(all => all.map(candidate => (candidate.path === path ? { ...candidate, ...patch } : candidate)));
    }

    async function read(path: string) {
        try {
            const loaded = await call<FileContent>("file.read", { id, path });
            patchTab(path, { file: loaded, text: loaded.content, error: null });
        } catch (ex) {
            patchTab(path, { error: message(ex) });
        }
    }

    function openFile(path: string) {
        setCheckId(null);
        setNote("");
        setCurrent(path);
        setOpen(previous => new Set([...(previous ?? []), ...ancestors(path)]));
        if (!tabs.some(candidate => candidate.path === path)) {
            setTabs(all => [...all, { path, file: null, text: "", error: null }]);
            read(path);
        }
    }

    function closeTabs(paths: string[]) {
        setTabs(all => {
            const left = all.filter(candidate => !paths.includes(candidate.path));
            if (current && paths.includes(current)) setCurrent(left[left.length - 1]?.path ?? null);
            return left;
        });
    }

    // Closing what has changes not saved asks first.
    function guardClose(paths: string[], then: () => void = () => closeTabs(paths)) {
        const changed = tabs.filter(candidate => paths.includes(candidate.path) && isDirty(candidate)).map(candidate => candidate.path);
        if (changed.length) setDialog({ kind: "discard", paths: changed, then });
        else then();
    }

    // One change of the files, all or nothing, on the revision listed.
    async function apply(operations: object[], message?: string): Promise<ChangeResult> {
        const done = await call<ChangeResult>("file.change", {
            id, operations, revision: list.data?.revision ?? "", ...(message ? { message } : {}),
        });
        setSelected(new Set());
        list.reload();
        return done;
    }

    // A change made with no dialog to show what went wrong: drag and drop.
    async function attempt(action: () => Promise<unknown>) {
        setProblem(null);
        try {
            await action();
        } catch (ex) {
            setProblem(message(ex));
        }
    }

    // Open tabs follow what was moved, and go with what was deleted.
    function followMoves(moves: { path: string; to: string }[]) {
        const move = (path: string) => {
            for (const { path: from, to } of moves) {
                const next = renamed(path, from, to);
                if (next !== null) return next;
            }
            return path;
        };
        setTabs(all => all.map(candidate => ({ ...candidate, path: move(candidate.path) })));
        setCurrent(path => (path ? move(path) : path));
        setEmpty(paths => paths.map(move));
        setOpen(previous => new Set([...(previous ?? [])].map(move)));
    }

    async function relocateTo(paths: string[], into: string, copy: boolean) {
        const moves = relocate(paths, into);
        if (!moves) throw new Error(t("A folder cannot go into itself."));
        if (moves.length === 0) return;
        await apply(moves.map(({ path, to }) => ({ op: copy ? "copy" : "move", path, to })));
        if (!copy) followMoves(moves);
        setOpen(previous => new Set([...(previous ?? []), ...(into ? [...ancestors(into), into] : [])]));
    }

    async function remove(paths: string[]) {
        const gone = topmost(paths);
        const real = gone.filter(path => fileSet.has(path) || files.some(file => isInside(file, path) && file !== path));
        if (real.length) await apply(real.map(path => ({ op: "delete", path })));
        setEmpty(previous => previous.filter(path => !gone.some(deleted => isInside(path, deleted))));
        closeTabs(tabs.filter(candidate => gone.some(deleted => isInside(candidate.path, deleted))).map(candidate => candidate.path));
    }

    async function upload(chosen: File[], into: string, replace: string[]) {
        const tooLarge = chosen.filter(file => file.size > MAX_FILE);
        if (tooLarge.length) throw new Error(t("{{name}} is larger than 1 MB.", { name: tooLarge[0].name }));
        const operations = [];
        for (const file of chosen) {
            const path = into ? `${into}/${file.name}` : file.name;
            const expected = replace.includes(path) ? (await call<FileContent>("file.read", { id, path })).sha256 : "";
            operations.push({ op: "write", path, data: await fileBase64(file), expected });
        }
        await apply(operations, chosen.length === 1 ? undefined : t("Upload {{number}} files", { number: chosen.length }));
        // What was replaced and is open, unchanged, is read again.
        for (const path of replace) {
            const open = tabs.find(candidate => candidate.path === path);
            if (open && !isDirty(open)) read(path);
        }
    }

    function startUpload(chosen: File[], into: string) {
        if (!chosen.length) return;
        const existing = chosen.map(file => (into ? `${into}/${file.name}` : file.name)).filter(path => fileSet.has(path));
        if (existing.length) setDialog({ kind: "replace", files: chosen, into, existing });
        else attempt(() => upload(chosen, into, []));
    }

    async function download(path: string) {
        await attempt(async () => {
            const got = await call<{ name: string; data: string }>("file.download", path ? { id, path } : { id });
            saveDownload(got.name, got.data);
        });
    }

    async function save() {
        if (!tab?.file) return;
        const { path, text } = tab;
        setSaving(true);
        setProblem(null);
        try {
            const done = await call<{ sha256: string; check_run_id: string | null }>(
                "file.write", { id, path, content: text, expected: tab.file.sha256, message: note });
            patchTab(path, { file: { ...tab.file, content: text, sha256: done.sha256 } });
            setNote("");
            setCheckId(done.check_run_id);
            list.reload();
        } catch (ex) {
            setProblem(message(ex));
        }
        setSaving(false);
    }

    async function check() {
        if (!current) return;
        await attempt(async () => setCheckId((await call<{ run_id: string }>("file.check", { id, path: current })).run_id));
    }

    function toggle(path: string) {
        setOpen(previous => {
            const next = new Set(previous ?? []);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    }

    function pickFiles(into: string) {
        setUploadInto(into);
        uploader.current?.click();
    }

    const inHere = (node: FileNode) => (node.children ? node.path : dirname(node.path));

    function actions(node: FileNode): IAction[] {
        const isDirectory = node.children !== undefined;
        const items: IAction[] = [];
        if (!isDirectory) items.push({ title: t("Open"), onClick: () => openFile(node.path) });
        if (editable && isDirectory) {
            items.push(
                { title: t("New file here"), onClick: () => setDialog({ kind: "new-file", in: node.path }) },
                { title: t("New folder here"), onClick: () => setDialog({ kind: "new-folder", in: node.path }) },
                { title: t("Upload files here"), onClick: () => pickFiles(node.path) },
                { title: t("Import an archive here"), onClick: () => setDialog({ kind: "import", into: node.path }) },
                { isSeparator: true },
            );
        }
        if (editable) {
            items.push(
                { title: t("Rename"), onClick: () => setDialog({ kind: "rename", path: node.path }) },
                { title: t("Move to…"), onClick: () => setDialog({ kind: "move", paths: selected.has(node.path) ? [...selected] : [node.path] }) },
                { title: t("Copy to…"), onClick: () => setDialog({ kind: "copy", paths: selected.has(node.path) ? [...selected] : [node.path] }) },
            );
        }
        items.push({ title: isDirectory ? t("Download as an archive") : t("Download"), onClick: () => download(node.path) });
        if (editable && !isDirectory) items.push({ title: t("Changes"), onClick: () => setDialog({ kind: "history", path: node.path }) });
        if (editable) {
            items.push({ isSeparator: true },
                { title: t("Delete"), onClick: () => setDialog({ kind: "delete", paths: selected.has(node.path) ? [...selected] : [node.path] }) });
        }
        return items;
    }

    // Ansible's keywords, and the modules installed on the controller; not in
    // an inventory, whose files are hosts and variables, not tasks.
    const complete = useMemo(() => (current && editable && !ofInventory && isYaml(current) ? ansibleSource(current) : undefined), [current, editable, ofInventory]);
    const currentDirectory = current ? dirname(current) : "";

    return (
        <>
            <SectionHeader title={project?.name ?? t("Files")}>
                {editable && <Button variant="primary" onClick={() => setDialog({ kind: "new-file", in: currentDirectory })}>{t("New file")}</Button>}
                {editable && <Button variant="secondary" onClick={() => setDialog({ kind: "new-folder", in: currentDirectory })}>{t("New folder")}</Button>}
                {editable && <Button variant="secondary" onClick={() => pickFiles("")}>{t("Upload files")}</Button>}
                {editable && <Button variant="secondary" onClick={() => setDialog({ kind: "import", into: "" })}>{t("Import an archive")}</Button>}
                {editable && <Button variant="secondary" onClick={() => setDialog({ kind: "history" })}>{t("All changes")}</Button>}
                <Button variant="secondary" onClick={() => download("")}>{t("Download all")}</Button>
                {can("module.help") && !ofInventory && <ModuleHelpButton />}
                {ofInventory
                    ? <Button variant="secondary" onClick={() => guardClose(tabs.map(c => c.path), () => cockpit.location.go(["inventories"]))}>{t("All inventories")}</Button>
                    : <Button variant="secondary" onClick={() => guardClose(tabs.map(c => c.path), () => cockpit.location.go(["projects"]))}>{t("All projects")}</Button>}
            </SectionHeader>
            <input ref={uploader} type="file" multiple hidden aria-hidden tabIndex={-1}
                onChange={event => { startUpload([...(event.target.files ?? [])], uploadInto); event.target.value = ""; }} />
            {project && !ofInventory && project.kind !== "managed" && (
                <Alert variant="info" isInline isPlain style={{ marginBottom: "1rem" }}
                    title={project.kind === "git"
                        ? t("These files come from a git repository: they are changed there, and fetched again with “Update from git”.")
                        : project.kind === "archive"
                            ? t("These files come from an uploaded archive: to change them, upload another one.")
                            : t("These files are a directory of this server: they are changed there.")} />
            )}
            {problem && <Alert variant="danger" isInline title={problem} style={{ marginBottom: "1rem" }} />}
            {importId && <div style={{ marginBottom: "1rem" }}><ImportResult key={importId} id={importId} onDone={list.reload} /></div>}
            <ListState loaded={list} isEmpty={data => data.files.length === 0 && empty.length === 0 && !editable}
                emptyTitle={t("No files")} emptyBody={t("This project has no files.")}>
                {data => (
                    <div className="fleet-files">
                        <div className="fleet-files-tree">
                            {data.truncated && <Alert variant="warning" isInline isPlain title={t("Only the first files of this project are listed.")} />}
                            <div className="fleet-files-filter">
                                <SearchInput aria-label={t("Filter files")} placeholder={t("Filter files")} value={query}
                                    onChange={(_e, value) => setQuery(value)} onClear={() => setQuery("")} />
                            </div>
                            {editable && selected.size > 0 && (
                                <Flex className="fleet-files-selection" gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                                    <FlexItem>{t("files_selected", { count: selected.size })}</FlexItem>
                                    <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "move", paths: [...selected] })}>{t("Move to…")}</Button>
                                    <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "copy", paths: [...selected] })}>{t("Copy to…")}</Button>
                                    <Button variant="secondary" size="sm" isDanger onClick={() => setDialog({ kind: "delete", paths: [...selected] })}>{t("Delete")}</Button>
                                    <Button variant="link" size="sm" onClick={() => setSelected(new Set())}>{t("Clear the selection")}</Button>
                                </Flex>
                            )}
                            {data.files.length === 0 && empty.length === 0
                                ? <Content component="p">{ofInventory
                                    ? t("Create the first file of this inventory: hosts.yml, for example, with group_vars and host_vars beside it.")
                                    : t("Create the first file of this project: a playbook, for example site.yml.")}</Content>
                                : (
                                    <FileTree label={t("Files")} files={filtered.files} directories={query ? [] : directories}
                                        open={shownOpen} onToggle={toggle} current={current}
                                        selected={selected} onSelect={setSelected} editable={editable}
                                        actions={actions} onOpen={openFile}
                                        onMove={(paths, into) => attempt(() => relocateTo(paths, into, false))}
                                        onDropFiles={(chosen, into) => startUpload(chosen, into)}
                                        onRename={path => setDialog({ kind: "rename", path })}
                                        onDelete={paths => setDialog({ kind: "delete", paths })} />
                                )}
                        </div>
                        <div>
                            {tabs.length === 0 && <Content component="p">{t("Choose a file to see it.")}</Content>}
                            {tabs.length > 0 && (
                                <>
                                    <Tabs panelId="fleet-file-panel" activeKey={current ?? ""} onSelect={(_e, key) => { setCurrent(String(key)); setCheckId(null); }}
                                        aria-label={t("Open files")}>
                                        {tabs.map(candidate => (
                                            <Tab key={candidate.path} eventKey={candidate.path}
                                                title={<TabTitleText>{basename(candidate.path)}{isDirty(candidate) ? " •" : ""}</TabTitleText>} />
                                        ))}
                                    </Tabs>
                                    <TabPanel panelId="fleet-file-panel" active={current ?? ""}>
                                        {tab && (
                                            <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }} style={{ paddingBlockStart: "1rem" }}>
                                                <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} alignItems={{ default: "alignItemsCenter" }}>
                                                    <FlexItem>
                                                        <code>{tab.path}</code>
                                                        {isDirty(tab) && <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }}>{t("Not saved")}</Label>}
                                                    </FlexItem>
                                                    <Flex gap={{ default: "gapSm" }}>
                                                        {isYaml(tab.path) && !ofInventory && <Button variant="secondary" onClick={check} isDisabled={!tab.file || isDirty(tab)}>{t("Check syntax")}</Button>}
                                                        {editable && <Button variant="secondary" onClick={() => setDialog({ kind: "history", path: tab.path })}>{t("Changes")}</Button>}
                                                        <Button variant="secondary" onClick={() => download(tab.path)}>{t("Download")}</Button>
                                                        <Button variant="secondary" onClick={() => guardClose([tab.path])}>{t("Close")}</Button>
                                                    </Flex>
                                                </Flex>
                                                {tab.error && <Alert variant="danger" isInline title={tab.error} />}
                                                {tab.file?.binary && (
                                                    <Alert variant="info" isInline isPlain
                                                        title={t("This file is not text ({{size}} bytes): it cannot be shown or edited here.", { size: tab.file.size ?? 0 })} />
                                                )}
                                                {tab.file && !tab.file.binary && (
                                                    <CodeEditor key={tab.path} className="fleet-editor" label={tab.path} path={tab.path} value={tab.text}
                                                        onChange={text => patchTab(tab.path, { text })}
                                                        readOnly={!editable} complete={complete}
                                                        onSave={() => { if (editable && isDirty(tab) && !saving) save(); }} />
                                                )}
                                                {!tab.file && !tab.error && <Spinner aria-label={t("Loading")} />}
                                                {editable && tab.file && !tab.file.binary && (
                                                    <Flex alignItems={{ default: "alignItemsCenter" }}>
                                                        <FlexItem grow={{ default: "grow" }}>
                                                            <TextInput aria-label={t("What changed")} placeholder={t("What changed (optional)")} value={note}
                                                                onChange={(_e, value) => setNote(value)} maxLength={80} />
                                                        </FlexItem>
                                                        <Button variant="primary" onClick={save} isLoading={saving} isDisabled={!isDirty(tab) || saving}>{t("Save")}</Button>
                                                    </Flex>
                                                )}
                                                {checkId && <CheckResult key={checkId} id={checkId} />}
                                            </Flex>
                                        )}
                                    </TabPanel>
                                </>
                            )}
                        </div>
                    </div>
                )}
            </ListState>
            {(dialog?.kind === "new-file" || dialog?.kind === "new-folder") && (
                <PathModal title={dialog.kind === "new-file" ? t("New file") : t("New folder")} submitLabel={t("Create")}
                    initial={dialog.in ? `${dialog.in}/` : ""}
                    help={dialog.kind === "new-file"
                        ? t("Inside the project, for example roles/web/tasks/main.yml. Folders are created as needed.")
                        : t("A folder is kept once a file is in it.")}
                    onSubmit={async next => {
                        if (dialog.kind === "new-folder") {
                            setEmpty(paths => [...paths, next]);
                            setOpen(previous => new Set([...(previous ?? []), ...ancestors(next), next]));
                        } else {
                            await call("file.write", { id, path: next, content: "", expected: "" });
                            list.reload();
                            openFile(next);
                        }
                        setDialog(null);
                    }}
                    onClose={() => setDialog(null)} />
            )}
            {dialog?.kind === "rename" && (
                <PathModal title={t("Rename {{path}}", { path: dialog.path })} submitLabel={t("Rename")} initial={dialog.path}
                    help={t("The new path inside the project: a folder is renamed with everything in it.")}
                    onSubmit={async next => {
                        const moves = [{ path: dialog.path, to: next }];
                        if (empty.includes(dialog.path)) followMoves(moves);
                        else {
                            await apply([{ op: "move", path: dialog.path, to: next }]);
                            followMoves(moves);
                        }
                        setDialog(null);
                    }}
                    onClose={() => setDialog(null)} />
            )}
            {(dialog?.kind === "move" || dialog?.kind === "copy") && (
                <RelocateModal paths={dialog.paths} copy={dialog.kind === "copy"} directories={directories}
                    onSubmit={async into => { await relocateTo(dialog.paths, into, dialog.kind === "copy"); setDialog(null); }}
                    onClose={() => setDialog(null)} />
            )}
            {dialog?.kind === "delete" && (
                <ConfirmDelete name={dialog.paths.length === 1 ? dialog.paths[0] : t("files_count", { count: dialog.paths.length })}
                    what={t("What is deleted goes from the project, folders with everything in them, as one change; it stays in the record of changes. Open files among them are closed.")}
                    onDelete={async () => { await remove(dialog.paths); setDialog(null); }}
                    onClose={() => setDialog(null)} />
            )}
            {dialog?.kind === "replace" && (
                <FormModal title={t("Replace files?")} submitLabel={t("Replace")} isDanger variant={ModalVariant.small}
                    onSubmit={async () => { await upload(dialog.files, dialog.into, dialog.existing); setDialog(null); }}
                    onClose={() => setDialog(null)}>
                    <Content component="p">{t("These files exist already, and are replaced by those uploaded:")}</Content>
                    <Content component="ul">{dialog.existing.map(path => <Content component="li" key={path}><code>{path}</code></Content>)}</Content>
                </FormModal>
            )}
            {dialog?.kind === "import" && (
                <ImportModal id={id} into={dialog.into} onStarted={setImportId} onClose={() => setDialog(null)} />
            )}
            {dialog?.kind === "history" && (
                <HistoryModal id={id} path={dialog.path} editable={editable} onClose={() => setDialog(null)}
                    onRestored={() => {
                        list.reload();
                        const restored = dialog.path;
                        tabs.filter(candidate => restored && isInside(candidate.path, restored)).forEach(candidate => read(candidate.path));
                    }} />
            )}
            {dialog?.kind === "discard" && (
                <FormModal title={t("Discard the changes?")} submitLabel={t("Discard")} isDanger variant={ModalVariant.small}
                    onSubmit={async () => { const then = dialog.then; setDialog(null); then(); }}
                    onClose={() => setDialog(null)}>
                    <Content component="p">{t("These files have changes that were not saved:")}</Content>
                    <Content component="ul">{dialog.paths.map(path => <Content component="li" key={path}><code>{path}</code></Content>)}</Content>
                </FormModal>
            )}
        </>
    );
}
