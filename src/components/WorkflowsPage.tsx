import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Card,
    CardBody,
    CardTitle,
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
    Tooltip,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { TimesIcon, UserClockIcon } from "@patternfly/react-icons";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import { answerProblem, collectAnswers, storedAnswers, type Answers } from "../lib/survey.ts";
import { OUTCOMES, type Outcome } from "../lib/templates.ts";
import {
    EACH_STEPS_OWN, inScope, isWorkflowGroupOpen, lastWorkflowRun, layers, listWorkflows, nextStepId, readWorkflowsView, stepAsksApproval, stepColumns,
    stepName, templateGroups, withoutWorkflowFilters, withWorkflowGroupsOpen, workflowInventories, workflowProjects,
    type StepCell, type StepScope, type WorkflowGroup, type WorkflowNames, type WorkflowSort, type WorkflowsView, type WorkflowTrait,
} from "../lib/workflow.ts";
import type { ApprovalMode, Inventory, Project, Run, Schedule, StepCondition, Template, Workflow, WorkflowEdge, WorkflowProgress, StepState } from "../lib/types.ts";
import { ApprovalModeField, ApprovalModeLabel } from "./ApprovalsPage.tsx";
import { ConfirmDelete, FormModal, ListState, RunStatusLabel, SectionHeader, message, announceRun, openRun, isActiveRun } from "./common.tsx";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";
import { SearchSelect } from "./SearchSelect.tsx";
import { SurveyAnswers } from "./SurveyEditor.tsx";
import { FilterChips, GroupByChoice, GroupRow, NothingMatches, useStoredView } from "./ListView.tsx";
import { ActionsColumn, Alert, SearchInput } from "./pf.tsx";
import { useOutcomeNames } from "./TemplatesPage.tsx";

interface StepDraft {
    id: string;
    name: string;
    template_id: string;
    limit: string;
    approval: ApprovalMode;
    answers: Answers;
    after: { from: string; condition: StepCondition }[];
}

function toDrafts(workflow: Workflow | undefined, templates: Template[]): StepDraft[] {
    if (!workflow) return [{ id: "s1", name: "", template_id: "", limit: "", approval: "none", answers: {}, after: [] }];
    return workflow.nodes.map(node => ({
        id: node.id, name: node.name, template_id: node.template_id, limit: node.limit, approval: node.approval,
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

// The steps of a workflow in columns, one after the other: those of a column may
// start together, or be alternatives, a step reached only when one before it fails.
export function StepColumns({ columns }: { columns: StepCell[][] }) {
    const { t } = useTranslation();
    const branchText = { failure: t("if it fails"), always: t("anyway") };
    const step = (cell: StepCell) => {
        const label = (
            <Label isCompact variant={cell.branch ? "outline" : "filled"} color={cell.branch === "failure" ? "red" : "grey"}
                icon={cell.approval ? <UserClockIcon /> : undefined}>
                {cell.name}
                {cell.branch && <span className="pf-v6-u-text-color-subtle"> · {branchText[cell.branch]}</span>}
                {cell.approval && <span className="pf-v6-screen-reader"> ({t("waits for an approval")})</span>}
            </Label>
        );
        return <React.Fragment key={cell.id}>{cell.approval ? <Tooltip content={t("Waits for an approval")}>{label}</Tooltip> : label}</React.Fragment>;
    };
    return (
        <ol className="fleet-step-columns" aria-label={t("Steps in order")}>
            {columns.map((column, index) => (
                <li key={index}>{column.length > 1 ? <span className="fleet-step-together">{column.map(step)}</span> : column.map(step)}</li>
            ))}
        </ol>
    );
}

interface Choices {
    templates: Template[];
    projects: Project[];
    inventories: Inventory[];
}

function StepEditor({ step, steps, templates, scope, inventories, projects, onChange, onRemove }: {
    step: StepDraft;
    steps: StepDraft[];
    templates: Template[];
    scope: StepScope;
    inventories: Map<string, string>;
    projects: Map<string, string>;
    onChange: (step: StepDraft) => void;
    onRemove?: () => void;
}) {
    const { t } = useTranslation();
    const conditions = useConditions();
    const template = templates.find(item => item.id === step.template_id);
    const others = steps.filter(other => other.id !== step.id);
    const label = (other: StepDraft) => other.name || templates.find(item => item.id === other.template_id)?.name || other.id;
    const needsSecret = (template?.survey ?? []).some(field => field.type === "secret" && field.required);
    const outside = !!template && !inScope(template, scope);
    // The one chosen stays in the list when it no longer fits, to be seen and changed.
    const offered = templates.filter(item => inScope(item, scope) || item.id === step.template_id);
    const groups = templateGroups(offered, scope, inventories, projects, t("Chosen at launch"));
    // Where it runs: the workflow's inventory, else its template's default.
    const runsOn = scope.inventory_id || template?.inventory_id || "";
    const inventory = runsOn ? inventories.get(runsOn) ?? runsOn : "";

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
                            <SearchSelect id={`wf-${step.id}-template`} label={t("Job template")} value={step.template_id}
                                groups={groups} placeholder={t("Choose a job template")} toggleLabel={t("Show the job templates")}
                                emptyText={t("No job template matches what was typed")}
                                onChange={value => onChange({ ...step, template_id: value, answers: storedAnswers(templates.find(item => item.id === value), {}) })} />
                            {template && (
                                <FormHelperText><HelperText><HelperTextItem>
                                    {inventory
                                        ? t("Runs on the inventory {{inventory}}.", { inventory })
                                        : t("Runs on the inventory chosen at launch: the job template has no default.")}
                                </HelperTextItem></HelperText></FormHelperText>
                            )}
                        </FormGroup>
                    </GridItem>
                    <GridItem>
                        <FormGroup label={t("Name")} fieldId={`wf-${step.id}-name`}>
                            <TextInput id={`wf-${step.id}-name`} value={step.name} placeholder={template?.name} onChange={(_e, value) => onChange({ ...step, name: value })} />
                        </FormGroup>
                    </GridItem>
                </Grid>
                {outside && <Alert variant="danger" isInline title={t("This job template is not of the project or the inventory of the workflow: choose another one.")} />}
                {needsSecret && <Alert variant="warning" isInline title={t("This job template asks for a secret at launch, so it cannot be a step: a workflow never stores a secret.")} />}
                <FormGroup label={t("Limit to hosts")} fieldId={`wf-${step.id}-limit`}>
                    <TextInput id={`wf-${step.id}-limit`} value={step.limit} placeholder={template?.limit_hosts || "web01,web02"} onChange={(_e, value) => onChange({ ...step, limit: value })} />
                    {template && (
                        <FormHelperText><HelperText><HelperTextItem>
                            {inventory
                                ? t("Hosts or groups of {{inventory}}, to narrow the job template's; empty for all of them.", { inventory })
                                : t("Hosts or groups of the inventory chosen at launch, to narrow the job template's; empty for all of them.")}
                        </HelperTextItem></HelperText></FormHelperText>
                    )}
                </FormGroup>
                <SurveyAnswers survey={surveyOf(template)} answers={step.answers} onChange={answers => onChange({ ...step, answers })} />
                <ApprovalModeField id={`wf-${step.id}-approval`} value={step.approval} floor={template?.approval ?? "none"}
                    onChange={approval => onChange({ ...step, approval })} />
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

function WorkflowModal({ workflow, choices, onClose, onDone }: { workflow?: Workflow; choices: Choices; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const templates = choices.templates;
    const [name, setName] = useState(workflow?.name ?? "");
    const [description, setDescription] = useState(workflow?.description ?? "");
    const [scope, setScope] = useState<StepScope>({ project_id: workflow?.project_id ?? "", inventory_id: workflow?.inventory_id ?? "" });
    const inventories = new Map(choices.inventories.map(item => [item.id, item.name]));
    const projects = new Map(choices.projects.map(item => [item.id, item.name]));
    const [steps, setSteps] = useState<StepDraft[]>(() => toDrafts(workflow, templates));

    const invalid = !name.trim() || steps.some(step => {
        const template = templates.find(item => item.id === step.template_id);
        return !template || !inScope(template, scope) || step.after.some(link => !link.from)
            || surveyOf(template).some(field => answerProblem(field, step.answers[field.name]))
            || (template.survey ?? []).some(field => field.type === "secret" && field.required);
    });

    async function submit() {
        const nodes = steps.map(step => ({
            id: step.id, name: step.name.trim(), template_id: step.template_id, limit: step.limit.trim(),
            approval: step.approval,
            answers: collectAnswers(surveyOf(templates.find(item => item.id === step.template_id)), step.answers),
        }));
        const edges: WorkflowEdge[] = steps.flatMap(step => step.after.map(link => ({ from: link.from, to: step.id, condition: link.condition })));
        const params = { name, description, project_id: scope.project_id || null, inventory_id: scope.inventory_id || null, nodes, edges };
        await call(workflow ? "workflow.update" : "workflow.create", workflow ? { id: workflow.id, ...params } : params);
        onDone();
        onClose();
    }

    function remove(id: string) {
        setSteps(steps.filter(step => step.id !== id).map(step => ({ ...step, after: step.after.filter(link => link.from !== id) })));
    }

    return (
        <FormModal title={workflow ? t("Edit workflow") : t("New workflow")} submitLabel={t("Save")} onSubmit={submit} onClose={onClose} guardClose isDisabled={invalid}>
            <FormGroup label={t("Name")} isRequired fieldId="wf-name">
                <TextInput id="wf-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("Description")} fieldId="wf-description">
                <TextArea id="wf-description" value={description} onChange={(_e, value) => setDescription(value)} rows={2} />
            </FormGroup>
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("Project")} fieldId="wf-project">
                        <FormSelect id="wf-project" value={scope.project_id} onChange={(_e, value) => setScope({ ...scope, project_id: value })}>
                            <FormSelectOption value="" label={t("Any project")} />
                            {choices.projects.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Inventory")} fieldId="wf-inventory">
                        <FormSelect id="wf-inventory" value={scope.inventory_id} onChange={(_e, value) => setScope({ ...scope, inventory_id: value })}>
                            <FormSelectOption value="" label={t("Any inventory")} />
                            {choices.inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
            </Grid>
            <FormHelperText><HelperText><HelperTextItem>
                {t("With a project, only its job templates can be steps; with an inventory, every step runs on it, and only job templates that allow it can be steps. Without an inventory, one may be chosen at launch.")}
            </HelperTextItem></HelperText></FormHelperText>
            <Content component="small">{t("Each step passes on to the steps after it the variables its playbook sets with set_stats (with per_host: false). They are kept in clear with the run: not for secrets.")}</Content>
            {steps.length > 1 && (
                <FormGroup label={t("Steps in order")} fieldId="wf-shape" role="group">
                    <StepColumns columns={stepColumns(steps,
                        steps.flatMap(step => step.after.filter(link => link.from).map(link => ({ from: link.from, to: step.id, condition: link.condition }))),
                        step => step.name || templates.find(item => item.id === step.template_id)?.name || step.id,
                        step => stepAsksApproval(step, id => templates.find(item => item.id === id)))} />
                </FormGroup>
            )}
            {steps.map(step => (
                <StepEditor key={step.id} step={step} steps={steps} templates={templates} scope={scope} inventories={inventories} projects={projects}
                    onChange={changed => setSteps(steps.map(item => item.id === step.id ? changed : item))}
                    onRemove={steps.length > 1 ? () => remove(step.id) : undefined} />
            ))}
            <div>
                <Button variant="secondary" onClick={() => {
                    const last = steps[steps.length - 1];
                    const id = nextStepId(steps.map(step => step.id));
                    setSteps([...steps, { id, name: "", template_id: "", limit: "", approval: "none", answers: {}, after: last ? [{ from: last.id, condition: "success" }] : [] }]);
                }}>{t("Add a step")}</Button>
            </div>
        </FormModal>
    );
}

// The filters and the grouping above the list of workflows.
function WorkflowsToolbar({ view, onChange, projects, inventories, groups, shown, total }: {
    view: WorkflowsView;
    onChange: (view: WorkflowsView) => void;
    // Those that some workflow uses.
    projects: { id: string; name: string }[];
    inventories: { id: string; name: string }[];
    groups: WorkflowGroup[];
    shown: number;
    total: number;
}) {
    const { t } = useTranslation();
    const outcomeNames = useOutcomeNames();
    const set = (change: Partial<WorkflowsView>) => onChange({ ...view, ...change });
    const traitNames: Record<Exclude<WorkflowTrait, "">, string> = {
        approval: t("With approval"), choose: t("Inventory chosen at launch"), scheduled: t("Scheduled"), unscheduled: t("Not scheduled"),
    };
    const nameIn = (items: { id: string; name: string }[], id: string) => items.find(item => item.id === id)?.name ?? "";
    const allOpen = groups.every(group => isWorkflowGroupOpen(view, group));
    const chip = (key: string, text: string, clear: Partial<WorkflowsView>) => ({ key, text, onClear: () => set(clear) });
    const chips = [
        ...(view.search.trim() ? [chip("search", t("Text: {{text}}", { text: view.search.trim() }), { search: "" })] : []),
        ...(view.project ? [chip("project", t("Project: {{value}}", { value: nameIn(projects, view.project) }), { project: "" })] : []),
        ...(view.inventory ? [chip("inventory", t("Inventory: {{value}}", { value: nameIn(inventories, view.inventory) }), { inventory: "" })] : []),
        ...(view.trait ? [chip("trait", traitNames[view.trait], { trait: "" })] : []),
        ...(view.outcome ? [chip("outcome", outcomeNames[view.outcome], { outcome: "" })] : []),
    ];

    return (
        <div style={{ marginBlockEnd: "1rem" }}>
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                <FlexItem style={{ width: "18rem" }}>
                    <SearchInput aria-label={t("Filter workflows")} placeholder={t("Name, description, step, project, inventory…")} value={view.search}
                        onChange={(_e, value) => set({ search: value })} onClear={() => set({ search: "" })} />
                </FlexItem>
                {projects.length > 1 && (
                    <FlexItem style={{ width: "12rem" }}>
                        <FormSelect aria-label={t("Project")} value={view.project} onChange={(_e, value) => set({ project: value })}>
                            <FormSelectOption value="" label={t("Every project")} />
                            {projects.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FlexItem>
                )}
                {inventories.length > 1 && (
                    <FlexItem style={{ width: "12rem" }}>
                        <FormSelect aria-label={t("Inventory")} value={view.inventory} onChange={(_e, value) => set({ inventory: value })}>
                            <FormSelectOption value="" label={t("Every inventory")} />
                            {inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FlexItem>
                )}
                <FlexItem style={{ width: "14rem" }}>
                    <FormSelect aria-label={t("Filter by feature")} value={view.trait} onChange={(_e, value) => set({ trait: value as WorkflowTrait })}>
                        <FormSelectOption value="" label={t("Any feature")} />
                        {Object.entries(traitNames).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                    </FormSelect>
                </FlexItem>
                <FlexItem style={{ width: "13rem" }}>
                    <FormSelect aria-label={t("Filter by last run")} value={view.outcome} onChange={(_e, value) => set({ outcome: value as WorkflowsView["outcome"] })}>
                        <FormSelectOption value="" label={t("Any outcome")} />
                        {OUTCOMES.map(item => <FormSelectOption key={item} value={item} label={outcomeNames[item]} />)}
                    </FormSelect>
                </FlexItem>
                <GroupByChoice id="wf-group-by" value={view.groupBy} onChange={groupBy => set({ groupBy })}
                    options={[["inventory", t("Inventory")], ["project", t("Project")], ["outcome", t("Last run")], ["none", t("No grouping")]]}
                    groups={groups.length} allOpen={allOpen} onToggleAll={() => onChange(withWorkflowGroupsOpen(view, groups, !allOpen))} />
            </Flex>
            <FilterChips chips={chips} onClearAll={() => onChange(withoutWorkflowFilters(view))} shown={shown} total={total} />
        </div>
    );
}

export function WorkflowsPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const outcomeNames = useOutcomeNames();
    const [stored, setView] = useStoredView("workflows-view", readWorkflowsView);
    const list = useLoad(() => call<{ workflows: Workflow[] }>("workflow.list"));
    const templates = useLoad(() => call<{ templates: Template[] }>("template.list"));
    const schedules = useLoad(() => call<{ schedules: Schedule[] }>("schedule.list"));
    const places = useLoad(() => Promise.all([
        call<{ projects: Project[] }>("project.list"),
        call<{ inventories: Inventory[] }>("inventory.list"),
    ]).then(([p, i]) => ({ projects: p.projects, inventories: i.inventories })));
    const [editing, setEditing] = useState<Workflow | "new" | null>(null);
    const [launching, setLaunching] = useState<{ workflow: Workflow; inventory?: string } | null>(null);
    const [deleting, setDeleting] = useState<Workflow | null>(null);
    const templateById = new Map((templates.data?.templates ?? []).map(item => [item.id, item]));
    const projectNames = new Map((places.data?.projects ?? []).map(item => [item.id, item.name]));
    const inventoryNames = new Map((places.data?.inventories ?? []).map(item => [item.id, item.name]));
    const scheduleCount = new Map<string, number>();
    for (const { workflow_id: id } of schedules.data?.schedules ?? [])
        if (id) scheduleCount.set(id, (scheduleCount.get(id) ?? 0) + 1);
    // A starter is used by no run: it is not offered.
    const choices: Choices | null = templates.data && places.data ? {
        templates: templates.data.templates,
        projects: places.data.projects.filter(one => !one.starter),
        inventories: places.data.inventories.filter(one => !one.starter),
    } : null;
    const names: WorkflowNames = {
        template: id => templateById.get(id),
        project: id => projectNames.get(id) ?? "",
        inventory: id => id === EACH_STEPS_OWN ? t("Each step's own inventory") : inventoryNames.get(id) ?? "",
        schedules: id => scheduleCount.get(id) ?? 0,
        inventories: (choices?.inventories ?? []).map(one => one.id),
    };
    // A last run not over yet, on any inventory, is followed, so that its outcome shows.
    usePolling(list.reload, 3000, list.data?.workflows.some(item => Object.values(item.last_runs ?? {}).concat(item.last_run ?? [])
        .some(run => isActiveRun(run.status))) ?? false);

    // A workflow on no inventory of its own may be launched on one its steps all allow.
    function inventoryChoices(workflow: Workflow): string[] {
        if (workflow.inventory_id) return [];
        const usable = (choices?.inventories ?? []).map(one => one.id);
        return workflow.inventory_ids === null ? usable : workflow.inventory_ids.filter(id => usable.includes(id));
    }

    return (
        <>
            <SectionHeader title={t("Workflows")}>
                <Button variant="secondary" onClick={list.reload}>{t("Refresh")}</Button>
                {can("workflow.create") && <Button variant="primary" onClick={() => setEditing("new")} isDisabled={!choices}>{t("New workflow")}</Button>}
            </SectionHeader>
            <ListState loaded={list} isEmpty={data => data.workflows.length === 0}
                emptyTitle={t("No workflows yet")}
                emptyBody={t("A workflow runs job templates one after the other: each step starts by how the steps before it ended.")}>
                {data => {
                    const used = (ids: string[], name: (id: string) => string) => [...new Set(ids)]
                        .map(id => ({ id, name: name(id) })).sort((a, b) => a.name.localeCompare(b.name));
                    const projects = used(data.workflows.flatMap(item => workflowProjects(item, names)), names.project);
                    const inventories = used(data.workflows.flatMap(item => workflowInventories(item, names.inventories))
                        .filter(id => id !== EACH_STEPS_OWN), names.inventory);
                    // A stored project or inventory that no workflow uses any more is not a
                    // filter, nor is the only one, whose select is not shown.
                    const view = {
                        ...stored,
                        project: projects.length > 1 && projects.some(item => item.id === stored.project) ? stored.project : "",
                        inventory: inventories.length > 1 && inventories.some(item => item.id === stored.inventory) ? stored.inventory : "",
                    };
                    const { groups, shown } = listWorkflows(data.workflows, view, names);
                    const grouped = view.groupBy !== "none";
                    const width = grouped ? 6 : 5;
                    const columns: WorkflowSort[] = ["name", "last_run"];
                    const sortBy = (column: WorkflowSort) => ({
                        columnIndex: columns.indexOf(column),
                        sortBy: { index: columns.indexOf(view.sort), direction: view.descending ? "desc" as const : "asc" as const },
                        onSort: (_e: unknown, _index: number, direction: "asc" | "desc") => setView({ ...view, sort: column, descending: direction === "desc" }),
                    });
                    const title = (group: WorkflowGroup) => view.groupBy === "outcome" ? outcomeNames[group.key as Outcome] : group.name;

                    const runLink = (run: { id: string; status: Run["status"]; created_at: number }) => (
                        <Button variant="link" isInline onClick={() => openRun(run.id)}>
                            <RunStatusLabel status={run.status} /> {formatTime(run.created_at, i18n.language)}
                        </Button>
                    );
                    // Under an inventory, the last run there, or the last elsewhere, muted.
                    const lastRunCell = (workflow: Workflow, where?: string) => {
                        const run = lastWorkflowRun(workflow, where);
                        const any = workflow.last_run;
                        if (where !== undefined && !run && any) {
                            const on = Object.entries(workflow.last_runs ?? {}).find(([, one]) => one.id === any.id)?.[0];
                            const inventory = on === undefined ? "" : on === EACH_STEPS_OWN ? t("the inventory of each step") : names.inventory(on);
                            return (
                                <>
                                    {t("Never here")}
                                    <div className="pf-v6-u-font-size-sm pf-v6-u-text-color-subtle">
                                        {t("Last on {{inventory}}:", { inventory: inventory || t("an inventory deleted") })} {runLink(any)}
                                    </div>
                                </>
                            );
                        }
                        return run ? runLink(run) : t("Never");
                    };

                    // where: the inventory of the group it is listed under, if grouped by inventory.
                    const row = (workflow: Workflow, open: boolean, where?: string) => {
                        const count = names.schedules(workflow.id);
                        const asks = workflow.nodes.some(node => stepAsksApproval(node, names.template));
                        return (
                            <Tr key={workflow.id} isExpanded={grouped ? open : undefined}>
                                {grouped && <Td />}
                                <Td dataLabel={t("Name")}>
                                    {workflow.name}
                                    {asks && <Label isCompact color="purple" style={{ marginInlineStart: "0.5rem" }}>{t("Approval")}</Label>}
                                    {workflow.description && <Content component="small" style={{ display: "block" }}>{workflow.description}</Content>}
                                    {(workflow.project_id || workflow.inventory_id) && (
                                        <Flex gap={{ default: "gapSm" }} style={{ marginBlockStart: "0.25rem" }}>
                                            {workflow.project_id && <Label isCompact color="blue">{t("Project")}: {names.project(workflow.project_id)}</Label>}
                                            {workflow.inventory_id && <Label isCompact color="teal">{t("Inventory")}: {names.inventory(workflow.inventory_id)}</Label>}
                                        </Flex>
                                    )}
                                </Td>
                                <Td dataLabel={t("Steps")}>
                                    <StepColumns columns={stepColumns(workflow.nodes, workflow.edges, node => stepName(node, names),
                                        node => stepAsksApproval(node, names.template))} />
                                </Td>
                                <Td dataLabel={t("Last run")}>{lastRunCell(workflow, where)}</Td>
                                <Td dataLabel={t("Schedules")}>
                                    {count > 0 && <Button variant="link" isInline onClick={() => cockpit.location.go(["schedules"])}>{count}</Button>}
                                </Td>
                                <Td isActionCell>
                                    <Flex gap={{ default: "gapSm" }} flexWrap={{ default: "nowrap" }} justifyContent={{ default: "justifyContentFlexEnd" }}>
                                        {can("workflow.launch") && <Button variant="secondary" size="sm"
                                            onClick={() => setLaunching({ workflow, inventory: where })}>{t("Launch")}</Button>}
                                        {can("workflow.update") && (
                                            <ActionsColumn items={[
                                                { title: t("Edit"), onClick: () => setEditing(workflow), isDisabled: !choices },
                                                { title: t("Delete"), onClick: () => setDeleting(workflow) },
                                            ]} />
                                        )}
                                    </Flex>
                                </Td>
                            </Tr>
                        );
                    };

                    return (
                        <>
                            <WorkflowsToolbar view={view} onChange={setView} projects={projects} inventories={inventories} groups={groups}
                                shown={shown} total={data.workflows.length} />
                            {shown === 0 ? (
                                <NothingMatches title={t("No workflow matches the filters")} onClearAll={() => setView(withoutWorkflowFilters(view))} />
                            ) : (
                                <Table variant="compact" aria-label={t("Workflows")}>
                                    <Thead>
                                        <Tr>
                                            {grouped && <Th screenReaderText={t("Group")} />}
                                            <Th sort={sortBy("name")}>{t("Name")}</Th><Th>{t("Steps")}</Th>
                                            <Th sort={sortBy("last_run")}>{t("Last run")}</Th><Th>{t("Schedules")}</Th>
                                            <Th screenReaderText={t("Actions")} />
                                        </Tr>
                                    </Thead>
                                    {grouped ? groups.map(group => {
                                        const open = isWorkflowGroupOpen(view, group);
                                        return (
                                            <Tbody key={group.key} isExpanded={open}>
                                                <GroupRow title={title(group)} count={t("workflows_count", { count: group.items.length })} open={open} width={width}
                                                    onToggle={() => setView(withWorkflowGroupsOpen(view, [group], !open))} />
                                                {group.items.map(workflow => row(workflow, open, view.groupBy === "inventory" ? group.key : undefined))}
                                            </Tbody>
                                        );
                                    }) : <Tbody>{groups[0].items.map(workflow => row(workflow, true))}</Tbody>}
                                </Table>
                            )}
                        </>
                    );
                }}
            </ListState>
            {editing && choices && (
                <WorkflowModal workflow={editing === "new" ? undefined : editing} choices={choices}
                    onClose={() => setEditing(null)} onDone={list.reload} />
            )}
            {launching && (
                <LaunchWorkflowModal workflow={launching.workflow} inventories={inventoryChoices(launching.workflow)} names={inventoryNames}
                    initial={launching.inventory} onClose={() => setLaunching(null)} onLaunched={list.reload}
                    steps={<StepColumns columns={stepColumns(launching.workflow.nodes, launching.workflow.edges, node => stepName(node, names),
                        node => stepAsksApproval(node, names.template))} />} />
            )}
            {deleting && (
                <ConfirmDelete what={t("Its past runs stay.")} name={deleting.name}
                    onDelete={async () => { await call("workflow.delete", { id: deleting.id }); list.reload(); setDeleting(null); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}

// The inventory every step of a run is to be on, or each step's own.
// initial: the inventory of the group it was launched from ("" each step's own).
// With no inventory to choose it is still asked, with the steps in sight:
// a launch from the list never starts a chain of runs at one click.
function LaunchWorkflowModal({ workflow, inventories, names, initial, steps, onClose, onLaunched }: {
    workflow: Workflow; inventories: string[]; names: Map<string, string>; initial?: string; steps: React.ReactNode;
    onClose: () => void; onLaunched: () => void;
}) {
    const { t } = useTranslation();
    const [inventoryId, setInventoryId] = useState(initial && inventories.includes(initial) ? initial : "");
    const needed = workflow.needs_inventory;

    async function submit() {
        const run = await call<Run>("workflow.launch", { id: workflow.id, ...(inventoryId ? { inventory_id: inventoryId } : {}) });
        announceRun(run.id, run.title, run.status);
        onLaunched();
        onClose();
    }

    return (
        <FormModal title={t("Launch {{name}}", { name: workflow.name })} submitLabel={t("Launch")} onSubmit={submit} onClose={onClose}
            isDisabled={needed && !inventoryId}>
            {inventories.length > 0
                ? (
                    <FormGroup label={t("Inventory")} isRequired={needed} fieldId="wfl-inventory">
                        <FormSelect id="wfl-inventory" value={inventoryId} onChange={(_e, value) => setInventoryId(value)}>
                            {needed
                                ? !inventoryId && <FormSelectOption value="" label={t("Choose an inventory")} isPlaceholder />
                                : <FormSelectOption value="" label={t("Each step's own")} />}
                            {inventories.map(id => <FormSelectOption key={id} value={id} label={names.get(id) ?? id} />)}
                        </FormSelect>
                        <FormHelperText><HelperText><HelperTextItem>{t("Every step runs on the inventory chosen; the list has those all of its job templates allow.")}</HelperTextItem></HelperText></FormHelperText>
                    </FormGroup>
                )
                : (
                    <Content component="p">
                        {workflow.inventory_id
                            ? t("Every step runs on {{inventory}}.", { inventory: names.get(workflow.inventory_id) ?? workflow.inventory_id })
                            : t("Each step runs on the inventory of its job template.")}
                    </Content>
                )}
            <FormGroup label={t("Steps in order")} fieldId="wfl-steps" role="group">{steps}</FormGroup>
        </FormModal>
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
            announceRun(run.id, run.title, run.status);
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {data.resumed_from && (
                <Content component="p">
                    {t("Resumed from")} <Button variant="link" isInline onClick={() => openRun(data.resumed_from!)}>{data.resumed_from}</Button>
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
                                            ? <Button variant="link" isInline onClick={() => openRun(node.run!.id)}>{name(node)}</Button>
                                            : name(node)}
                                    </CardTitle>
                                    <CardBody>
                                        <StepStateLabel node={node} />
                                        {node.carried && <Label isCompact color="blue" style={{ marginInlineStart: "0.5rem" }}>{t("Done before")}</Label>}
                                        {node.approval !== "none" && <span style={{ marginInlineStart: "0.5rem" }}><ApprovalModeLabel mode={node.approval} color="orange" /></span>}
                                        {node.inventory && <Content component="small" style={{ display: "block" }}>{t("Inventory")}: {node.inventory}</Content>}
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
