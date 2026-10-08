import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, Label, Radio, TextArea, TextInput } from "@patternfly/react-core";
import { ActionsColumn, ExpandableRowContent, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Credential, Inventory, Project } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";

const MONO = { fontFamily: "var(--pf-t--global--font--family--mono)" };

function InventoryModal({ inventory, onClose, onDone }: { inventory?: Inventory; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const projects = useLoad(() => call<{ projects: Project[] }>("project.list"));
    const credentials = useLoad(() => call<{ credentials: Credential[] }>("credential.list"));
    const [name, setName] = useState(inventory?.name ?? "");
    const [kind, setKind] = useState<"managed" | "file">(inventory?.kind ?? "managed");
    const [format, setFormat] = useState<"yaml" | "ini">(inventory?.format ?? "yaml");
    const [content, setContent] = useState(inventory?.content ?? "");
    const [projectId, setProjectId] = useState(inventory?.project_id ?? "");
    const [path, setPath] = useState(inventory?.path ?? "");
    const [vaultId, setVaultId] = useState(inventory?.vault_credential_id ?? "");
    const files = useLoad(() => projectId
        ? call<{ files: string[] }>("project.files", { id: projectId }).catch(() => ({ files: [] }))
        : Promise.resolve({ files: [] as string[] }));

    // After the render that has the new project, not in the change handler.
    useEffect(files.reload, [projectId]);

    async function submit() {
        const source = kind === "managed"
            ? { format, content }
            : { project_id: projectId, path: path.trim() };
        const params = { name, vault_credential_id: vaultId, ...source };
        const saved = inventory
            ? await call<Inventory>("inventory.update", { id: inventory.id, ...params })
            : await call<Inventory>("inventory.create", { kind, ...params });
        // Read it straight away, so that its hosts show up and mistakes are reported.
        await call("inventory.parse", { id: saved.id });
        onDone();
        onClose();
    }

    const vaults = (credentials.data?.credentials ?? []).filter(credential => credential.kind === "vault");
    return (
        <FormModal title={inventory ? t("Edit inventory") : t("New inventory")} submitLabel={t("Save")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || (kind === "file" && (!projectId || !path.trim()))}>
            <FormGroup label={t("Name")} isRequired fieldId="inv-name">
                <TextInput id="inv-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            {!inventory && (
                <FormGroup label={t("Source")} role="radiogroup" isInline fieldId="inv-kind">
                    <Radio id="inv-kind-managed" name="inv-kind" label={t("Written here")} isChecked={kind === "managed"} onChange={() => setKind("managed")} />
                    <Radio id="inv-kind-file" name="inv-kind" label={t("File of a project")} isChecked={kind === "file"} onChange={() => setKind("file")} />
                </FormGroup>
            )}
            {kind === "managed" ? (
                <>
                    <FormGroup label={t("Format")} role="radiogroup" isInline fieldId="inv-format">
                        <Radio id="inv-format-yaml" name="inv-format" label="YAML" isChecked={format === "yaml"} onChange={() => setFormat("yaml")} />
                        <Radio id="inv-format-ini" name="inv-format" label="INI" isChecked={format === "ini"} onChange={() => setFormat("ini")} />
                    </FormGroup>
                    <FormGroup label={t("Hosts and groups")} fieldId="inv-content">
                        <TextArea id="inv-content" value={content} onChange={(_e, value) => setContent(value)} rows={12}
                            resizeOrientation="vertical" spellCheck={false} style={MONO}
                            placeholder={format === "yaml" ? "all:\n  hosts:\n    web01:\n      ansible_host: 192.0.2.10" : "[web]\nweb01 ansible_host=192.0.2.10"} />
                        <FormHelperText><HelperText><HelperTextItem>{t("Do not write passwords here: use a credential.")}</HelperTextItem></HelperText></FormHelperText>
                    </FormGroup>
                </>
            ) : (
                <>
                    <FormGroup label={t("Project")} isRequired fieldId="inv-project">
                        <FormSelect id="inv-project" value={projectId} onChange={(_e, value) => { setProjectId(value); setPath(""); }}>
                            <FormSelectOption value="" label={t("Choose a project")} isPlaceholder />
                            {(projects.data?.projects ?? []).map(project => <FormSelectOption key={project.id} value={project.id} label={project.name} />)}
                        </FormSelect>
                    </FormGroup>
                    <FormGroup label={t("Inventory file")} isRequired fieldId="inv-path">
                        <TextInput id="inv-path" value={path} onChange={(_e, value) => setPath(value)} list="inv-path-files" placeholder="inventories/production/hosts.yaml" />
                        <datalist id="inv-path-files">
                            {(files.data?.files ?? []).map(file => <option key={file} value={file} />)}
                        </datalist>
                        <FormHelperText><HelperText><HelperTextItem>{t("Path inside the project. Its group_vars and host_vars are used too.")}</HelperTextItem></HelperText></FormHelperText>
                    </FormGroup>
                </>
            )}
            <FormGroup label={t("Vault credential")} fieldId="inv-vault">
                <FormSelect id="inv-vault" value={vaultId} onChange={(_e, value) => setVaultId(value)}>
                    <FormSelectOption value="" label={t("None")} />
                    {vaults.map(credential => <FormSelectOption key={credential.id} value={credential.id} label={credential.name} />)}
                </FormSelect>
                <FormHelperText><HelperText><HelperTextItem>{t("Needed when the inventory's variables are encrypted with Ansible Vault.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

function Hosts({ id }: { id: string }) {
    const { t } = useTranslation();
    const detail = useLoad(() => call<Inventory>("inventory.get", { id }));
    if (detail.error) return <Alert variant="danger" isInline title={detail.error.message} />;
    if (!detail.data) return null;
    if (detail.data.parse_error) {
        return (
            <Alert variant="danger" isInline title={t("Ansible could not read this inventory")}>
                <pre style={{ whiteSpace: "pre-wrap" }}>{detail.data.parse_error}</pre>
            </Alert>
        );
    }
    return (
        <Table variant="compact" borders={false} aria-label={t("Hosts")}>
            <Thead><Tr><Th>{t("Host")}</Th><Th>{t("Address")}</Th><Th>{t("Groups")}</Th></Tr></Thead>
            <Tbody>
                {(detail.data.hosts ?? []).map(host => (
                    <Tr key={host.name}>
                        <Td>{host.name}</Td>
                        <Td>{host.address}</Td>
                        <Td>{host.groups.map(group => <Label key={group} isCompact style={{ marginInlineEnd: "0.25rem" }}>{group}</Label>)}</Td>
                    </Tr>
                ))}
            </Tbody>
        </Table>
    );
}

export function InventoriesPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ inventories: Inventory[] }>("inventory.list"));
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<Inventory | null>(null);
    const [deleting, setDeleting] = useState<Inventory | null>(null);
    const [expanded, setExpanded] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Reading an inventory is a job: poll for a while after asking for one.
    const [waiting, setWaiting] = useState(0);
    usePolling(() => { setWaiting(count => count - 1); list.reload(); }, 2000, waiting > 0);

    async function act(action: () => Promise<unknown>) {
        setError(null);
        try {
            await action();
        } catch (ex) {
            setError(message(ex));
        }
    }

    const refresh = () => { list.reload(); setWaiting(8); };
    const read = (inventory: Inventory) => act(async () => { await call("inventory.parse", { id: inventory.id }); refresh(); });
    const edit = (inventory: Inventory) => act(async () => setEditing(await call<Inventory>("inventory.get", { id: inventory.id })));

    return (
        <>
            <SectionHeader title={t("Inventories")}>
                {can("inventory.create") && <Button variant="primary" onClick={() => setCreating(true)}>{t("New inventory")}</Button>}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.inventories.length === 0}
                emptyTitle={t("No inventories")}
                emptyBody={t("An inventory lists the nodes Ansible works on and their groups. It can be written here or be a file of a project.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Inventories")}>
                        <Thead>
                            <Tr><Th screenReaderText={t("Show hosts")} /><Th>{t("Name")}</Th><Th>{t("Source")}</Th><Th>{t("Hosts")}</Th><Th>{t("Read")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                        </Thead>
                        {data.inventories.map((inventory, index) => (
                            <Tbody key={inventory.id} isExpanded={expanded === inventory.id}>
                                <Tr>
                                    <Td expand={{ rowIndex: index, isExpanded: expanded === inventory.id, onToggle: () => setExpanded(expanded === inventory.id ? null : inventory.id) }} />
                                    <Td dataLabel={t("Name")}>{inventory.name}</Td>
                                    <Td dataLabel={t("Source")}>{inventory.kind === "managed" ? t("Written here") : inventory.path}</Td>
                                    <Td dataLabel={t("Hosts")}>
                                        {inventory.parse_error ? t("Not readable") : inventory.parsed_at ? inventory.host_count : t("Not read yet")}
                                    </Td>
                                    <Td dataLabel={t("Read")}>{formatTime(inventory.parsed_at, i18n.language)}</Td>
                                    <Td isActionCell>
                                        <ActionsColumn items={[
                                            ...(can("inventory.parse") ? [{ title: t("Read again"), onClick: () => read(inventory) }] : []),
                                            ...(can("inventory.update") ? [{ title: t("Edit"), onClick: () => edit(inventory) }] : []),
                                            ...(can("inventory.delete") ? [{ title: t("Delete"), onClick: () => setDeleting(inventory) }] : []),
                                        ]} />
                                    </Td>
                                </Tr>
                                <Tr isExpanded={expanded === inventory.id}>
                                    <Td colSpan={6}>
                                        <ExpandableRowContent>
                                            {expanded === inventory.id && <Hosts key={`${inventory.id}-${inventory.parsed_at}`} id={inventory.id} />}
                                        </ExpandableRowContent>
                                    </Td>
                                </Tr>
                            </Tbody>
                        ))}
                    </Table>
                )}
            </ListState>
            {creating && <InventoryModal onClose={() => setCreating(false)} onDone={refresh} />}
            {editing && <InventoryModal inventory={editing} onClose={() => setEditing(null)} onDone={refresh} />}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The inventory is deleted. Files of a project are not touched.")}
                    onDelete={async () => { await call("inventory.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
