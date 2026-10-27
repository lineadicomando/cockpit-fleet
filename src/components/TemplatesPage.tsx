import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Checkbox, Flex, FlexItem, FormGroup, FormHelperText, FormSelect, FormSelectOption, Grid, GridItem, HelperText, HelperTextItem, Label, TextInput } from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { answerSecret, call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import { answerProblem, collectAnswers, draftProblem, fromDraft, initialAnswers, secretAnswers, toDraft } from "../lib/survey.ts";
import { playbookChoices } from "../lib/files.ts";
import { formatTime } from "../lib/format.ts";
import {
    filterTemplates, groupTemplates, isTemplateGroupOpen, OUTCOMES, readTemplatesView, sortTemplates, withoutTemplateFilters, withTemplateGroupsOpen,
    type Outcome, type TemplateGroup, type TemplateNames, type TemplateSort, type TemplatesView, type Trait,
} from "../lib/templates.ts";
import type { Credential, Inventory, Project, ProjectPaths, Run, Schedule, Template } from "../lib/types.ts";
import { CodeEditor } from "./CodeEditor.tsx";
import { AtTerminal, ConfirmDelete, FormModal, ListState, RunStatusLabel, SectionHeader } from "./common.tsx";
import { launchCommand } from "../lib/terminal.ts";
import { SurveyAnswers, SurveyEditor } from "./SurveyEditor.tsx";
import { PathInput } from "./PathInput.tsx";
import { FilterChips, GroupByChoice, GroupRow, NothingMatches, useStoredView } from "./ListView.tsx";
import { ActionsColumn, SearchInput } from "./pf.tsx";

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
    const [vaultIds, setVaultIds] = useState<string[]>(template?.vault_credential_ids ?? []);
    const [limit, setLimit] = useState(template?.limit_hosts ?? "");
    const [tags, setTags] = useState((template?.tags ?? []).join(", "));
    const [skipTags, setSkipTags] = useState((template?.skip_tags ?? []).join(", "));
    const [extraVars, setExtraVars] = useState(template && Object.keys(template.extra_vars).length ? JSON.stringify(template.extra_vars, null, 2) : "");
    const [forks, setForks] = useState(template?.forks ? String(template.forks) : "");
    const [verbosity, setVerbosity] = useState(String(template?.verbosity ?? 0));
    const [become, setBecome] = useState(template?.become ?? false);
    const [needsApproval, setNeedsApproval] = useState(template?.needs_approval ?? false);
    const [survey, setSurvey] = useState(() => (template?.survey ?? []).map(toDraft));
    const files = useLoad(() => projectId
        ? call<ProjectPaths>("project.files", { id: projectId }).catch(() => ({ files: [], directories: [] }))
        : Promise.resolve<ProjectPaths>({ files: [], directories: [] }));

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
            machine_credential_id: machineId, vault_credential_ids: vaultIds,
            limit_hosts: limit.trim(), tags: words(tags), skip_tags: words(skipTags), extra_vars: vars,
            survey: survey.map(fromDraft),
            ...(forks ? { forks: Number(forks) } : {}), verbosity: Number(verbosity), become,
            needs_approval: needsApproval,
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
            isDisabled={!name.trim() || !projectId || !playbook.trim() || !inventoryId || survey.some(draft => draftProblem(draft, survey))}>
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
                        <PathInput id="tpl-playbook" label={t("Playbook")} value={playbook} onChange={setPlaybook} placeholder="playbooks/site.yaml"
                            choices={playbookChoices(files.data?.files ?? [])} />
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
                    <FormGroup label={t("Vault credentials")} role="group" fieldId="tpl-vault">
                        {ofKind("vault").length === 0 && <span>{t("None defined")}</span>}
                        {ofKind("vault").map(credential => (
                            <Checkbox key={credential.id} id={`tpl-vault-${credential.id}`}
                                label={credential.vault_id ? `${credential.name} (${credential.vault_id})` : credential.name}
                                isChecked={vaultIds.includes(credential.id)}
                                onChange={(_e, checked) => setVaultIds(current => checked
                                    ? [...current, credential.id]
                                    : current.filter(id => id !== credential.id))} />
                        ))}
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
            <FormGroup fieldId="tpl-approval">
                <Checkbox id="tpl-approval" label={t("Each run needs approval")} isChecked={needsApproval} onChange={(_e, value) => setNeedsApproval(value)}
                    description={t("A run waits until a member of cockpit-fleet-approver, or an administrator, other than who launched it approves it. An administrator may approve their own.")} />
            </FormGroup>
            <FormGroup label={t("Extra variables")} fieldId="tpl-vars" role="group">
                <CodeEditor className="fleet-code-field fleet-code-short" label={t("Extra variables")} path="vars.json" value={extraVars} onChange={setExtraVars} placeholder={'{\n  "version": "1.2"\n}'} />
                <FormHelperText><HelperText><HelperTextItem>{t("A JSON object. Not for secrets: they would be stored in clear.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            <SurveyEditor drafts={survey} onChange={setSurvey} />
        </FormModal>
    );
}

// limit: what the hosts are narrowed to at first, such as the host whose updates are applied.
export function LaunchModal({ template, limit: initialLimit = "", onClose }: { template: Template; limit?: string; onClose: () => void }) {
    const { t } = useTranslation();
    const [limit, setLimit] = useState(initialLimit);
    const [check, setCheck] = useState(false);
    const [answers, setAnswers] = useState(() => initialAnswers(template.survey));

    async function submit() {
        const given: Record<string, unknown> = collectAnswers(template.survey, answers);
        // A secret goes to the helper that encrypts it; the launch carries a ticket.
        for (const [name, value] of Object.entries(secretAnswers(template.survey, answers)))
            given[name] = await answerSecret(value);
        const run = await call<Run>("run.start", { template_id: template.id, limit: limit.trim(), check, answers: given });
        cockpit.location.go(["runs", run.id]);
    }

    return (
        <FormModal title={t("Launch {{name}}", { name: template.name })} submitLabel={check ? t("Launch dry run") : t("Launch")} onSubmit={submit} onClose={onClose}
            isDisabled={template.survey.some(field => answerProblem(field, answers[field.name]))}>
            <SurveyAnswers survey={template.survey} answers={answers} onChange={setAnswers} />
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
            <AtTerminal text={launchCommand(template.name, collectAnswers(template.survey, answers), limit, check)} />
        </FormModal>
    );
}

function useOutcomeNames(): Record<Outcome, string> {
    const { t } = useTranslation();
    return {
        failed: t("Last run failed"), active: t("Last run under way"), canceled: t("Last run canceled"),
        successful: t("Last run successful"), never: t("Never launched"),
    };
}

interface Listed {
    templates: Template[];
    schedules: Schedule[];
    projects: Project[];
    inventories: Inventory[];
}

function loadListed(): Promise<Listed> {
    return Promise.all([
        call<{ templates: Template[] }>("template.list"),
        call<{ schedules: Schedule[] }>("schedule.list"),
        call<{ projects: Project[] }>("project.list"),
        call<{ inventories: Inventory[] }>("inventory.list"),
    ]).then(([templates, schedules, projects, inventories]) => ({
        templates: templates.templates, schedules: schedules.schedules, projects: projects.projects, inventories: inventories.inventories,
    }));
}

// The filters and the grouping above the list of templates.
function TemplatesToolbar({ view, onChange, projects, inventories, groups, shown, total }: {
    view: TemplatesView;
    onChange: (view: TemplatesView) => void;
    // Those that some template uses.
    projects: { id: string; name: string }[];
    inventories: { id: string; name: string }[];
    groups: TemplateGroup[];
    shown: number;
    total: number;
}) {
    const { t } = useTranslation();
    const outcomeNames = useOutcomeNames();
    const set = (change: Partial<TemplatesView>) => onChange({ ...view, ...change });
    const traitNames: Record<Exclude<Trait, "">, string> = {
        approval: t("With approval"), survey: t("With questions"), scheduled: t("Scheduled"), unscheduled: t("Not scheduled"),
    };
    const nameIn = (items: { id: string; name: string }[], id: string) => items.find(item => item.id === id)?.name ?? "";
    const allOpen = groups.every(group => isTemplateGroupOpen(view, group));
    const chip = (key: string, text: string, clear: Partial<TemplatesView>) => ({ key, text, onClear: () => set(clear) });
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
                    <SearchInput aria-label={t("Filter job templates")} placeholder={t("Name, playbook, project, inventory, tag…")} value={view.search}
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
                <FlexItem style={{ width: "12rem" }}>
                    <FormSelect aria-label={t("Filter by feature")} value={view.trait} onChange={(_e, value) => set({ trait: value as Trait })}>
                        <FormSelectOption value="" label={t("Any feature")} />
                        {Object.entries(traitNames).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                    </FormSelect>
                </FlexItem>
                <FlexItem style={{ width: "13rem" }}>
                    <FormSelect aria-label={t("Filter by last run")} value={view.outcome} onChange={(_e, value) => set({ outcome: value as TemplatesView["outcome"] })}>
                        <FormSelectOption value="" label={t("Any outcome")} />
                        {OUTCOMES.map(item => <FormSelectOption key={item} value={item} label={outcomeNames[item]} />)}
                    </FormSelect>
                </FlexItem>
                <GroupByChoice id="tpl-group-by" value={view.groupBy} onChange={groupBy => set({ groupBy })}
                    options={[["project", t("Project")], ["inventory", t("Inventory")], ["outcome", t("Last run")], ["none", t("Nothing")]]}
                    groups={groups.length} allOpen={allOpen} onToggleAll={() => onChange(withTemplateGroupsOpen(view, groups, !allOpen))} />
            </Flex>
            <FilterChips chips={chips} onClearAll={() => onChange(withoutTemplateFilters(view))} shown={shown} total={total} />
        </div>
    );
}

export function TemplatesPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const outcomeNames = useOutcomeNames();
    const [stored, setView] = useStoredView("templates-view", readTemplatesView);
    const list = useLoad(loadListed);
    const choices = useLoad(loadChoices);
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<Template | null>(null);
    const [launching, setLaunching] = useState<Template | null>(null);
    const [deleting, setDeleting] = useState<Template | null>(null);

    function reload() {
        list.reload();
    }

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
                {data => {
                    const projectName = new Map(data.projects.map(item => [item.id, item.name]));
                    const inventoryName = new Map(data.inventories.map(item => [item.id, item.name]));
                    const scheduleCount = new Map<string, number>();
                    for (const schedule of data.schedules)
                        scheduleCount.set(schedule.template_id, (scheduleCount.get(schedule.template_id) ?? 0) + 1);
                    const names: TemplateNames = {
                        project: id => projectName.get(id) ?? "",
                        inventory: id => inventoryName.get(id) ?? "",
                        schedules: id => scheduleCount.get(id) ?? 0,
                    };
                    const used = (ids: string[], name: (id: string) => string) => [...new Set(ids)]
                        .map(id => ({ id, name: name(id) })).sort((a, b) => a.name.localeCompare(b.name));
                    const projects = used(data.templates.map(item => item.project_id), names.project);
                    const inventories = used(data.templates.map(item => item.inventory_id), names.inventory);
                    // A stored project or inventory that no template uses any more is not a filter.
                    const view = {
                        ...stored,
                        project: projects.some(item => item.id === stored.project) ? stored.project : "",
                        inventory: inventories.some(item => item.id === stored.inventory) ? stored.inventory : "",
                    };
                    const shown = filterTemplates(data.templates, view, names);
                    const groups = groupTemplates(sortTemplates(shown, view.sort, view.descending, names), view.groupBy, names);
                    const grouped = view.groupBy !== "none";
                    const width = grouped ? 9 : 8;
                    const columns: TemplateSort[] = ["name", "project", "inventory", "last_run"];
                    const sortBy = (column: TemplateSort) => ({
                        columnIndex: columns.indexOf(column),
                        sortBy: { index: columns.indexOf(view.sort), direction: view.descending ? "desc" as const : "asc" as const },
                        onSort: (_e: unknown, _index: number, direction: "asc" | "desc") => setView({ ...view, sort: column, descending: direction === "desc" }),
                    });
                    const title = (group: TemplateGroup) => view.groupBy === "outcome" ? outcomeNames[group.key as Outcome] : group.name;

                    const row = (template: Template, open: boolean) => {
                        const count = names.schedules(template.id);
                        return (
                            <Tr key={template.id} isExpanded={grouped ? open : undefined}>
                                {grouped && <Td />}
                                <Td dataLabel={t("Name")}>
                                    {template.name}
                                    <Flex gap={{ default: "gapXs" }} style={{ display: "inline-flex", marginInlineStart: "0.5rem" }}>
                                        {template.needs_approval && <Label isCompact color="purple">{t("Approval")}</Label>}
                                        {template.survey.length > 0 && <Label isCompact color="blue">{t("Questions")}</Label>}
                                        {template.become && <Label isCompact color="grey">become</Label>}
                                    </Flex>
                                </Td>
                                <Td dataLabel={t("Project")}>{names.project(template.project_id)}</Td>
                                <Td dataLabel={t("Playbook")}><code>{template.playbook}</code></Td>
                                <Td dataLabel={t("Inventory")}>{names.inventory(template.inventory_id)}</Td>
                                <Td dataLabel={t("Last run")}>
                                    {template.last_run
                                        ? (
                                            <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", template.last_run!.id])}>
                                                <RunStatusLabel status={template.last_run.status} /> {formatTime(template.last_run.created_at, i18n.language)}
                                            </Button>
                                        )
                                        : t("Never")}
                                </Td>
                                <Td dataLabel={t("Schedules")}>
                                    {count > 0 && <Button variant="link" isInline onClick={() => cockpit.location.go(["schedules"])}>{count}</Button>}
                                </Td>
                                <Td isActionCell style={{ whiteSpace: "nowrap" }}>
                                    {can("run.start") && <Button variant="secondary" size="sm" onClick={() => setLaunching(template)}>{t("Launch")}</Button>}
                                </Td>
                                <Td isActionCell>
                                    {can("template.update") && (
                                        <ActionsColumn items={[
                                            { title: t("Edit"), onClick: () => setEditing(template), isDisabled: !choices.data },
                                            { title: t("Delete"), onClick: () => setDeleting(template) },
                                        ]} />
                                    )}
                                </Td>
                            </Tr>
                        );
                    };

                    return (
                        <>
                            <TemplatesToolbar view={view} onChange={setView} projects={projects} inventories={inventories} groups={groups}
                                shown={shown.length} total={data.templates.length} />
                            {shown.length === 0 ? (
                                <NothingMatches title={t("No job template matches the filters")} onClearAll={() => setView(withoutTemplateFilters(view))} />
                            ) : (
                                <Table variant="compact" aria-label={t("Job templates")}>
                                    <Thead>
                                        <Tr>
                                            {grouped && <Th screenReaderText={t("Group")} />}
                                            <Th sort={sortBy("name")}>{t("Name")}</Th><Th sort={sortBy("project")}>{t("Project")}</Th><Th>{t("Playbook")}</Th>
                                            <Th sort={sortBy("inventory")}>{t("Inventory")}</Th><Th sort={sortBy("last_run")}>{t("Last run")}</Th><Th>{t("Schedules")}</Th>
                                            <Th screenReaderText={t("Launch")} /><Th screenReaderText={t("Actions")} />
                                        </Tr>
                                    </Thead>
                                    {grouped ? groups.map(group => {
                                        const open = isTemplateGroupOpen(view, group);
                                        return (
                                            <Tbody key={group.key} isExpanded={open}>
                                                <GroupRow title={title(group)} count={t("templates_count", { count: group.items.length })} open={open} width={width}
                                                    onToggle={() => setView(withTemplateGroupsOpen(view, [group], !open))} />
                                                {group.items.map(template => row(template, open))}
                                            </Tbody>
                                        );
                                    }) : <Tbody>{groups[0].items.map(template => row(template, true))}</Tbody>}
                                </Table>
                            )}
                        </>
                    );
                }}
            </ListState>
            {creating && choices.data && <TemplateModal choices={choices.data} onClose={() => setCreating(false)} onDone={reload} />}
            {editing && choices.data && <TemplateModal template={editing} choices={choices.data} onClose={() => setEditing(null)} onDone={reload} />}
            {launching && <LaunchModal template={launching} onClose={() => setLaunching(null)} />}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The job template is deleted. Its past runs stay in the history.")}
                    onDelete={async () => { await call("template.delete", { id: deleting.id }); setDeleting(null); reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
