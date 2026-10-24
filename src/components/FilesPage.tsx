import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Alert, AlertActionLink, Button, Content, ExpandableSection, Flex, FlexItem, FormGroup, FormHelperText, HelperText, HelperTextItem,
    Label, Modal, ModalBody, ModalHeader, ModalVariant, Spinner, TextInput, TreeView, type TreeViewDataItem,
} from "@patternfly/react-core";
import { FileIcon, FolderIcon, FolderOpenIcon } from "@patternfly/react-icons";
import cockpit from "cockpit";
import { ansibleSource } from "../lib/ansible-source.ts";
import { ancestors, buildTree, isUsablePath, isYaml, type FileNode } from "../lib/files.ts";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Chunk, Commit, FileContent, Project, ProjectFiles, Run } from "../lib/types.ts";
import { CodeEditor } from "./CodeEditor.tsx";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { ModuleHelpButton } from "./ModuleHelp.tsx";
import { Diff, Output } from "./RunsPage.tsx";

// Up to this many files the tree starts open: there is nothing to look for.
const OPEN_ALL = 40;

// The outcome of a syntax check, which is a job: it is followed until it ends.
function CheckResult({ id }: { id: string }) {
    const { t } = useTranslation();
    const run = useLoad(() => call<Run>("run.get", { id }));
    const [log, setLog] = useState("");
    const active = !run.data || ["pending_approval", "queued", "running"].includes(run.data.status);
    usePolling(run.reload, 1000, active && !run.error);

    useEffect(() => {
        if (active) return;
        let stale = false;
        (async () => {
            let text = "";
            for (let offset = 0, more = true; more;) {
                const chunk = await call<Chunk>("run.log", { id, offset });
                text += chunk.text;
                offset = chunk.offset;
                more = chunk.more;
            }
            if (!stale) setLog(text);
        })().catch(() => { /* the alert still says how it went */ });
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

function CommitEntry({ id, commit }: { id: string; commit: Commit }) {
    const { i18n } = useTranslation();
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
            {/* Asked for only when opened: most changes are never looked at. */}
            {open && <CommitDiff id={id} revision={commit.revision} />}
        </ExpandableSection>
    );
}

function HistoryModal({ id, path, onClose }: { id: string; path?: string; onClose: () => void }) {
    const { t } = useTranslation();
    const history = useLoad(() => call<{ commits: Commit[] }>("file.history", path ? { id, path } : { id }));
    return (
        <Modal variant={ModalVariant.large} isOpen onClose={onClose}>
            <ModalHeader title={path ? t("Changes to {{path}}", { path }) : t("Changes")} />
            <ModalBody>
                <ListState loaded={history} isEmpty={data => data.commits.length === 0}
                    emptyTitle={t("No changes yet")}
                    emptyBody={t("Every change saved here is recorded with its author.")}>
                    {data => data.commits.map(commit => <CommitEntry key={commit.revision} id={id} commit={commit} />)}
                </ListState>
            </ModalBody>
        </Modal>
    );
}

// Asks for the path of a new file, or for the new path of an existing one.
function PathModal({ title, submitLabel, initial, onSubmit, onClose }: {
    title: string;
    submitLabel: string;
    initial: string;
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
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem>{t("Inside the project, for example roles/web/tasks/main.yml. Directories are created as needed.")}</HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

// The files of one project: shown for any project, edited when the project is
// written here.
export function FilesPage({ id }: { id: string }) {
    const { t } = useTranslation();
    const can = useCan();
    const projects = useLoad(() => call<{ projects: Project[] }>("project.list"));
    const list = useLoad(() => call<ProjectFiles>("file.list", { id }));
    const [path, setPath] = useState<string | null>(null);
    const [file, setFile] = useState<FileContent | null>(null);
    const [text, setText] = useState("");
    const [note, setNote] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [checkId, setCheckId] = useState<string | null>(null);
    const [dialog, setDialog] = useState<"new" | "rename" | "delete" | "history" | "file-history" | null>(null);
    // What was asked for while there are changes not saved; done once they are given up.
    const [pending, setPending] = useState<(() => void) | null>(null);
    const request = useRef(0);

    const project = projects.data?.projects.find(candidate => candidate.id === id);
    const editable = list.data?.editable ?? false;
    const dirty = file !== null && text !== file.content;

    const guard = (action: () => void) => (dirty ? setPending(() => action) : action());

    async function load(next: string) {
        const mine = ++request.current;
        setPath(next);
        setFile(null);
        setError(null);
        setCheckId(null);
        setNote("");
        try {
            const loaded = await call<FileContent>("file.read", { id, path: next });
            if (mine !== request.current) return;
            setFile(loaded);
            setText(loaded.content);
        } catch (ex) {
            if (mine === request.current) setError(message(ex));
        }
    }

    function close() {
        request.current++;
        setPath(null);
        setFile(null);
        setError(null);
        setCheckId(null);
    }

    async function save() {
        if (!file || !path) return;
        const content = text;
        setSaving(true);
        setError(null);
        try {
            const saved = await call<{ sha256: string; check_run_id: string | null }>(
                "file.write", { id, path, content, expected: file.sha256, message: note });
            setFile({ path, content, sha256: saved.sha256 });
            setNote("");
            setCheckId(saved.check_run_id);
            list.reload();
        } catch (ex) {
            setError(message(ex));
        }
        setSaving(false);
    }

    async function check() {
        if (!path) return;
        setError(null);
        try {
            setCheckId((await call<{ run_id: string }>("file.check", { id, path })).run_id);
        } catch (ex) {
            setError(message(ex));
        }
    }

    // Ansible's keywords, and the modules installed on the controller.
    const complete = useMemo(() => (path && editable && isYaml(path) ? ansibleSource(path) : undefined), [path, editable]);

    const tree = useMemo(() => {
        const files = list.data?.files ?? [];
        const open = new Set(path ? ancestors(path) : []);
        const item = (node: FileNode): TreeViewDataItem => node.children
            ? {
                id: node.path, name: node.name, icon: <FolderIcon />, expandedIcon: <FolderOpenIcon />,
                defaultExpanded: files.length <= OPEN_ALL || open.has(node.path), children: node.children.map(item),
            }
            : { id: node.path, name: node.name, icon: <FileIcon /> };
        return buildTree(files.map(entry => entry.path)).map(item);
    }, [list.data, path]);

    return (
        <>
            <SectionHeader title={project?.name ?? t("Files")}>
                {editable && <Button variant="primary" onClick={() => guard(() => setDialog("new"))}>{t("New file")}</Button>}
                {editable && <Button variant="secondary" onClick={() => setDialog("history")}>{t("All changes")}</Button>}
                {can("module.help") && <ModuleHelpButton />}
                <Button variant="secondary" onClick={() => guard(() => cockpit.location.go(["projects"]))}>{t("All projects")}</Button>
            </SectionHeader>
            {project && project.kind !== "managed" && (
                <Alert variant="info" isInline isPlain style={{ marginBottom: "1rem" }}
                    title={project.kind === "git"
                        ? t("These files come from a git repository: they are changed there, and fetched again with “Update from git”.")
                        : project.kind === "archive"
                            ? t("These files come from an uploaded archive: to change them, upload another one.")
                            : t("These files are a directory of this server: they are changed there.")} />
            )}
            <ListState loaded={list} isEmpty={data => data.files.length === 0}
                emptyTitle={t("No files")}
                emptyBody={editable ? t("Create the first file of this project: a playbook, for example site.yml.") : t("This project has no files.")}>
                {data => (
                    <div className="fleet-files">
                        <div className="fleet-files-tree">
                            {data.truncated && <Alert variant="warning" isInline isPlain title={t("Only the first files of this project are listed.")} />}
                            <TreeView aria-label={t("Files")} data={tree} hasGuides
                                activeItems={path ? [{ id: path, name: path }] : []}
                                onSelect={(_event, item) => {
                                    const next = item.id;
                                    if (!item.children && next && next !== path) guard(() => load(next));
                                }} />
                        </div>
                        <div>
                            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
                            {!path && <Content component="p">{t("Choose a file to see it.")}</Content>}
                            {path && (
                                <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
                                    <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} alignItems={{ default: "alignItemsCenter" }}>
                                        <FlexItem>
                                            <code>{path}</code>
                                            {dirty && <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }}>{t("Not saved")}</Label>}
                                        </FlexItem>
                                        <Flex gap={{ default: "gapSm" }}>
                                            {isYaml(path) && <Button variant="secondary" onClick={check} isDisabled={!file || dirty}>{t("Check syntax")}</Button>}
                                            {editable && <Button variant="secondary" onClick={() => setDialog("file-history")}>{t("Changes")}</Button>}
                                            {editable && <Button variant="secondary" onClick={() => setDialog("rename")} isDisabled={!file || dirty}>{t("Rename")}</Button>}
                                            {editable && <Button variant="secondary" isDanger onClick={() => setDialog("delete")}>{t("Delete")}</Button>}
                                        </Flex>
                                    </Flex>
                                    {file
                                        ? (
                                            <CodeEditor key={path} className="fleet-editor" label={path} path={path} value={text} onChange={setText}
                                                readOnly={!editable} complete={complete}
                                                onSave={() => { if (editable && dirty && !saving) save(); }} />
                                        )
                                        : !error && <Spinner aria-label={t("Loading")} />}
                                    {editable && file && (
                                        <Flex alignItems={{ default: "alignItemsCenter" }}>
                                            <FlexItem grow={{ default: "grow" }}>
                                                <TextInput aria-label={t("What changed")} placeholder={t("What changed (optional)")} value={note}
                                                    onChange={(_e, value) => setNote(value)} maxLength={80} />
                                            </FlexItem>
                                            <Button variant="primary" onClick={save} isLoading={saving} isDisabled={!dirty || saving}>{t("Save")}</Button>
                                        </Flex>
                                    )}
                                    {checkId && <CheckResult key={checkId} id={checkId} />}
                                </Flex>
                            )}
                        </div>
                    </div>
                )}
            </ListState>
            {dialog === "new" && (
                <PathModal title={t("New file")} submitLabel={t("Create")}
                    initial={path ? path.slice(0, path.lastIndexOf("/") + 1) : ""}
                    onSubmit={async next => {
                        await call("file.write", { id, path: next, content: "", expected: "" });
                        setDialog(null);
                        list.reload();
                        load(next);
                    }}
                    onClose={() => setDialog(null)} />
            )}
            {dialog === "rename" && path && (
                <PathModal title={t("Rename {{path}}", { path })} submitLabel={t("Rename")} initial={path}
                    onSubmit={async next => {
                        await call("file.move", { id, path, to: next });
                        setDialog(null);
                        setPath(next);
                        setFile(current => current && { ...current, path: next });
                        list.reload();
                    }}
                    onClose={() => setDialog(null)} />
            )}
            {dialog === "delete" && path && (
                <ConfirmDelete name={path}
                    what={t("The file is deleted from the project. What it held stays in the record of changes.")}
                    onDelete={async () => { await call("file.delete", { id, path }); setDialog(null); close(); list.reload(); }}
                    onClose={() => setDialog(null)} />
            )}
            {dialog === "history" && <HistoryModal id={id} onClose={() => setDialog(null)} />}
            {dialog === "file-history" && path && <HistoryModal id={id} path={path} onClose={() => setDialog(null)} />}
            {pending && (
                <FormModal title={t("Discard the changes?")} submitLabel={t("Discard")} isDanger variant={ModalVariant.small}
                    onSubmit={async () => { const action = pending; setPending(null); setText(file?.content ?? ""); action(); }}
                    onClose={() => setPending(null)}>
                    <Content component="p">{t("{{path}} has changes that were not saved.", { path })}</Content>
                </FormModal>
            )}
        </>
    );
}
