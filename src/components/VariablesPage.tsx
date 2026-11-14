import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button, Content, ExpandableSection, Flex, FlexItem, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem,
    Label, TextInput,
} from "@patternfly/react-core";
import { LockIcon } from "@patternfly/react-icons";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call, deleteSecret, setSecret } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { VariableSecret, VariableSet } from "../lib/types.ts";
import { effectiveVariables, moved, parseVariables } from "../lib/variables.ts";
import { CodeEditor } from "./CodeEditor.tsx";
import { ConfirmDelete, FormModal, ListState, SectionHeader } from "./common.tsx";
import { ActionsColumn } from "./pf.tsx";

const shown = (value: unknown) => JSON.stringify(value);

// A secret variable in the form: one of the set's, or one being added. Its
// value is typed here and handed to fleet-secret, never shown again.
interface SecretRow {
    key: number;
    name: string;
    saved?: VariableSecret;
    value: string;
}

const usedBy = (set: VariableSet) => [...set.templates, ...(set.inventories ?? [])].map(one => one.name);

function SecretVariables({ rows, onChange }: { rows: SecretRow[]; onChange: (rows: SecretRow[]) => void }) {
    const { t } = useTranslation();
    const change = (key: number, part: Partial<SecretRow>) => onChange(rows.map(row => row.key === key ? { ...row, ...part } : row));
    const unnamed = t("the secret variable");
    return (
        <FormGroup label={t("Secret variables")} fieldId="varset-secrets" role="group">
            {rows.length > 0 && (
                <Table variant="compact" aria-label={t("Secret variables")} borders={false}>
                    <Tbody>
                        {rows.map((row, index) => (
                            <Tr key={row.key}>
                                <Td dataLabel={t("Name")}>
                                    {row.saved
                                        ? <code>{row.name}</code>
                                        : <TextInput id={`varset-secret-name-${index}`} aria-label={t("Name of the secret variable")} value={row.name}
                                            placeholder="license_key" onChange={(_e, value) => change(row.key, { name: value })} />}
                                </Td>
                                <Td dataLabel={t("Value")}>
                                    <TextInput id={`varset-secret-value-${index}`} type="password" autoComplete="new-password"
                                        aria-label={t("Value of {{name}}", { name: row.name || unnamed })}
                                        value={row.value} onChange={(_e, value) => change(row.key, { value })}
                                        placeholder={row.saved?.set ? t("Set: leave empty to keep it") : t("Not set")} />
                                </Td>
                                <Td isActionCell>
                                    <Button variant="link" isInline onClick={() => onChange(rows.filter(one => one.key !== row.key))}
                                        aria-label={t("Remove {{name}}", { name: row.name || unnamed })}>{t("Remove")}</Button>
                                </Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
            <FlexItem>
                <Button variant="link" isInline onClick={() => onChange([...rows, { key: Date.now() + rows.length, name: "", value: "" }])}>
                    {t("Add a secret variable")}
                </Button>
            </FlexItem>
            <FormHelperText><HelperText><HelperTextItem>{t("Their values are encrypted and can never be read back, here or anywhere; a run gets them as extra variables, used as written, and they are hidden in its output. The inventory's files and the playbooks use them by name: {{example}}.", { example: '"{{ license_key }}"' })}</HelperTextItem></HelperText></FormHelperText>
        </FormGroup>
    );
}

function VariableSetModal({ set, onClose, onDone }: { set?: VariableSet; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState(set?.name ?? "");
    const [description, setDescription] = useState(set?.description ?? "");
    const [text, setText] = useState(set && Object.keys(set.variables).length ? JSON.stringify(set.variables, null, 2) : "");
    const [secrets, setSecrets] = useState<SecretRow[]>(
        (set?.secrets ?? []).map((saved, key) => ({ key, name: saved.name, saved, value: "" })));

    async function submit() {
        const variables = parseVariables(text);
        if (variables === null) throw new Error(t("The variables must be a JSON object, for example {\"domain\": \"school.edu\"}."));
        const rows = secrets.map(row => ({ ...row, name: row.name.trim() }));
        if (rows.some(row => !row.name)) throw new Error(t("Give each secret variable a name."));
        const params = { name, description, variables, secrets: rows.map(row => row.name) };
        const saved = await call<VariableSet>(set ? "varset.update" : "varset.create", set ? { id: set.id, ...params } : params);
        // The values go to fleet-secret once the set has made room for them.
        for (const row of rows.filter(one => one.value)) {
            const where = saved.secrets.find(one => one.name === row.name);
            if (where) await setSecret(where.secret_name, row.value);
        }
        // A secret variable taken out of the set loses its value.
        for (const gone of (set?.secrets ?? []).filter(one => one.set && !rows.some(row => row.name === one.name))) {
            await deleteSecret(gone.secret_name);
        }
        onDone();
        onClose();
    }

    return (
        <FormModal title={set ? t("Edit set of variables") : t("New set of variables")} submitLabel={set ? t("Save") : t("Create")}
            onSubmit={submit} onClose={onClose} isDisabled={!name.trim()}>
            <FormGroup label={t("Name")} isRequired fieldId="varset-name">
                <TextInput id="varset-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("Description")} fieldId="varset-description">
                <TextInput id="varset-description" value={description} onChange={(_e, value) => setDescription(value)} />
            </FormGroup>
            <FormGroup label={t("Variables")} fieldId="varset-variables" role="group">
                <CodeEditor className="fleet-code-field" label={t("Variables")} path="vars.json" value={text} onChange={setText}
                    placeholder={'{\n  "domain": "school.edu"\n}'} />
                <FormHelperText><HelperText><HelperTextItem>{t("A JSON object, which every role sees: a secret goes among the secret variables below.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            <SecretVariables rows={secrets} onChange={setSecrets} />
            {set && usedBy(set).length > 0 && (
                <Content component="p">{t("A change applies to the next runs of: {{templates}}.", { templates: usedBy(set).join(", ") })}</Content>
            )}
        </FormModal>
    );
}

export function VariablesPage() {
    const { t } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ variable_sets: VariableSet[] }>("varset.list"));
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<VariableSet | null>(null);
    const [deleting, setDeleting] = useState<VariableSet | null>(null);

    return (
        <>
            <SectionHeader title={t("Sets of variables")}>
                {can("varset.create") && <Button variant="primary" onClick={() => setCreating(true)}>{t("New set of variables")}</Button>}
            </SectionHeader>
            <ListState loaded={list} isEmpty={data => data.variable_sets.length === 0}
                emptyTitle={t("No sets of variables")}
                emptyBody={t("A set of variables holds extra variables, secret ones too, that job templates and inventories take, so that a value common to many of them is written once. A run takes its inventory's sets, then its template's, in order, then the template's own variables; a later one wins. Variables that depend on the hosts belong in the group_vars of an inventory instead.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Sets of variables")}>
                        <Thead>
                            <Tr>
                                <Th>{t("Name")}</Th><Th>{t("Variables")}</Th><Th>{t("Used by")}</Th><Th screenReaderText={t("Actions")} />
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.variable_sets.map(set => (
                                <Tr key={set.id}>
                                    <Td dataLabel={t("Name")}>
                                        {set.name}
                                        {set.description && <Content component="small" style={{ display: "block" }}>{set.description}</Content>}
                                    </Td>
                                    <Td dataLabel={t("Variables")}>
                                        {Object.keys(set.variables).map(name => (
                                            <Label key={name} isCompact style={{ marginInlineEnd: "0.25rem" }}><code>{name}</code></Label>
                                        ))}
                                        {(set.secrets ?? []).map(secret => (
                                            <Label key={secret.name} isCompact color={secret.set ? "purple" : "orange"} icon={<LockIcon />}
                                                style={{ marginInlineEnd: "0.25rem" }}
                                                title={secret.set ? t("Secret") : t("Secret, not set: runs that take it stop")}>
                                                <code>{secret.name}</code>{!secret.set && <> {t("not set")}</>}
                                            </Label>
                                        ))}
                                    </Td>
                                    <Td dataLabel={t("Used by")}>{usedBy(set).join(", ") || "—"}</Td>
                                    <Td isActionCell>
                                        {can("varset.update") && (
                                            <ActionsColumn items={[
                                                { title: t("Edit"), onClick: () => setEditing(set) },
                                                {
                                                    title: t("Delete"), onClick: () => setDeleting(set), isAriaDisabled: usedBy(set).length > 0,
                                                    tooltipProps: usedBy(set).length > 0 ? { content: t("Job templates or inventories use it") } : undefined,
                                                },
                                            ]} />
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {creating && <VariableSetModal onClose={() => setCreating(false)} onDone={list.reload} />}
            {editing && <VariableSetModal set={editing} onClose={() => setEditing(null)} onDone={list.reload} />}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The set of variables is deleted. This cannot be undone.")}
                    onDelete={async () => {
                        await call("varset.delete", { id: deleting.id });
                        for (const secret of (deleting.secrets ?? []).filter(one => one.set)) await deleteSecret(secret.secret_name);
                        setDeleting(null);
                        list.reload();
                    }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}

// The sets a job template or an inventory takes, in order.
export function SetOrderField({ sets, ids, onChange, fieldId, help }: {
    sets: VariableSet[];
    ids: string[];
    onChange: (ids: string[]) => void;
    fieldId: string;
    help: string;
}) {
    const { t } = useTranslation();
    const name = (id: string) => sets.find(set => set.id === id)?.name ?? id;
    const free = sets.filter(set => !ids.includes(set.id));
    return (
        <FormGroup label={t("Sets of variables")} fieldId={fieldId} role="group">
            {ids.length > 0 && (
                <Table variant="compact" aria-label={t("Sets of variables")} borders={false}>
                    <Tbody>
                        {ids.map((id, index) => (
                            <Tr key={id}>
                                <Td>{index + 1}. {name(id)}</Td>
                                <Td isActionCell>
                                    <Flex gap={{ default: "gapSm" }} justifyContent={{ default: "justifyContentFlexEnd" }} flexWrap={{ default: "nowrap" }}>
                                        <Button variant="plain" isDisabled={index === 0} onClick={() => onChange(moved(ids, index, -1))}
                                            aria-label={t("Move {{name}} up", { name: name(id) })}>↑</Button>
                                        <Button variant="plain" isDisabled={index === ids.length - 1} onClick={() => onChange(moved(ids, index, 1))}
                                            aria-label={t("Move {{name}} down", { name: name(id) })}>↓</Button>
                                        <Button variant="link" isInline onClick={() => onChange(ids.filter(one => one !== id))}
                                            aria-label={t("Remove {{name}}", { name: name(id) })}>{t("Remove")}</Button>
                                    </Flex>
                                </Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
            {sets.length === 0
                ? <span>{t("None defined")}</span>
                : (
                    <FlexItem>
                        <FormSelect id={fieldId} value="" isDisabled={free.length === 0} aria-label={t("Add a set of variables")}
                            onChange={(_e, value) => { if (value) onChange([...ids, value]); }}>
                            <FormSelectOption value="" label={free.length ? t("Add a set of variables…") : t("Every set is taken")} isPlaceholder />
                            {free.map(set => <FormSelectOption key={set.id} value={set.id} label={set.name} />)}
                        </FormSelect>
                    </FlexItem>
                )}
            <FormHelperText><HelperText><HelperTextItem>{help}</HelperTextItem></HelperText></FormHelperText>
        </FormGroup>
    );
}

// In the form of a job template: the sets it takes, in order, and what a run
// gets in the end from them, the sets of its inventory and the variables
// typed in the template.
export function VariableSetsField({ sets, ids, onChange, own, inventoryIds }: {
    sets: VariableSet[];
    ids: string[];
    onChange: (ids: string[]) => void;
    own: string;
    inventoryIds: string[];
}) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const parsed = parseVariables(own);
    const effective = effectiveVariables(sets, ids, parsed ?? {}, inventoryIds);
    const from = (one: { from: string | null; ofInventory: boolean }) => one.from === null
        ? t("This template")
        : one.ofInventory ? t("{{name}} (inventory)", { name: one.from }) : one.from;
    return (
        <>
            <SetOrderField sets={sets} ids={ids} onChange={onChange} fieldId="tpl-varsets"
                help={t("Taken in this order, after those of the inventory and before the extra variables below: a later one wins.")} />
            {ids.length + inventoryIds.length > 0 && (
                <ExpandableSection toggleText={t("Variables of a run ({{number}})", { number: effective.length })} isExpanded={open}
                    onToggle={(_e, value) => setOpen(value)}>
                    {parsed === null && <Content component="p">{t("The extra variables below are not a JSON object yet: only the sets are shown.")}</Content>}
                    <Table variant="compact" aria-label={t("Variables of a run")}>
                        <Thead><Tr><Th>{t("Variable")}</Th><Th>{t("Value")}</Th><Th>{t("From")}</Th></Tr></Thead>
                        <Tbody>
                            {effective.map(one => (
                                <Tr key={one.name}>
                                    <Td dataLabel={t("Variable")}><code>{one.name}</code></Td>
                                    <Td dataLabel={t("Value")}>
                                        {one.secret
                                            ? <Label isCompact color="purple" icon={<LockIcon />}>{t("Secret")}</Label>
                                            : <code>{shown(one.value)}</code>}
                                    </Td>
                                    <Td dataLabel={t("From")}>
                                        {from(one)}
                                        {one.overrides.length > 0 && (
                                            <Content component="small" style={{ display: "block" }}>
                                                {t("instead of {{sets}}", { sets: one.overrides.map(name => name || t("This template")).join(", ") })}
                                            </Content>
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                    <Content component="small">{t("A survey's answer, and what earlier steps of a workflow pass on, win over these.")}</Content>
                </ExpandableSection>
            )}
        </>
    );
}
