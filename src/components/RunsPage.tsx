import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Card,
    CardBody,
    Content,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    ExpandableSection,
    Flex,
    FlexItem,
    FormSelect,
    FormSelectOption,
    Label,
    Tab,
    TabTitleText,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { parseAnsi } from "../lib/ansi.ts";
import { addEvents, changesByHost, countOutcomes, diffLines, type HostChanges, type TaskView } from "../lib/events.ts";
import { call } from "../lib/fleet.ts";
import { formatTime, runDuration } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Chunk, HostStats, Run, RunStatus } from "../lib/types.ts";
import { AtTerminal, ListState, RunStatusLabel, SectionHeader, message } from "./common.tsx";
import { runCommand } from "../lib/terminal.ts";
import { AdhocModal } from "./AdhocModal.tsx";
import { DecideButtons, PendingApprovals } from "./ApprovalsPage.tsx";
import { WorkflowSteps } from "./WorkflowsPage.tsx";
import { Answered } from "./SurveyEditor.tsx";
import { Alert, TabPanel, Tabs } from "./pf.tsx";

const isActive = (status: RunStatus) => status === "pending_approval" || status === "queued" || status === "running";
// Past this, only the end of the output is drawn; the whole log stays on the server.
const MAX_SHOWN = 400_000;

function useKinds() {
    const { t } = useTranslation();
    return { playbook: t("Playbook"), adhoc: t("Ad-hoc command"), inventory: t("Inventory reading"), sync: t("Project update"), syntax: t("Syntax check"), unpack: t("Archive upload"), galaxy: t("Collection installation"), check: t("Check cycle"), workflow: t("Workflow"), download: t("Download into the store"), copy: t("Copy within the store") } as Record<Run["kind"], string>;
}

export function RunsPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const kinds = useKinds();
    const [status, setStatus] = useState("");
    const [commanding, setCommanding] = useState(false);
    const [limit, setLimit] = useState(50);
    const list = useLoad(() => call<{ runs: Run[]; more: boolean }>("run.list", { limit, status }), [status, limit]);
    const anyActive = list.data?.runs.some(run => isActive(run.status)) ?? false;
    usePolling(list.reload, 3000, anyActive);
    const now = Date.now() / 1000;

    const statuses: Record<string, string> = {
        "": t("Any status"), pending_approval: t("Waiting for approval"), running: t("Running"), queued: t("Queued"),
        successful: t("Successful"), failed: t("Failed"), canceled: t("Canceled"), error: t("Error"),
        rejected: t("Rejected"), expired: t("Expired"),
    };

    return (
        <>
            {can("approval.list") && <PendingApprovals />}
            <SectionHeader title={t("Runs")}>
                <FormSelect value={status} onChange={(_e, value) => setStatus(value)} aria-label={t("Filter by status")} style={{ width: "12rem" }}>
                    {Object.entries(statuses).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                </FormSelect>
                <Button variant="secondary" onClick={list.reload}>{t("Refresh")}</Button>
                {can("run.adhoc") && <Button variant="primary" onClick={() => setCommanding(true)}>{t("Run a command")}</Button>}
            </SectionHeader>
            <ListState loaded={list} isEmpty={data => data.runs.length === 0}
                emptyTitle={status ? t("No runs with this status") : t("No runs yet")}
                emptyBody={t("Launch a job template to see its run here.")}>
                {data => (
                    <>
                        <Table variant="compact" aria-label={t("Runs")}>
                            <Thead>
                                <Tr><Th>{t("Run")}</Th><Th>{t("Status")}</Th><Th>{t("Type")}</Th><Th>{t("Launched by")}</Th><Th>{t("Started")}</Th><Th>{t("Duration")}</Th></Tr>
                            </Thead>
                            <Tbody>
                                {data.runs.map(run => (
                                    <Tr key={run.id} isClickable onRowClick={() => cockpit.location.go(["runs", run.id])}>
                                        <Td dataLabel={t("Run")}>
                                            <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", run.id])}>{run.title}</Button>
                                            {run.options.check && <Label isCompact color="blue" style={{ marginInlineStart: "0.5rem" }}>{t("Dry run")}</Label>}
                                        </Td>
                                        <Td dataLabel={t("Status")}>
                                            <RunStatusLabel status={run.status} />
                                            {run.waiting_for && <Content component="small" style={{ display: "block" }}>{t("After {{title}}", { title: run.waiting_for.title })}</Content>}
                                        </Td>
                                        <Td dataLabel={t("Type")}>{kinds[run.kind]}</Td>
                                        <Td dataLabel={t("Launched by")}>{run.created_by}</Td>
                                        <Td dataLabel={t("Started")}>{formatTime(run.started_at ?? run.created_at, i18n.language)}</Td>
                                        <Td dataLabel={t("Duration")}>{runDuration(run, now)}</Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                        {data.more && <Button variant="link" onClick={() => setLimit(limit + 50)}>{t("Show older runs")}</Button>}
                    </>
                )}
            </ListState>
            {commanding && <AdhocModal onClose={() => setCommanding(false)} />}
        </>
    );
}

export function Output({ text }: { text: string }) {
    const { t } = useTranslation();
    const box = useRef<HTMLPreElement>(null);
    const follow = useRef(true);
    const shown = text.length > MAX_SHOWN ? text.slice(text.indexOf("\n", text.length - MAX_SHOWN) + 1) : text;
    const spans = useMemo(() => parseAnsi(shown), [shown]);

    // Stay at the bottom while output arrives, unless the reader scrolled up.
    useEffect(() => {
        const element = box.current;
        if (element && follow.current) element.scrollTop = element.scrollHeight;
    }, [spans]);

    return (
        <>
            {shown.length < text.length && <Content component="small">{t("Only the last part of the output is shown.")}</Content>}
            <pre
                ref={box}
                className="fleet-log"
                onScroll={event => {
                    const element = event.currentTarget;
                    follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
                }}
            >
                {spans.map((span, index) => (
                    <span key={index} className={`${span.color ? `fleet-ansi-${span.color}` : ""}${span.bold ? " fleet-ansi-bold" : ""}`}>
                        {span.text}
                    </span>
                ))}
            </pre>
        </>
    );
}

function Tasks({ tasks }: { tasks: TaskView[] }) {
    const { t } = useTranslation();
    if (tasks.length === 0) return <Content component="p">{t("No task has started yet.")}</Content>;
    return (
        <Table variant="compact" aria-label={t("Tasks")}>
            <Thead><Tr><Th>{t("Task")}</Th><Th>{t("Hosts")}</Th><Th>{t("Problems")}</Th></Tr></Thead>
            <Tbody>
                {tasks.map(task => {
                    const counts = countOutcomes(task);
                    const problems = Object.entries(task.messages);
                    return (
                        <Tr key={task.uuid}>
                            <Td dataLabel={t("Task")}>
                                {task.name}
                                {task.play && <Content component="small" style={{ display: "block" }}>{task.play}</Content>}
                            </Td>
                            <Td dataLabel={t("Hosts")} style={{ whiteSpace: "nowrap" }}>
                                {counts.ok > 0 && <Label isCompact color="green">{t("ok {{n}}", { n: counts.ok })}</Label>}{" "}
                                {counts.changed > 0 && <Label isCompact color="orange">{t("changed {{n}}", { n: counts.changed })}</Label>}{" "}
                                {counts.failed > 0 && <Label isCompact color="red">{t("failed {{n}}", { n: counts.failed })}</Label>}{" "}
                                {counts.unreachable > 0 && <Label isCompact color="red">{t("unreachable {{n}}", { n: counts.unreachable })}</Label>}{" "}
                                {counts.skipped > 0 && <Label isCompact color="grey">{t("skipped {{n}}", { n: counts.skipped })}</Label>}
                            </Td>
                            <Td dataLabel={t("Problems")}>
                                {problems.map(([host, said]) => (
                                    <div key={host}><strong>{host}</strong>: {said}</div>
                                ))}
                            </Td>
                        </Tr>
                    );
                })}
            </Tbody>
        </Table>
    );
}

export function Diff({ text }: { text: string }) {
    const lines = useMemo(() => diffLines(text), [text]);
    return (
        <pre className="fleet-log fleet-diff">
            {lines.map((line, index) => <span key={index} className={`fleet-diff-${line.kind}`}>{line.text}{"\n"}</span>)}
        </pre>
    );
}

function HostDiffs({ entry }: { entry: HostChanges }) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    return (
        <ExpandableSection
            isExpanded={open}
            onToggle={(_event, expanded) => setOpen(expanded)}
            toggleContent={<>{entry.host} <Label isCompact color="orange">{entry.changes.length}</Label></>}
        >
            {/* Drawn only when open: on a large fleet most hosts are never looked at. */}
            {open && entry.changes.map((change, index) => (
                <div key={index} className="fleet-change">
                    <strong>{change.task}</strong>
                    {change.item && <> <code>{change.item}</code></>}
                    {change.play && <Content component="small" style={{ display: "block" }}>{change.play}</Content>}
                    <Diff text={change.text} />
                    {change.cut && <Content component="small">{t("This is only the beginning of the differences: the output has all of them.")}</Content>}
                </div>
            ))}
        </ExpandableSection>
    );
}

function Changes({ tasks, run }: { tasks: TaskView[]; run: Run }) {
    const { t } = useTranslation();
    const hosts = useMemo(() => changesByHost(tasks), [tasks]);
    if (hosts.length === 0) {
        if (!run.options.diff) return <Content component="p">{t("This run was not asked to show differences. Launch it as a dry run to see them.")}</Content>;
        return <Content component="p">{isActive(run.status) ? t("No differences so far.") : t("No task reported a difference.")}</Content>;
    }
    return (
        <>
            {run.options.check && <Content component="p">{t("Dry run: this is what would change, nothing was changed.")}</Content>}
            {hosts.map(entry => <HostDiffs key={entry.host} entry={entry} />)}
        </>
    );
}

function Recap({ stats }: { stats: Record<string, HostStats> }) {
    const { t } = useTranslation();
    const bad = (host: HostStats) => (host.failures ?? 0) + (host.unreachable ?? 0);
    // Hosts with problems first: on a large fleet they are what one looks for.
    const hosts = Object.entries(stats).sort(([a, x], [b, y]) => bad(y) - bad(x) || a.localeCompare(b));
    if (hosts.length === 0) return null;
    const cell = (value: number | undefined, color: "green" | "orange" | "red" | "grey") =>
        value ? <Label isCompact color={color}>{value}</Label> : <span aria-hidden>·</span>;
    return (
        <Table variant="compact" aria-label={t("Result per host")}>
            <Thead>
                <Tr><Th>{t("Host")}</Th><Th>{t("OK")}</Th><Th>{t("Changed")}</Th><Th>{t("Failures")}</Th><Th>{t("Unreachable")}</Th><Th>{t("Skipped")}</Th></Tr>
            </Thead>
            <Tbody>
                {hosts.map(([name, host]) => (
                    <Tr key={name}>
                        <Td dataLabel={t("Host")}>{name}</Td>
                        <Td dataLabel={t("OK")}>{cell(host.ok, "green")}</Td>
                        <Td dataLabel={t("Changed")}>{cell(host.changed, "orange")}</Td>
                        <Td dataLabel={t("Failures")}>{cell(host.failures, "red")}</Td>
                        <Td dataLabel={t("Unreachable")}>{cell(host.unreachable, "red")}</Td>
                        <Td dataLabel={t("Skipped")}>{cell(host.skipped, "grey")}</Td>
                    </Tr>
                ))}
            </Tbody>
        </Table>
    );
}

export function RunDetailPage({ id }: { id: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const kinds = useKinds();
    const run = useLoad(() => call<Run>("run.get", { id }));
    const [log, setLog] = useState("");
    const [tasks, setTasks] = useState<TaskView[]>([]);
    const [tab, setTab] = useState("output");
    const [error, setError] = useState<string | null>(null);
    const [canceling, setCanceling] = useState(false);
    const position = useRef({ log: 0, events: 0, play: "", busy: false, done: false });
    const active = run.data ? isActive(run.data.status) : true;
    const mayRead = can("run.log");

    // Fetches what was written since the last call. Also called once after the
    // run has ended, to pick up its last lines.
    async function fetchOutput() {
        const state = position.current;
        if (!mayRead || state.busy) return;
        state.busy = true;
        try {
            for (let more = true; more;) {
                const chunk = await call<Chunk>("run.log", { id, offset: state.log });
                state.log = chunk.offset;
                if (chunk.text) setLog(current => current + chunk.text);
                more = chunk.more;
            }
            for (let more = true; more;) {
                const chunk = await call<Chunk>("run.events", { id, offset: state.events });
                state.events = chunk.offset;
                if (chunk.text) {
                    const play = state.play;
                    setTasks(current => {
                        const next = addEvents(current, chunk.text, play);
                        state.play = next.play;
                        return next.tasks;
                    });
                }
                more = chunk.more;
            }
        } catch (ex) {
            setError(message(ex));
        } finally {
            state.busy = false;
        }
    }

    usePolling(async () => { run.reload(); await fetchOutput(); }, 1500, active);
    useEffect(() => {
        if (run.data && !active && !position.current.done) {
            position.current.done = true;
            fetchOutput();
        }
    }, [run.data, active]);

    async function cancel() {
        setCanceling(true);
        setError(null);
        try {
            await call("run.cancel", { id });
            run.reload();
        } catch (ex) {
            setError(message(ex));
        }
        setCanceling(false);
    }

    if (run.error) return <Alert variant="danger" isInline title={run.error.message} />;
    if (!run.data) return null;
    const data = run.data;
    const hasTasks = data.kind === "playbook" || data.kind === "adhoc";
    const isWorkflow = data.kind === "workflow";

    return (
        <>
            <SectionHeader title={data.title}>
                {data.approval?.can_decide && <DecideButtons runId={data.id} title={data.title} onDone={run.reload} />}
                {active && can("run.cancel") && (
                    <Button variant="danger" onClick={cancel} isLoading={canceling} isDisabled={canceling}>{t("Cancel run")}</Button>
                )}
                <Button variant="secondary" onClick={() => cockpit.location.go(["runs"])}>{t("All runs")}</Button>
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {data.error && <Alert variant="danger" isInline title={data.error} style={{ marginBottom: "1rem" }} />}
            <Flex direction={{ default: "column" }} gap={{ default: "gapLg" }}>
                <FlexItem>
                    <AtTerminal text={runCommand(data.id, active && mayRead)} />
                </FlexItem>
                <FlexItem>
                    <DescriptionList isHorizontal isCompact columnModifier={{ default: "2Col" }}>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Status")}</DescriptionListTerm>
                            <DescriptionListDescription><RunStatusLabel status={data.status} /></DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Type")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {kinds[data.kind]}
                                {data.options.check && <Label isCompact color="blue" style={{ marginInlineStart: "0.5rem" }}>{t("Dry run")}</Label>}
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Launched by")}</DescriptionListTerm>
                            <DescriptionListDescription>{data.created_by}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Started")}</DescriptionListTerm>
                            <DescriptionListDescription>{formatTime(data.started_at ?? data.created_at, i18n.language)}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Duration")}</DescriptionListTerm>
                            <DescriptionListDescription>{runDuration(data, Date.now() / 1000)}</DescriptionListDescription>
                        </DescriptionListGroup>
                        {data.kind === "galaxy" && data.options.path && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Requirements")}</DescriptionListTerm>
                                <DescriptionListDescription>{data.options.path}</DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.options.filename && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Archive")}</DescriptionListTerm>
                                <DescriptionListDescription>{data.options.filename}</DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.options.module && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Module")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    <code>{data.options.module}</code>
                                    {data.options.become && <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }}>{t("become")}</Label>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {(data.options.free_form || Object.keys(data.options.args ?? {}).length > 0) && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Arguments")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {data.options.free_form && <code>{data.options.free_form}</code>}
                                    {Object.keys(data.options.args ?? {}).length > 0 && <code style={{ display: "block" }}>{JSON.stringify(data.options.args)}</code>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.options.limit && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{data.kind === "adhoc" ? t("Hosts") : t("Limit to hosts")}</DescriptionListTerm>
                                <DescriptionListDescription><code>{data.options.limit}</code></DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {(data.options.variable_sets ?? []).length > 0 && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Sets of variables")}</DescriptionListTerm>
                                <DescriptionListDescription>{data.options.variable_sets!.map(one => one.name).join(", ")}</DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {Object.keys(data.options.answers ?? {}).length + (data.options.secret_answers ?? []).length > 0 && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Answers")}</DescriptionListTerm>
                                <DescriptionListDescription><Answered answers={data.options.answers ?? {}} secrets={data.options.secret_answers} /></DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.options.serial && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("In series")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {data.waiting_for
                                        ? <>{t("Waits for")} <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", data.waiting_for!.id])}>{data.waiting_for.title}</Button></>
                                        : t("After the runs launched before it on its inventory")}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.workflow_run_id && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Step of")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", data.workflow_run_id!])}>{t("a workflow run")}</Button>
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.revision && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Revision")}</DescriptionListTerm>
                                <DescriptionListDescription><code>{data.revision.slice(0, 10)}</code></DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.approval && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Approval")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {data.approval.decision === "approved" && t("Approved by {{user}} on {{when}}", { user: data.approval.decided_by, when: formatTime(data.approval.decided_at, i18n.language) })}
                                    {data.approval.decision === "rejected" && t("Rejected by {{user}} on {{when}}", { user: data.approval.decided_by, when: formatTime(data.approval.decided_at, i18n.language) })}
                                    {data.approval.decision === "expired" && t("Nobody approved it by {{when}}", { when: formatTime(data.approval.expires_at, i18n.language) })}
                                    {data.approval.decision === "" && (data.status === "pending_approval"
                                        ? t("Waiting, until {{when}}", { when: formatTime(data.approval.expires_at, i18n.language) })
                                        : t("Not decided"))}
                                    {data.approval.comment && <Content component="small" style={{ display: "block" }}>{data.approval.comment}</Content>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                    </DescriptionList>
                </FlexItem>
                {isWorkflow && <FlexItem><WorkflowSteps id={data.id} /></FlexItem>}
                <FlexItem><Recap stats={data.stats} /></FlexItem>
                <FlexItem>
                    {isWorkflow ? null : mayRead && data.pruned_at ? (
                        <Alert variant="info" isInline title={t("The output of this run was deleted on {{when}}, as runs this old keep only their outcome.", { when: formatTime(data.pruned_at, i18n.language) })} />
                    ) : mayRead ? (
                        <Card isPlain>
                            <Tabs panelId="run-detail" activeKey={tab} onSelect={(_event, key) => setTab(String(key))}>
                                <Tab eventKey="output" title={<TabTitleText>{t("Output")}</TabTitleText>} />
                                {hasTasks && <Tab eventKey="tasks" title={<TabTitleText>{t("Tasks")}</TabTitleText>} />}
                                {hasTasks && <Tab eventKey="changes" title={<TabTitleText>{t("Differences")}</TabTitleText>} />}
                            </Tabs>
                            <CardBody style={{ paddingInline: 0 }}>
                                <TabPanel panelId="run-detail" active={tab}>
                                    {tab === "output" && <Output text={log} />}
                                    {tab === "tasks" && <Tasks tasks={tasks} />}
                                    {tab === "changes" && <Changes tasks={tasks} run={data} />}
                                </TabPanel>
                            </CardBody>
                        </Card>
                    ) : (
                        <Alert variant="info" isInline title={t("The output of a run is shown to operators and administrators only, because it may contain sensitive data.")} />
                    )}
                </FlexItem>
            </Flex>
        </>
    );
}
