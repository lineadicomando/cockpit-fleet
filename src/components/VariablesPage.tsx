import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button, Content, ExpandableSection, Flex, FlexItem, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem,
    Label, TextInput,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { VariableSet } from "../lib/types.ts";
import { effectiveVariables, moved, parseVariables } from "../lib/variables.ts";
import { CodeEditor } from "./CodeEditor.tsx";
import { ConfirmDelete, FormModal, ListState, SectionHeader } from "./common.tsx";
import { ActionsColumn } from "./pf.tsx";

const shown = (value: unknown) => JSON.stringify(value);

function VariableSetModal({ set, onClose, onDone }: { set?: VariableSet; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState(set?.name ?? "");
    const [description, setDescription] = useState(set?.description ?? "");
    const [text, setText] = useState(set && Object.keys(set.variables).length ? JSON.stringify(set.variables, null, 2) : "");

    async function submit() {
        const variables = parseVariables(text);
        if (variables === null) throw new Error(t("The variables must be a JSON object, for example {\"domain\": \"school.edu\"}."));
        const params = { name, description, variables };
        await call(set ? "varset.update" : "varset.create", set ? { id: set.id, ...params } : params);
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
                <FormHelperText><HelperText><HelperTextItem>{t("A JSON object. Not for secrets: they would be stored in clear, and every role sees them.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            {set && set.templates.length > 0 && (
                <Content component="p">{t("A change applies to the next runs of: {{templates}}.", { templates: set.templates.map(one => one.name).join(", ") })}</Content>
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
                emptyBody={t("A set of variables holds extra variables that job templates take, so that a value common to many of them is written once. A template takes them in order, before its own; a later one wins. Variables that depend on the hosts belong in the group_vars of an inventory instead.")}>
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
                                    </Td>
                                    <Td dataLabel={t("Used by")}>{set.templates.map(one => one.name).join(", ") || "—"}</Td>
                                    <Td isActionCell>
                                        {can("varset.update") && (
                                            <ActionsColumn items={[
                                                { title: t("Edit"), onClick: () => setEditing(set) },
                                                {
                                                    title: t("Delete"), onClick: () => setDeleting(set), isAriaDisabled: set.templates.length > 0,
                                                    tooltipProps: set.templates.length > 0 ? { content: t("Job templates use it") } : undefined,
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
                    onDelete={async () => { await call("varset.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}

// In the form of a job template: the sets it takes, in order, and what a run
// gets in the end from them and the variables typed in the template.
export function VariableSetsField({ sets, ids, onChange, own }: {
    sets: VariableSet[];
    ids: string[];
    onChange: (ids: string[]) => void;
    own: string;
}) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const name = (id: string) => sets.find(set => set.id === id)?.name ?? id;
    const free = sets.filter(set => !ids.includes(set.id));
    const parsed = parseVariables(own);
    const effective = effectiveVariables(sets, ids, parsed ?? {});
    return (
        <>
            <FormGroup label={t("Sets of variables")} fieldId="tpl-varsets" role="group">
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
                            <FormSelect id="tpl-varsets" value="" isDisabled={free.length === 0} aria-label={t("Add a set of variables")}
                                onChange={(_e, value) => { if (value) onChange([...ids, value]); }}>
                                <FormSelectOption value="" label={free.length ? t("Add a set of variables…") : t("Every set is taken")} isPlaceholder />
                                {free.map(set => <FormSelectOption key={set.id} value={set.id} label={set.name} />)}
                            </FormSelect>
                        </FlexItem>
                    )}
                <FormHelperText><HelperText><HelperTextItem>{t("Taken in this order, before the extra variables below: a later one wins.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            {ids.length > 0 && (
                <ExpandableSection toggleText={t("Variables of a run ({{number}})", { number: effective.length })} isExpanded={open}
                    onToggle={(_e, value) => setOpen(value)}>
                    {parsed === null && <Content component="p">{t("The extra variables below are not a JSON object yet: only the sets are shown.")}</Content>}
                    <Table variant="compact" aria-label={t("Variables of a run")}>
                        <Thead><Tr><Th>{t("Variable")}</Th><Th>{t("Value")}</Th><Th>{t("From")}</Th></Tr></Thead>
                        <Tbody>
                            {effective.map(one => (
                                <Tr key={one.name}>
                                    <Td dataLabel={t("Variable")}><code>{one.name}</code></Td>
                                    <Td dataLabel={t("Value")}><code>{shown(one.value)}</code></Td>
                                    <Td dataLabel={t("From")}>
                                        {one.from ?? t("This template")}
                                        {one.overrides.length > 0 && (
                                            <Content component="small" style={{ display: "block" }}>
                                                {t("instead of {{sets}}", { sets: one.overrides.map(from => from || t("This template")).join(", ") })}
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
