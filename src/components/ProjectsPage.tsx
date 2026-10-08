import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, Radio, TextInput } from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Credential, Project } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";

interface ProjectList {
    projects: Project[];
    local_roots: string[];
}

function CreateModal({ roots, onClose, onDone }: { roots: string[]; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const credentials = useLoad(() => call<{ credentials: Credential[] }>("credential.list"));
    const [name, setName] = useState("");
    const [kind, setKind] = useState<"git" | "local">("git");
    const [url, setUrl] = useState("");
    const [ref, setRef] = useState("main");
    const [credentialId, setCredentialId] = useState("");
    const [path, setPath] = useState("");

    async function submit() {
        const params = kind === "git"
            ? { name, kind, url: url.trim(), ref: ref.trim() || "main", credential_id: credentialId }
            : { name, kind, path: path.trim() };
        const project = await call<Project>("project.create", params);
        // A git project is of no use until its files are here.
        if (kind === "git") await call("project.sync", { id: project.id });
        onDone();
        onClose();
    }

    const source = (credentials.data?.credentials ?? []).filter(credential => credential.kind === "source");
    return (
        <FormModal title={t("New project")} submitLabel={t("Create")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || (kind === "git" ? !url.trim() : !path.trim())}>
            <FormGroup label={t("Name")} isRequired fieldId="proj-name">
                <TextInput id="proj-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("Source")} role="radiogroup" isInline fieldId="proj-kind">
                <Radio id="proj-kind-git" name="proj-kind" label={t("Git repository")} isChecked={kind === "git"} onChange={() => setKind("git")} />
                <Radio id="proj-kind-local" name="proj-kind" label={t("Directory on this server")} isChecked={kind === "local"} onChange={() => setKind("local")} />
            </FormGroup>
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
    const [deleting, setDeleting] = useState<Project | null>(null);
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
                emptyBody={t("A project is where playbooks, roles and inventory files come from: a git repository or a directory of this server.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Projects")}>
                        <Thead>
                            <Tr><Th>{t("Name")}</Th><Th>{t("Source")}</Th><Th>{t("Revision")}</Th><Th>{t("Updated")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                        </Thead>
                        <Tbody>
                            {data.projects.map(project => (
                                <Tr key={project.id}>
                                    <Td dataLabel={t("Name")}>{project.name}</Td>
                                    <Td dataLabel={t("Source")}>
                                        {project.kind === "git" ? `${project.url} (${project.ref})` : project.path}
                                    </Td>
                                    <Td dataLabel={t("Revision")}>
                                        {project.sync_error
                                            ? <span title={project.sync_error}>{t("Last update failed")}</span>
                                            : project.kind === "git"
                                                ? (project.revision ? <code>{project.revision.slice(0, 10)}</code> : t("Not fetched yet"))
                                                : t("Read at each run")}
                                    </Td>
                                    <Td dataLabel={t("Updated")}>{formatTime(project.synced_at, i18n.language)}</Td>
                                    <Td isActionCell>
                                        {can("project.delete") && (
                                            <ActionsColumn items={[
                                                ...(project.kind === "git" ? [{ title: t("Update from git"), onClick: () => sync(project) }] : []),
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
            {creating && <CreateModal roots={list.data?.local_roots ?? []} onClose={() => setCreating(false)} onDone={() => { list.reload(); setWaiting(15); }} />}
            {deleting && (
                <ConfirmDelete name={deleting.name}
                    what={deleting.kind === "git"
                        ? t("The project and the files fetched for it are deleted. The repository is not touched.")
                        : t("The project is forgotten. The directory and its files are not touched.")}
                    onDelete={async () => { await call("project.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
