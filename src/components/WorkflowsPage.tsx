import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Card,
    CardBody,
    CardTitle,
    Checkbox,
    Content,
    Flex,
    FlexItem,
    FormGroup,
    FormHelperText,
    FormSelect,
    FormSelectOption,
    Grid,
    GridItem,
    HelperText,
    HelperTextItem,
    Label,
    TextArea,
    TextInput,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { TimesIcon } from "@patternfly/react-icons";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import { answerProblem, collectAnswers, storedAnswers, type Answers } from "../lib/survey.ts";
import { layers, nextStepId } from "../lib/workflow.ts";
import type { Run, StepCondition, Template, Workflow, WorkflowEdge, WorkflowProgress, StepState } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, RunStatusLabel, SectionHeader, message } from "./common.tsx";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";
import { SurveyAnswers } from "./SurveyEditor.tsx";
import { ActionsColumn, Alert } from "./pf.tsx";

interface StepDraft {
    id: string;
    name: string;
    template_id: string;
    limit: string;
    needs_approval: boolean;
    answers: Answers;
    after: { from: string; condition: StepCondition }[];
}

function toDrafts(workflow: Workflow | undefined, templates: Template[]): StepDraft[] {
    if (!workflow) return [{ id: "s1", name: "", template_id: "", limit: "", needs_approval: false, answers: {}, after: [] }];
    return workflow.nodes.map(node => ({
        id: node.id, name: node.name, template_id: node.template_id, limit: node.limit, needs_approval: node.needs_approval,
        answers: storedAnswers(templates.find(item => item.id === node.template_id), node.answers),
        after: workflow.edges.filter(edge => edge.to === node.id).map(edge => ({ from: edge.from, condition: edge.condition })),
    }));
}

// A step answers its template's survey, without the secret questions: nothing secret is stored.
const surveyOf = (template: Template | undefined) => (template?.survey ?? []).filter(field => field.type !== "secret");

function useConditions() {
    const { t } = useTranslation();
    return { success: t("succeeds"), failure: t("fails"), always: t("ends, either way") } as Record<StepCondition, string>;
}

function StepEditor({ step, steps, templates, onChange, onRemove }: {
    step: StepDraft;
    steps: StepDraft[];
    templates: Template[];
    onChange: (step: StepDraft) => void;
    onRemove?: () => void;
}) {
    const { t } = useTranslation();
    const conditions = useConditions();
    const template = templates.find(item => item.id === step.template_id);
    const others = steps.filter(other => other.id !== step.id);
    const label = (other: StepDraft) => other.name || templates.find(item => item.id === other.template_id)?.name || other.id;
    const needsSecret = (template?.survey ?? []).some(field => field.type === "secret" && field.required);

    return (
        <Card isCompact>
            <CardTitle>
                <Flex justifyContent={{ default: "justifyContentSpaceBetween" }}>
                    <FlexItem>{t("Step {{id}}", { id: step.id })}</FlexItem>
                    {onRemove && <FlexItem><Button variant="link" isDanger isInline onClick={onRemove}>{t("Remove")}</Button></FlexItem>}
                </Flex>
            </CardTitle>
            <CardBody>
                <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
                <Grid hasGutter md={6}>
                    <GridItem>
                        <FormGroup label={t("Job template")} isRequired fieldId={`wf-${step.id}-template`}>
                            <FormSelect id={`wf-${step.id}-template`} value={step.template_id}
                                onChange={(_e, value) => onChange({ ...step, template_id: value, answers: storedAnswers(templates.find(item => item.id === value), {}) })}>
                                <FormSelectOption value="" label={t("Choose a job template")} isPlaceholder />
                                {templates.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                            </FormSelect>
                        </FormGroup>
                    </GridItem>
                    <GridItem>
                        <FormGroup label={t("Name")} fieldId={`wf-${step.id}-name`}>
                            <TextInput id={`wf-${step.id}-name`} value={step.name} placeholder={template?.name} onChange={(_e, value) => onChange({ ...step, name: value })} />
                        </FormGroup>
                    </GridItem>
                </Grid>
                {needsSecret && <Alert variant="warning" isInline title={t("This job template asks for a secret at launch, so it cannot be a step: a workflow never stores a secret.")} />}
                <FormGroup label={t("Limit to hosts")} fieldId={`wf-${step.id}-limit`}>
                    <TextInput id={`wf-${step.id}-limit`} value={step.limit} placeholder={template?.limit_hosts || "web01,web02"} onChange={(_e, value) => onChange({ ...step, limit: value })} />
                </FormGroup>
                <SurveyAnswers survey={surveyOf(template)} answers={step.answers} onChange={answers => onChange({ ...step, answers })} />
                <FormGroup fieldId={`wf-${step.id}-approval`}>
                    <Checkbox id={`wf-${step.id}-approval`} label={t("Wait for an approval before this step")} isChecked={step.needs_approval || !!template?.needs_approval}
                        isDisabled={!!template?.needs_approval} onChange={(_e, value) => onChange({ ...step, needs_approval: value })} />
                </FormGroup>
                <FormGroup label={t("Runs after")} fieldId={`wf-${step.id}-after`} role="group">
                    {step.after.map((link, index) => (
                        <Grid key={index} hasGutter style={{ marginBlockEnd: "0.5rem" }}>
                            <GridItem span={6}>
                            <FormSelect aria-label={t("Step before")} value={link.from}
                                onChange={(_e, value) => onChange({ ...step, after: step.after.map((item, at) => at === index ? { ...item, from: value } : item) })}>
                                <FormSelectOption value="" label={t("Choose a step")} isPlaceholder />
                                {others.map(other => <FormSelectOption key={other.id} value={other.id} label={label(other)} />)}
                            </FormSelect>
                            </GridItem>
                            <GridItem span={5}>
                            <FormSelect aria-label={t("When it")} value={link.condition}
                                onChange={(_e, value) => onChange({ ...step, after: step.after.map((item, at) => at === index ? { ...item, condition: value as StepCondition } : item) })}>
                                {(Object.keys(conditions) as StepCondition[]).map(condition => <FormSelectOption key={condition} value={condition} label={conditions[condition]} />)}
                            </FormSelect>
                            </GridItem>
                            <GridItem span={1}>
                                <Button variant="plain" icon={<TimesIcon />} aria-label={t("Remove")} onClick={() => onChange({ ...step, after: step.after.filter((_item, at) => at !== index) })} />
                            </GridItem>
                        </Grid>
                    ))}
                    {others.length > 0 && (
                        <Button variant="link" isInline onClick={() => onChange({ ...step, after: [...step.after, { from: "", condition: "success" }] })}>{t("Add a step it follows")}</Button>
                    )}
                    <FormHelperText><HelperText><HelperTextItem>
                        {step.after.length === 0 ? t("It runs as soon as the workflow is launched.") : t("It runs once all these steps have ended, if one of them ended as said.")}
                    </HelperTextItem></HelperText></FormHelperText>
                </FormGroup>
                </Flex>
            </CardBody>
        </Card>
    );
}

function WorkflowModal({ workflow, templates, onClose, onDone }: { workflow?: Workflow; templates: Template[]; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState(workflow?.name ?? "");
    const [description, setDescription] = useState(workflow?.description ?? "");
    const [steps, setSteps] = useState<StepDraft[]>(() => toDrafts(workflow, templates));

    const invalid = !name.trim() || steps.some(step => {
        const template = templates.find(item => item.id === step.template_id);
        return !template || step.after.some(link => !link.from)
            || surveyOf(template).some(field => answerProblem(field, step.answers[field.name]))
            || (template.survey ?? []).some(field => field.type === "secret" && field.required);
    });

    async function submit() {
        const nodes = steps.map(step => ({
            id: step.id, name: step.name.trim(), template_id: step.template_id, limit: step.limit.trim(),
            needs_approval: step.needs_approval,
            answers: collectAnswers(surveyOf(templates.find(item => item.id === step.template_id)), step.answers),
        }));
        const edges: WorkflowEdge[] = steps.flatMap(step => step.after.map(link => ({ from: link.from, to: step.id, condition: link.condition })));
        const params = { name, description, nodes, edges };
        await call(workflow ? "workflow.update" : "workflow.create", workflow ? { id: workflow.id, ...params } : params);
        onDone();
        onClose();
    }

    function remove(id: string) {
        setSteps(steps.filter(step => step.id !== id).map(step => ({ ...step, after: step.after.filter(link => link.from !== id) })));
    }

    return (
        <FormModal title={workflow ? t("Edit workflow") : t("New workflow")} submitLabel={t("Save")} onSubmit={submit} onClose={onClose} isDisabled={invalid}>
            <FormGroup label={t("Name")} isRequired fieldId="wf-name">
                <TextInput id="wf-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("Description")} fieldId="wf-description">
                <TextArea id="wf-description" value={description} onChange={(_e, value) => setDescription(value)} rows={2} />
            </FormGroup>
            <Content component="small">{t("Each step passes on to the steps after it the variables its playbook sets with set_stats (with per_host: false). They are kept in clear with the run: not for secrets.")}</Content>
            {steps.map(step => (
                <StepEditor key={step.id} step={step} steps={steps} templates={templates}
                    onChange={changed => setSteps(steps.map(item => item.id === step.id ? changed : item))}
                    onRemove={steps.length > 1 ? () => remove(step.id) : undefined} />
            ))}
            <div>
                <Button variant="secondary" onClick={() => {
                    const last = steps[steps.length - 1];
                    const id = nextStepId(steps.map(step => step.id));
                    setSteps([...steps, { id, name: "", template_id: "", limit: "", needs_approval: false, answers: {}, after: last ? [{ from: last.id, condition: "success" }] : [] }]);
                }}>{t("Add a step")}</Button>
            </div>
        </FormModal>
    );
}

export function WorkflowsPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ workflows: Workflow[] }>("workflow.list"));
    const templates = useLoad(() => call<{ templates: Template[] }>("template.list"));
    const [editing, setEditing] = useState<Workflow | "new" | null>(null);
    const [deleting, setDeleting] = useState<Workflow | null>(null);
    const [error, setError] = useState<string | null>(null);
    const names = new Map((templates.data?.templates ?? []).map(item => [item.id, item.name]));

    async function launch(workflow: Workflow) {
        setError(null);
        try {
            const run = await call<Run>("workflow.launch", { id: workflow.id });
            cockpit.location.go(["runs", run.id]);
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            <SectionHeader title={t("Workflows")}>
                <Button variant="secondary" onClick={list.reload}>{t("Refresh")}</Button>
                {can("workflow.create") && <Button variant="primary" onClick={() => setEditing("new")} isDisabled={!templates.data}>{t("New workflow")}</Button>}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.workflows.length === 0}
                emptyTitle={t("No workflows yet")}
                emptyBody={t("A workflow runs job templates one after the other: each step starts by how the steps before it ended.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Workflows")}>
                        <Thead><Tr><Th>{t("Name")}</Th><Th>{t("Steps")}</Th><Th>{t("Last run")}</Th><Th screenReaderText={t("Actions")} /></Tr></Thead>
                        <Tbody>
                            {data.workflows.map(workflow => (
                                <Tr key={workflow.id}>
                                    <Td dataLabel={t("Name")}>
                                        {workflow.name}
                                        {workflow.description && <Content component="small" style={{ display: "block" }}>{workflow.description}</Content>}
                                    </Td>
                                    <Td dataLabel={t("Steps")}>{workflow.nodes.map(node => node.name || names.get(node.template_id) || node.id).join(" → ")}</Td>
                                    <Td dataLabel={t("Last run")}>
                                        {workflow.last_run
                                            ? <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", workflow.last_run!.id])}>
                                                <RunStatusLabel status={workflow.last_run.status} /> {formatTime(workflow.last_run.created_at, i18n.language)}
                                            </Button>
                                            : "—"}
                                    </Td>
                                    <Td isActionCell>
                                        <Flex gap={{ default: "gapSm" }} flexWrap={{ default: "nowrap" }} justifyContent={{ default: "justifyContentFlexEnd" }}>
                                            {can("workflow.launch") && <Button variant="secondary" size="sm" onClick={() => launch(workflow)}>{t("Launch")}</Button>}
                                            {can("workflow.update") && (
                                                <ActionsColumn items={[
                                                    { title: t("Edit"), onClick: () => setEditing(workflow), isDisabled: !templates.data },
                                                    { title: t("Delete"), onClick: () => setDeleting(workflow) },
                                                ]} />
                                            )}
                                        </Flex>
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {editing && templates.data && (
                <WorkflowModal workflow={editing === "new" ? undefined : editing} templates={templates.data.templates}
                    onClose={() => setEditing(null)} onDone={list.reload} />
            )}
            {deleting && (
                <ConfirmDelete what={t("Its past runs stay.")} name={deleting.name}
                    onDelete={async () => { await call("workflow.delete", { id: deleting.id }); list.reload(); setDeleting(null); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}

const STATE_TONE: Record<StepState, Tone | null> = {
    success: "good", failure: "bad", active: null, ready: null, waiting: null, skipped: null,
};

function StepStateLabel({ node }: { node: WorkflowProgress["nodes"][number] }) {
    const { t } = useTranslation();
    if (node.run) return <RunStatusLabel status={node.run.status} />;
    const text: Record<StepState, string> = {
        success: t("Successful"), failure: t("Could not start"), active: t("Running"), ready: t("Starting"),
        waiting: t("Not yet"), skipped: t("Not run"),
    };
    const tone = STATE_TONE[node.state];
    return tone ? <StatusLabel tone={tone}>{text[node.state]}</StatusLabel> : <Label isCompact color="grey">{text[node.state]}</Label>;
}

// The steps of a workflow run, in columns: each after those it follows.
export function WorkflowSteps({ id }: { id: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const conditions = useConditions();
    const progress = useLoad(() => call<WorkflowProgress>("workflow.progress", { id }));
    const running = progress.data?.run.status === "running";
    usePolling(progress.reload, 2000, running ?? true);
    const [error, setError] = useState<string | null>(null);

    if (progress.error) return <Alert variant="danger" isInline title={progress.error.message} />;
    if (!progress.data) return null;
    const data = progress.data;
    const byId = new Map(data.nodes.map(node => [node.id, node]));
    const name = (node: WorkflowProgress["nodes"][number]) => node.name || node.run?.title || node.id;
    const columns = layers(data.nodes.map(node => node.id), data.edges);
    const resumable = ["failed", "error", "canceled"].includes(data.run.status) && can("workflow.resume");

    async function resume() {
        setError(null);
        try {
            const run = await call<Run>("workflow.resume", { id });
            cockpit.location.go(["runs", run.id]);
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {data.resumed_from && (
                <Content component="p">
                    {t("Resumed from")} <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", data.resumed_from!])}>{data.resumed_from}</Button>
                </Content>
            )}
            <Flex gap={{ default: "gapLg" }} alignItems={{ default: "alignItemsFlexStart" }} flexWrap={{ default: "nowrap" }} style={{ overflowX: "auto" }}>
                {columns.map((column, index) => (
                    <Flex key={index} direction={{ default: "column" }} gap={{ default: "gapMd" }} style={{ minWidth: "16rem" }}>
                        {column.map(nodeId => {
                            const node = byId.get(nodeId)!;
                            const after = data.edges.filter(edge => edge.to === nodeId);
                            return (
                                <Card key={nodeId} isCompact>
                                    <CardTitle>
                                        {node.run
                                            ? <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", node.run!.id])}>{name(node)}</Button>
                                            : name(node)}
                                    </CardTitle>
                                    <CardBody>
                                        <StepStateLabel node={node} />
                                        {node.carried && <Label isCompact color="blue" style={{ marginInlineStart: "0.5rem" }}>{t("Done before")}</Label>}
                                        {(node.needs_approval) && <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }}>{t("Approval")}</Label>}
                                        {node.limit && <Content component="small" style={{ display: "block" }}><code>{node.limit}</code></Content>}
                                        {node.run?.started_at && <Content component="small" style={{ display: "block" }}>{formatTime(node.run.started_at, i18n.language)}</Content>}
                                        {after.map(edge => (
                                            <Content key={edge.from} component="small" style={{ display: "block" }}>
                                                {t("after {{step}}, if it {{condition}}", { step: name(byId.get(edge.from)!), condition: conditions[edge.condition] })}
                                            </Content>
                                        ))}
                                        {node.error && <Content component="small" style={{ display: "block", color: "var(--pf-t--global--text--color--status--danger--default)" }}>{node.error}</Content>}
                                    </CardBody>
                                </Card>
                            );
                        })}
                    </Flex>
                ))}
            </Flex>
            {resumable && (
                <div style={{ marginBlockStart: "1rem" }}>
                    <Button variant="secondary" onClick={resume}>{t("Resume from the failed steps")}</Button>
                </div>
            )}
        </>
    );
}
