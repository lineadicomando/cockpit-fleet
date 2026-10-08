import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Checkbox, FormGroup, FormHelperText, FormSelect, FormSelectOption, Grid, GridItem, HelperText, HelperTextItem, TextArea, TextInput } from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Credential, Inventory, Project, Run, Template } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader } from "./common.tsx";

const MONO = { fontFamily: "var(--pf-t--global--font--family--mono)" };
const words = (text: string) => text.split(/[\s,]+/).filter(Boolean);

interface Choices {
    projects: Project[];
    inventories: Inventory[];
    credentials: Credential[];
}

function loadChoices(): Promise<Choices> {
    return Promise.all([
        call<{ projects: Project[] }>("project.list"),
        call<{ inventories: Inventory[] }>("inventory.list"),
        call<{ credentials: Credential[] }>("credential.list"),
    ]).then(([p, i, c]) => ({ projects: p.projects, inventories: i.inventories, credentials: c.credentials }));
}

function TemplateModal({ template, choices, onClose, onDone }: { template?: Template; choices: Choices; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState(template?.name ?? "");
    const [projectId, setProjectId] = useState(template?.project_id ?? "");
    const [playbook, setPlaybook] = useState(template?.playbook ?? "");
    const [inventoryId, setInventoryId] = useState(template?.inventory_id ?? "");
    const [machineId, setMachineId] = useState(template?.machine_credential_id ?? "");
    const [vaultId, setVaultId] = useState(template?.vault_credential_id ?? "");
    const [limit, setLimit] = useState(template?.limit_hosts ?? "");
    const [tags, setTags] = useState((template?.tags ?? []).join(", "));
    const [skipTags, setSkipTags] = useState((template?.skip_tags ?? []).join(", "));
    const [extraVars, setExtraVars] = useState(template && Object.keys(template.extra_vars).length ? JSON.stringify(template.extra_vars, null, 2) : "");
    const [forks, setForks] = useState(template?.forks ? String(template.forks) : "");
    const [verbosity, setVerbosity] = useState(String(template?.verbosity ?? 0));
    const [become, setBecome] = useState(template?.become ?? false);
    const files = useLoad(() => projectId
        ? call<{ files: string[] }>("project.files", { id: projectId }).catch(() => ({ files: [] }))
        : Promise.resolve({ files: [] as string[] }));

    // After the render that has the new project, not in the change handler.
    useEffect(files.reload, [projectId]);

    async function submit() {
        let vars: unknown = {};
        if (extraVars.trim()) {
            try {
                vars = JSON.parse(extraVars);
            } catch {
                throw new Error(t("Extra variables must be a JSON object, for example {\"version\": \"1.2\"}."));
            }
        }
        const params = {
            name, project_id: projectId, playbook: playbook.trim(), inventory_id: inventoryId,
            machine_credential_id: machineId, vault_credential_id: vaultId,
            limit_hosts: limit.trim(), tags: words(tags), skip_tags: words(skipTags), extra_vars: vars,
            ...(forks ? { forks: Number(forks) } : {}), verbosity: Number(verbosity), become,
        };
        await call(template ? "template.update" : "template.create", template ? { id: template.id, ...params } : params);
        onDone();
        onClose();
    }

    // A file inventory belongs to one project; one written here fits any.
    const inventories = choices.inventories.filter(inventory => inventory.kind === "managed" || inventory.project_id === projectId);
    const ofKind = (kind: string) => choices.credentials.filter(credential => credential.kind === kind);

    return (
        <FormModal title={template ? t("Edit job template") : t("New job template")} submitLabel={t("Save")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || !projectId || !playbook.trim() || !inventoryId}>
            <FormGroup label={t("Name")} isRequired fieldId="tpl-name">
                <TextInput id="tpl-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("Project")} isRequired fieldId="tpl-project">
                        <FormSelect id="tpl-project" value={projectId} onChange={(_e, value) => { setProjectId(value); setInventoryId(""); }}>
                            <FormSelectOption value="" label={t("Choose a project")} isPlaceholder />
                            {choices.projects.map(project => <FormSelectOption key={project.id} value={project.id} label={project.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Playbook")} isRequired fieldId="tpl-playbook">
                        <TextInput id="tpl-playbook" value={playbook} onChange={(_e, value) => setPlaybook(value)} list="tpl-playbook-files" placeholder="playbooks/site.yaml" />
                        <datalist id="tpl-playbook-files">
                            {(files.data?.files ?? []).map(file => <option key={file} value={file} />)}
                        </datalist>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Inventory")} isRequired fieldId="tpl-inventory">
                        <FormSelect id="tpl-inventory" value={inventoryId} onChange={(_e, value) => setInventoryId(value)}>
                            <FormSelectOption value="" label={t("Choose an inventory")} isPlaceholder />
                            {inventories.map(inventory => <FormSelectOption key={inventory.id} value={inventory.id} label={inventory.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Limit to hosts")} fieldId="tpl-limit">
                        <TextInput id="tpl-limit" value={limit} onChange={(_e, value) => setLimit(value)} placeholder="web*:!web03" />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Machine credential")} fieldId="tpl-machine">
                        <FormSelect id="tpl-machine" value={machineId} onChange={(_e, value) => setMachineId(value)}>
                            <FormSelectOption value="" label={t("None")} />
                            {ofKind("machine").map(credential => <FormSelectOption key={credential.id} value={credential.id} label={credential.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Vault credential")} fieldId="tpl-vault">
                        <FormSelect id="tpl-vault" value={vaultId} onChange={(_e, value) => setVaultId(value)}>
                            <FormSelectOption value="" label={t("None")} />
                            {ofKind("vault").map(credential => <FormSelectOption key={credential.id} value={credential.id} label={credential.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Tags")} fieldId="tpl-tags">
                        <TextInput id="tpl-tags" value={tags} onChange={(_e, value) => setTags(value)} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Skip tags")} fieldId="tpl-skip">
                        <TextInput id="tpl-skip" value={skipTags} onChange={(_e, value) => setSkipTags(value)} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Hosts in parallel (forks)")} fieldId="tpl-forks">
                        <TextInput id="tpl-forks" type="number" min={1} value={forks} onChange={(_e, value) => setForks(value)} placeholder={t("Default")} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Output detail")} fieldId="tpl-verbosity">
                        <FormSelect id="tpl-verbosity" value={verbosity} onChange={(_e, value) => setVerbosity(value)}>
                            <FormSelectOption value="0" label={t("Normal")} />
                            <FormSelectOption value="1" label="-v" />
                            <FormSelectOption value="2" label="-vv" />
                            <FormSelectOption value="3" label="-vvv" />
                        </FormSelect>
                    </FormGroup>
                </GridItem>
            </Grid>
            <FormGroup fieldId="tpl-become">
                <Checkbox id="tpl-become" label={t("Run with privilege escalation (become)")} isChecked={become} onChange={(_e, value) => setBecome(value)} />
            </FormGroup>
            <FormGroup label={t("Extra variables")} fieldId="tpl-vars">
                <TextArea id="tpl-vars" value={extraVars} onChange={(_e, value) => setExtraVars(value)} rows={4} resizeOrientation="vertical" spellCheck={false} style={MONO} placeholder={'{\n  "version": "1.2"\n}'} />
                <FormHelperText><HelperText><HelperTextItem>{t("A JSON object. Not for secrets: they would be stored in clear.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

function LaunchModal({ template, onClose }: { template: Template; onClose: () => void }) {
    const { t } = useTranslation();
    const [limit, setLimit] = useState("");
    const [check, setCheck] = useState(false);

    async function submit() {
        const run = await call<Run>("run.start", { template_id: template.id, limit: limit.trim(), check });
        cockpit.location.go(["runs", run.id]);
    }

    return (
        <FormModal title={t("Launch {{name}}", { name: template.name })} submitLabel={check ? t("Launch dry run") : t("Launch")} onSubmit={submit} onClose={onClose}>
            <FormGroup label={t("Limit to hosts")} fieldId="launch-limit">
                <TextInput id="launch-limit" value={limit} onChange={(_e, value) => setLimit(value)} placeholder={template.limit_hosts || "web01,web02"} />
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem>
                            {template.limit_hosts
                                ? t("Narrows the template's own limit ({{limit}}); it cannot widen it.", { limit: template.limit_hosts })
                                : t("Empty means every host the playbook targets.")}
                        </HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
            <FormGroup fieldId="launch-check">
                <Checkbox id="launch-check" label={t("Dry run: show what would change, change nothing")} isChecked={check} onChange={(_e, value) => setCheck(value)} />
            </FormGroup>
        </FormModal>
    );
}

export function TemplatesPage() {
    const { t } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ templates: Template[] }>("template.list"));
    const choices = useLoad(loadChoices);
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<Template | null>(null);
    const [launching, setLaunching] = useState<Template | null>(null);
    const [deleting, setDeleting] = useState<Template | null>(null);

    const nameOf = (items: { id: string; name: string }[] | undefined, id: string) =>
        items?.find(item => item.id === id)?.name ?? "";

    return (
        <>
            <SectionHeader title={t("Job templates")}>
                {can("template.create") && (
                    <Button variant="primary" onClick={() => setCreating(true)} isDisabled={!choices.data}>{t("New job template")}</Button>
                )}
            </SectionHeader>
            <ListState loaded={list} isEmpty={data => data.templates.length === 0}
                emptyTitle={t("No job templates")}
                emptyBody={t("A job template puts together a playbook, an inventory and the credentials to run it. Once defined, it is launched with one click.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Job templates")}>
                        <Thead>
                            <Tr><Th>{t("Name")}</Th><Th>{t("Project")}</Th><Th>{t("Playbook")}</Th><Th>{t("Inventory")}</Th><Th screenReaderText={t("Launch")} /><Th screenReaderText={t("Actions")} /></Tr>
                        </Thead>
                        <Tbody>
                            {data.templates.map(template => (
                                <Tr key={template.id}>
                                    <Td dataLabel={t("Name")}>{template.name}</Td>
                                    <Td dataLabel={t("Project")}>{nameOf(choices.data?.projects, template.project_id)}</Td>
                                    <Td dataLabel={t("Playbook")}><code>{template.playbook}</code></Td>
                                    <Td dataLabel={t("Inventory")}>{nameOf(choices.data?.inventories, template.inventory_id)}</Td>
                                    <Td isActionCell style={{ whiteSpace: "nowrap" }}>
                                        {can("run.start") && <Button variant="secondary" size="sm" onClick={() => setLaunching(template)}>{t("Launch")}</Button>}
                                    </Td>
                                    <Td isActionCell>
                                        {can("template.update") && (
                                            <ActionsColumn items={[
                                                { title: t("Edit"), onClick: () => setEditing(template) },
                                                { title: t("Delete"), onClick: () => setDeleting(template) },
                                            ]} />
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {creating && choices.data && <TemplateModal choices={choices.data} onClose={() => setCreating(false)} onDone={list.reload} />}
            {editing && choices.data && <TemplateModal template={editing} choices={choices.data} onClose={() => setEditing(null)} onDone={list.reload} />}
            {launching && <LaunchModal template={launching} onClose={() => setLaunching(null)} />}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The job template is deleted. Its past runs stay in the history.")}
                    onDelete={async () => { await call("template.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
