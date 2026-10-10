import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Alert, Button, Card, CardBody, Content, DescriptionList, DescriptionListDescription, DescriptionListGroup,
    DescriptionListTerm, ExpandableSection, Flex, FlexItem, FormSelect, FormSelectOption, Label, Tab, Tabs, TabTitleText,
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
import { ListState, RunStatusLabel, SectionHeader, message } from "./common.tsx";
import { Answered } from "./SurveyEditor.tsx";

const isActive = (status: RunStatus) => status === "queued" || status === "running";
// Past this, only the end of the output is drawn; the whole log stays on the server.
const MAX_SHOWN = 400_000;

function useKinds() {
    const { t } = useTranslation();
    return { playbook: t("Playbook"), inventory: t("Inventory reading"), sync: t("Project update"), syntax: t("Syntax check") } as Record<Run["kind"], string>;
}

export function RunsPage() {
    const { t, i18n } = useTranslation();
    const kinds = useKinds();
    const [status, setStatus] = useState("");
    const [limit, setLimit] = useState(50);
    const list = useLoad(() => call<{ runs: Run[]; more: boolean }>("run.list", { limit, status }));
    useEffect(list.reload, [status, limit]);
    const anyActive = list.data?.runs.some(run => isActive(run.status)) ?? false;
    usePolling(list.reload, 3000, anyActive);
    const now = Date.now() / 1000;

    const statuses: Record<string, string> = {
        "": t("Any status"), running: t("Running"), queued: t("Queued"), successful: t("Successful"),
        failed: t("Failed"), canceled: t("Canceled"), error: t("Error"),
    };

    return (
        <>
            <SectionHeader title={t("Runs")}>
                <FormSelect value={status} onChange={(_e, value) => setStatus(value)} aria-label={t("Filter by status")} style={{ width: "12rem" }}>
                    {Object.entries(statuses).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                </FormSelect>
                <Button variant="secondary" onClick={list.reload}>{t("Refresh")}</Button>
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
                                        <Td dataLabel={t("Status")}><RunStatusLabel status={run.status} /></Td>
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

    return (
        <>
            <SectionHeader title={data.title}>
                {active && can("run.cancel") && (
                    <Button variant="danger" onClick={cancel} isLoading={canceling} isDisabled={canceling}>{t("Cancel run")}</Button>
                )}
                <Button variant="secondary" onClick={() => cockpit.location.go(["runs"])}>{t("All runs")}</Button>
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {data.error && <Alert variant="danger" isInline title={data.error} style={{ marginBottom: "1rem" }} />}
            <Flex direction={{ default: "column" }} gap={{ default: "gapLg" }}>
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
                        {data.options.limit && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Limit to hosts")}</DescriptionListTerm>
                                <DescriptionListDescription><code>{data.options.limit}</code></DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {Object.keys(data.options.answers ?? {}).length + (data.options.secret_answers ?? []).length > 0 && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Answers")}</DescriptionListTerm>
                                <DescriptionListDescription><Answered answers={data.options.answers ?? {}} secrets={data.options.secret_answers} /></DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                        {data.revision && (
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Revision")}</DescriptionListTerm>
                                <DescriptionListDescription><code>{data.revision.slice(0, 10)}</code></DescriptionListDescription>
                            </DescriptionListGroup>
                        )}
                    </DescriptionList>
                </FlexItem>
                <FlexItem><Recap stats={data.stats} /></FlexItem>
                <FlexItem>
                    {mayRead ? (
                        <Card isPlain>
                            <Tabs activeKey={tab} onSelect={(_event, key) => setTab(String(key))}>
                                <Tab eventKey="output" title={<TabTitleText>{t("Output")}</TabTitleText>} />
                                {data.kind === "playbook" && <Tab eventKey="tasks" title={<TabTitleText>{t("Tasks")}</TabTitleText>} />}
                                {data.kind === "playbook" && <Tab eventKey="changes" title={<TabTitleText>{t("Differences")}</TabTitleText>} />}
                            </Tabs>
                            <CardBody style={{ paddingInline: 0 }}>
                                {tab === "output" && <Output text={log} />}
                                {tab === "tasks" && <Tasks tasks={tasks} />}
                                {tab === "changes" && <Changes tasks={tasks} run={data} />}
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
