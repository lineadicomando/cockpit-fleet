import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Checkbox, FormGroup, FormHelperText, FormSelect, FormSelectOption, Grid, GridItem, HelperText, HelperTextItem, TextInput } from "@patternfly/react-core";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import type { Credential, Inventory, Project, Run } from "../lib/types.ts";
import { FormModal } from "./common.tsx";
import { ModuleHelpButton } from "./ModuleHelp.tsx";
import { Alert, Spinner } from "./pf.tsx";

interface Choices {
    modules: string[];
    projects: Project[];
    inventories: Inventory[];
    credentials: Credential[];
}

function loadChoices(): Promise<Choices> {
    return Promise.all([
        call<{ values: { adhoc_modules: string[] } }>("config.show"),
        call<{ projects: Project[] }>("project.list"),
        call<{ inventories: Inventory[] }>("inventory.list"),
        call<{ credentials: Credential[] }>("credential.list"),
    ]).then(([config, p, i, c]) => ({
        modules: config.values.adhoc_modules, projects: p.projects, inventories: i.inventories, credentials: c.credentials,
    }));
}

// One module on some hosts of an inventory, without a template.
export function AdhocModal({ onClose }: { onClose: () => void }) {
    const { t } = useTranslation();
    const choices = useLoad(loadChoices);
    const [inventoryId, setInventoryId] = useState("");
    const [projectId, setProjectId] = useState("");
    const [limit, setLimit] = useState("");
    const [module, setModule] = useState("");
    const [freeForm, setFreeForm] = useState("");
    const [machineId, setMachineId] = useState("");
    const [vaultIds, setVaultIds] = useState<string[]>([]);
    const [become, setBecome] = useState(false);
    const [check, setCheck] = useState(false);
    const [serial, setSerial] = useState(false);

    const data = choices.data;
    const inventory = data?.inventories.find(item => item.id === inventoryId);
    // With no project, a file inventory brings its own.
    const ownProject = inventory?.kind === "file" ? inventory.project_id ?? "" : null;
    const chosenModule = module || data?.modules[0] || "";
    const ofKind = (kind: string) => (data?.credentials ?? []).filter(credential => credential.kind === kind);

    async function submit() {
        const run = await call<Run>("run.adhoc", {
            inventory_id: inventoryId, project_id: projectId, limit: limit.trim(),
            module: chosenModule, free_form: freeForm.trim(),
            machine_credential_id: machineId, vault_credential_ids: vaultIds, become, check, serial,
        });
        cockpit.location.go(["runs", run.id]);
    }

    return (
        <FormModal title={t("Run a command")} submitLabel={check ? t("Launch dry run") : t("Launch")} onSubmit={submit} onClose={onClose}
            isDisabled={!inventoryId || !limit.trim() || !chosenModule}>
            {choices.error && <Alert variant="danger" isInline title={choices.error.message} />}
            {!data && !choices.error && <Spinner aria-label={t("Loading")} />}
            {data && data.modules.length === 0 && (
                <Alert variant="info" isInline title={t("Ad-hoc commands are disabled: the configuration file allows no module.")} />
            )}
            {data && data.modules.length > 0 && (
                <>
                    <Grid hasGutter md={6}>
                        <GridItem>
                            <FormGroup label={t("Inventory")} isRequired fieldId="adhoc-inventory">
                                <FormSelect id="adhoc-inventory" value={inventoryId} onChange={(_e, value) => setInventoryId(value)}>
                                    <FormSelectOption value="" label={t("Choose an inventory")} isPlaceholder />
                                    {data.inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                                </FormSelect>
                            </FormGroup>
                        </GridItem>
                        <GridItem>
                            <FormGroup label={t("Hosts")} isRequired fieldId="adhoc-limit">
                                <TextInput id="adhoc-limit" value={limit} onChange={(_e, value) => setLimit(value)} placeholder="web*:!web03" isRequired />
                                <FormHelperText><HelperText><HelperTextItem>{t("A host pattern; \"all\" for every host of the inventory.")}</HelperTextItem></HelperText></FormHelperText>
                            </FormGroup>
                        </GridItem>
                        <GridItem>
                            <FormGroup label={t("Module")} isRequired fieldId="adhoc-module">
                                <FormSelect id="adhoc-module" value={chosenModule} onChange={(_e, value) => setModule(value)}>
                                    {data.modules.map(name => <FormSelectOption key={name} value={name} label={name} />)}
                                </FormSelect>
                                <FormHelperText><HelperText><HelperTextItem>{t("The modules allowed are listed in the configuration file.")} <ModuleHelpButton name={chosenModule} /></HelperTextItem></HelperText></FormHelperText>
                            </FormGroup>
                        </GridItem>
                        <GridItem>
                            <FormGroup label={t("Project")} fieldId="adhoc-project">
                                <FormSelect id="adhoc-project" value={projectId} onChange={(_e, value) => setProjectId(value)}>
                                    <FormSelectOption value="" label={ownProject !== null ? t("That of the inventory") : t("None")} />
                                    {data.projects.map(project => <FormSelectOption key={project.id} value={project.id} label={project.name} />)}
                                </FormSelect>
                                <FormHelperText><HelperText><HelperTextItem>{t("Only needed for a module of a collection the project brings.")}</HelperTextItem></HelperText></FormHelperText>
                            </FormGroup>
                        </GridItem>
                    </Grid>
                    <FormGroup label={t("Arguments")} fieldId="adhoc-args">
                        <TextInput id="adhoc-args" value={freeForm} onChange={(_e, value) => setFreeForm(value)} placeholder="uptime" />
                        <FormHelperText><HelperText><HelperTextItem>{t("The command line for a command or shell module, key=value pairs for the others. Not for secrets: they would be stored in clear.")}</HelperTextItem></HelperText></FormHelperText>
                    </FormGroup>
                    <Grid hasGutter md={6}>
                        <GridItem>
                            <FormGroup label={t("Machine credential")} fieldId="adhoc-machine">
                                <FormSelect id="adhoc-machine" value={machineId} onChange={(_e, value) => setMachineId(value)}>
                                    <FormSelectOption value="" label={t("None")} />
                                    {ofKind("machine").map(credential => <FormSelectOption key={credential.id} value={credential.id} label={credential.name} />)}
                                </FormSelect>
                            </FormGroup>
                        </GridItem>
                        <GridItem>
                            <FormGroup label={t("Vault credentials")} role="group" fieldId="adhoc-vault">
                                {ofKind("vault").length === 0 && <span>{t("None defined")}</span>}
                                {ofKind("vault").map(credential => (
                                    <Checkbox key={credential.id} id={`adhoc-vault-${credential.id}`}
                                        label={credential.vault_id ? `${credential.name} (${credential.vault_id})` : credential.name}
                                        isChecked={vaultIds.includes(credential.id)}
                                        onChange={(_e, checked) => setVaultIds(current => checked
                                            ? [...current, credential.id]
                                            : current.filter(id => id !== credential.id))} />
                                ))}
                            </FormGroup>
                        </GridItem>
                    </Grid>
                    <FormGroup fieldId="adhoc-become">
                        <Checkbox id="adhoc-become" label={t("Run with privilege escalation (become)")} isChecked={become} onChange={(_e, value) => setBecome(value)} />
                    </FormGroup>
                    <FormGroup fieldId="adhoc-check">
                        <Checkbox id="adhoc-check" label={t("Dry run: show what would change, change nothing")} isChecked={check} onChange={(_e, value) => setCheck(value)} />
                    </FormGroup>
                    <FormGroup fieldId="adhoc-serial">
                        <Checkbox id="adhoc-serial" label={t("Start after the runs already launched on this inventory")} isChecked={serial} onChange={(_e, value) => setSerial(value)}
                            description={t("It waits in the queue while another run is running on the inventory, or one launched before it is queued there, whatever their outcome.")} />
                    </FormGroup>
                </>
            )}
        </FormModal>
    );
}
