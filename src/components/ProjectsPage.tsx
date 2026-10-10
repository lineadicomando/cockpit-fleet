import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Content, FileUpload, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, ModalVariant, Radio, TextInput } from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Credential, Project } from "../lib/types.ts";
import { uploadArchive } from "../lib/upload.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";

interface ProjectList {
    projects: Project[];
    local_roots: string[];
    max_upload_mb: number;
}

// Takes the archive that becomes the files of a project, all of them.
function UploadModal({ project, maxMb, onClose }: { project: Project; maxMb: number; onClose: () => void }) {
    const { t } = useTranslation();
    const [file, setFile] = useState<File | null>(null);
    const tooLarge = !!file && file.size > maxMb * 1024 * 1024;

    async function submit() {
        if (!file) return;
        const { run_id } = await uploadArchive(project.id, file);
        onClose();
        cockpit.location.go(["runs", run_id]);
    }

    return (
        <FormModal title={t("Upload an archive for {{name}}", { name: project.name })} submitLabel={t("Upload")} onSubmit={submit} onClose={onClose}
            isDisabled={!file || file.size === 0 || tooLarge} variant={ModalVariant.small}>
            <Content component="p">
                {project.revision
                    ? t("The files of the archive replace the ones this project has now, all of them.")
                    : t("The files of the archive become the files of this project.")}
            </Content>
            <FormGroup label={t("Archive")} isRequired fieldId="proj-archive">
                <FileUpload id="proj-archive" filename={file?.name ?? ""} filenamePlaceholder={t("Choose a file")} browseButtonText={t("Browse…")} clearButtonText={t("Clear")}
                    hideDefaultPreview onFileInputChange={(_e, chosen) => setFile(chosen)} onClearClick={() => setFile(null)}
                    validated={tooLarge ? "error" : "default"} />
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem variant={tooLarge ? "error" : "default"}>
                            {t("A tar archive, compressed or not, or a zip, of at most {{size}} MB.", { size: maxMb })}
                        </HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

// Creates a project, or edits one. Where its files come from cannot change.
function ProjectModal({ project, roots, onClose, onDone, onUpload }: { project?: Project; roots: string[]; onClose: () => void; onDone: () => void; onUpload: (project: Project) => void }) {
    const { t } = useTranslation();
    const credentials = useLoad(() => call<{ credentials: Credential[] }>("credential.list"));
    const [name, setName] = useState(project?.name ?? "");
    const [kind, setKind] = useState<Project["kind"]>(project?.kind ?? "git");
    const [url, setUrl] = useState(project?.url ?? "");
    const [ref, setRef] = useState(project?.ref || "main");
    const [credentialId, setCredentialId] = useState(project?.credential_id ?? "");
    const [path, setPath] = useState(project?.path ?? "");

    async function submit() {
        const params = kind === "git"
            ? { name, url: url.trim(), ref: ref.trim() || "main", credential_id: credentialId }
            : kind === "local" ? { name, path: path.trim() } : { name };
        const saved = project
            ? await call<Project>("project.update", { id: project.id, ...params })
            : await call<Project>("project.create", { kind, ...params });
        // A git project is of no use until its files are here, and a changed
        // URL or reference means different files.
        if (kind === "git" && (!project || project.url !== saved.url || project.ref !== saved.ref)) {
            await call("project.sync", { id: saved.id });
        }
        onDone();
        onClose();
        // An empty project: the next thing to do is to write its first file.
        if (kind === "managed" && !project) cockpit.location.go(["projects", saved.id]);
        // Likewise empty: the next thing to do is to give it its archive.
        if (kind === "archive" && !project) onUpload(saved);
    }

    const source = (credentials.data?.credentials ?? []).filter(credential => credential.kind === "source");
    return (
        <FormModal title={project ? t("Edit project") : t("New project")} submitLabel={project ? t("Save") : t("Create")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || (kind === "git" && !url.trim()) || (kind === "local" && !path.trim())}>
            <FormGroup label={t("Name")} isRequired fieldId="proj-name">
                <TextInput id="proj-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            {!project && (
                <FormGroup label={t("Source")} role="radiogroup" isInline fieldId="proj-kind">
                    <Radio id="proj-kind-git" name="proj-kind" label={t("Git repository")} isChecked={kind === "git"} onChange={() => setKind("git")} />
                    <Radio id="proj-kind-local" name="proj-kind" label={t("Directory on this server")} isChecked={kind === "local"} onChange={() => setKind("local")} />
                    <Radio id="proj-kind-managed" name="proj-kind" label={t("Written here")} isChecked={kind === "managed"} onChange={() => setKind("managed")} />
                    <Radio id="proj-kind-archive" name="proj-kind" label={t("Uploaded archive")} isChecked={kind === "archive"} onChange={() => setKind("archive")} />
                </FormGroup>
            )}
            {kind === "git" ? (
                <>
                    <FormGroup label={t("Repository URL")} isRequired fieldId="proj-url">
                        <TextInput id="proj-url" value={url} onChange={(_e, value) => setUrl(value)} placeholder="https://example.org/team/playbooks.git" />
                        <FormHelperText><HelperText><HelperTextItem>{t("https:// or ssh. For ssh, trust the server's host key first.")}</HelperTextItem></HelperText></FormHelperText>
                    </FormGroup>
                    <FormGroup label={t("Branch, tag or commit")} fieldId="proj-ref">
                        <TextInput id="proj-ref" value={ref} onChange={(_e, value) => setRef(value)} />
                    </FormGroup>
                    <FormGroup label={t("Credential")} fieldId="proj-cred">
                        <FormSelect id="proj-cred" value={credentialId} onChange={(_e, value) => setCredentialId(value)}>
                            <FormSelectOption value="" label={t("None (public repository)")} />
                            {source.map(credential => <FormSelectOption key={credential.id} value={credential.id} label={credential.name} />)}
                        </FormSelect>
                    </FormGroup>
                </>
            ) : kind === "managed" ? (
                !project && <Content component="p">{t("The files are written in this interface. Every change is recorded with its author, and can be looked up later.")}</Content>
            ) : kind === "archive" ? (
                !project && <Content component="p">{t("The files come from an archive chosen in the next step. They are not changed here: a new archive replaces them all.")}</Content>
            ) : (
                <FormGroup label={t("Path")} isRequired fieldId="proj-path">
                    <TextInput id="proj-path" value={path} onChange={(_e, value) => setPath(value)} placeholder={roots[0] ? `${roots[0]}/…` : ""} />
                    <FormHelperText>
                        <HelperText>
                            <HelperTextItem variant={roots.length ? "default" : "warning"}>
                                {roots.length
                                    ? t("Must be under: {{roots}}", { roots: roots.join(", ") })
                                    : t("No directory is allowed yet: add one to local_roots in /etc/cockpit-fleet/config.toml.")}
                            </HelperTextItem>
                        </HelperText>
                    </FormHelperText>
                </FormGroup>
            )}
        </FormModal>
    );
}

export function ProjectsPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<ProjectList>("project.list"));
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<Project | null>(null);
    const [deleting, setDeleting] = useState<Project | null>(null);
    const [uploading, setUploading] = useState<Project | null>(null);
    const [error, setError] = useState<string | null>(null);
    // A new git project is fetched by a job: poll for a while to show its revision.
    const [waiting, setWaiting] = useState(0);
    usePolling(() => { setWaiting(count => count - 1); list.reload(); }, 2000, waiting > 0);

    async function sync(project: Project) {
        setError(null);
        try {
            const { run_id } = await call<{ run_id: string }>("project.sync", { id: project.id });
            cockpit.location.go(["runs", run_id]);
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            <SectionHeader title={t("Projects")}>
                {can("project.create") && <Button variant="primary" onClick={() => setCreating(true)}>{t("New project")}</Button>}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.projects.length === 0}
                emptyTitle={t("No projects")}
                emptyBody={t("A project is where playbooks, roles and inventory files come from: a git repository, a directory of this server, files written here or an uploaded archive.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Projects")}>
                        <Thead>
                            <Tr><Th>{t("Name")}</Th><Th>{t("Source")}</Th><Th>{t("Revision")}</Th><Th>{t("Updated")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                        </Thead>
                        <Tbody>
                            {data.projects.map(project => (
                                <Tr key={project.id}>
                                    <Td dataLabel={t("Name")}>
                                        {can("file.list")
                                            ? <Button variant="link" isInline onClick={() => cockpit.location.go(["projects", project.id])}>{project.name}</Button>
                                            : project.name}
                                    </Td>
                                    <Td dataLabel={t("Source")}>
                                        {project.kind === "git" ? `${project.url} (${project.ref})` : project.kind === "local" ? project.path : project.kind === "archive" ? t("Uploaded archive") : t("Written here")}
                                    </Td>
                                    <Td dataLabel={t("Revision")}>
                                        {project.sync_error
                                            ? <span title={project.sync_error}>{t("Last update failed")}</span>
                                            : project.kind === "local"
                                                ? t("Read at each run")
                                                : project.revision
                                                    ? <code>{project.revision.slice(0, 10)}</code>
                                                    : project.kind === "git" ? t("Not fetched yet") : project.kind === "archive" ? t("No archive yet") : t("No changes yet")}
                                    </Td>
                                    <Td dataLabel={t("Updated")}>{formatTime(project.synced_at, i18n.language)}</Td>
                                    <Td isActionCell>
                                        {can("project.delete") && (
                                            <ActionsColumn items={[
                                                ...(project.kind === "git" ? [{ title: t("Update from git"), onClick: () => sync(project) }] : []),
                                                ...(project.kind === "archive" ? [{ title: t("Upload an archive"), onClick: () => setUploading(project) }] : []),
                                                { title: t("Files"), onClick: () => cockpit.location.go(["projects", project.id]) },
                                                { title: t("Edit"), onClick: () => setEditing(project) },
                                                { title: t("Delete"), onClick: () => setDeleting(project) },
                                            ]} />
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {creating && <ProjectModal roots={list.data?.local_roots ?? []} onClose={() => setCreating(false)} onDone={() => { list.reload(); setWaiting(15); }} onUpload={setUploading} />}
            {editing && <ProjectModal project={editing} roots={list.data?.local_roots ?? []} onClose={() => setEditing(null)} onDone={() => { list.reload(); setWaiting(15); }} onUpload={setUploading} />}
            {uploading && <UploadModal project={uploading} maxMb={list.data?.max_upload_mb ?? 20} onClose={() => setUploading(null)} />}
            {deleting && (
                <ConfirmDelete name={deleting.name}
                    what={deleting.kind === "git"
                        ? t("The project and the files fetched for it are deleted. The repository is not touched.")
                        : deleting.kind === "local"
                            ? t("The project is forgotten. The directory and its files are not touched.")
                            : deleting.kind === "archive"
                                ? t("The project and the files unpacked for it are deleted.")
                                : t("The project, its files and the record of their changes are deleted.")}
                    onDelete={async () => { await call("project.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
