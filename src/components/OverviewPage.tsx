import React, { Suspense, lazy } from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    Card,
    CardBody,
    CardFooter,
    CardTitle,
    Content,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    Flex,
    FlexItem,
    Gallery,
    Grid,
    GridItem,
    Label,
    List,
    ListItem,
    Title,
    ToggleGroup,
    ToggleGroupItem,
} from "@patternfly/react-core";
import { call } from "../lib/fleet.ts";
import { formatDuration, formatTime } from "../lib/format.ts";
import { useLoad, usePolling, useStoredState } from "../lib/hooks.ts";
import {
    WINDOWS, anyDay, controllerTrouble, dayLabel, formatRate, readWindow, topSystems, totalOf, tzOffset,
} from "../lib/overview.ts";
import type { AlertDay, Overview, RunDay, Window } from "../lib/overview.ts";
import type { Check, Session } from "../lib/types.ts";
import { RunStatusLabel, openHost, openRun } from "./common.tsx";
import { StateLabel } from "./ChecksPage.tsx";
import { StatusLabel } from "./StatusLabel.tsx";
import { Alert, Button, Spinner } from "./pf.tsx";

const POLL = 30_000;

function ChartsMissing() {
    const { t } = useTranslation();
    return <Alert variant="warning" isInline isPlain title={t("The charts could not be loaded: reload the page.")} />;
}

// The charts bring their own library, loaded the first time one is shown. A
// page loaded before an upgrade asks for a chunk that is gone: say so.
let charts: Promise<typeof import("./charts.tsx")> | null = null;
const loadCharts = () => (charts ??= import("./charts.tsx"));
const StatesDonut = lazy(() => loadCharts().then(module => ({ default: module.StatesDonut }), () => ({ default: ChartsMissing })));
const RunsChart = lazy(() => loadCharts().then(module => ({ default: module.RunsChart }), () => ({ default: ChartsMissing })));
const AlertsChart = lazy(() => loadCharts().then(module => ({ default: module.AlertsChart }), () => ({ default: ChartsMissing })));

function go(...path: string[]) {
    cockpit.location.go(path);
}

// A figure at the top of the page: a number, what it counts, a line about
// it, and the section it leads to.
function Figure({ title, value, children, section, tone }: {
    title: string; value: string; children?: React.ReactNode; section: string; tone?: "good" | "warning" | "bad";
}) {
    const color = tone === "bad"
        ? "var(--pf-t--global--text--color--status--danger--default)"
        : tone === "warning" ? "var(--pf-t--global--text--color--status--warning--default)" : undefined;
    return (
        <Card isCompact isFullHeight>
            <CardTitle component="h2">
                <Button variant="link" isInline onClick={() => go(section)}>{title}</Button>
            </CardTitle>
            <CardBody>
                <div style={{ fontSize: "var(--pf-t--global--font--size--heading--h1)", fontWeight: "var(--pf-t--global--font--weight--heading--default)", color }}>
                    {value}
                </div>
                {children && <Content component="small">{children}</Content>}
            </CardBody>
        </Card>
    );
}

// The days of a chart as a table, for a screen reader. The class is on a
// wrapper: Firefox does not clip the caption of a table that carries it.
function DaysTable<T extends { start: number }>({ caption, days, columns }: {
    caption: string; days: T[]; columns: [keyof T & string, string][];
}) {
    const { t, i18n } = useTranslation();
    return (
        <div className="pf-v6-screen-reader">
            <table>
                <caption>{caption}</caption>
                <thead><tr><th>{t("Day")}</th>{columns.map(([key, name]) => <th key={key}>{name}</th>)}</tr></thead>
                <tbody>
                    {days.map(day => (
                        <tr key={day.start}>
                            <td>{dayLabel(day.start, i18n.language)}</td>
                            {columns.map(([key]) => <td key={key}>{String(day[key])}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function Empty({ children }: { children: React.ReactNode }) {
    return <Content component="p" style={{ color: "var(--pf-t--global--text--color--subtle)" }}>{children}</Content>;
}

function ChecksCard({ data }: { data: Overview }) {
    const { t, i18n } = useTranslation();
    const total = totalOf(data.states);
    return (
        <Card isFullHeight>
            <CardTitle component="h2">{t("State of the checks")}</CardTitle>
            <CardBody>
                {total === 0
                    ? <Empty>{t("No check runs on the hosts yet.")}</Empty>
                    : <Suspense fallback={<Spinner size="lg" aria-label={t("Loading")} />}><StatesDonut states={data.states} /></Suspense>}
                {data.problems.top.length > 0 && (
                    <>
                        <Title headingLevel="h3" size="md" style={{ marginBlock: "1rem 0.5rem" }}>
                            {t("Open problems, the worst first")}
                        </Title>
                        <List isPlain>
                            {data.problems.top.map(problem => (
                                <ListItem key={`${problem.host_id}/${problem.check_id}`}>
                                    <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                                        <StateLabel state={problem.state} />
                                        <Button variant="link" isInline onClick={() => openHost(problem.host_id)}>{problem.host}</Button>
                                        <span>{problem.check}</span>
                                        {problem.acknowledged && <Label isCompact color="blue">{t("Acknowledged")}</Label>}
                                        {problem.in_maintenance && <Label isCompact color="blue">{t("In maintenance")}</Label>}
                                        {problem.since && (
                                            <Content component="small">{t("since {{time}}", { time: formatTime(problem.since, i18n.language) })}</Content>
                                        )}
                                    </Flex>
                                </ListItem>
                            ))}
                        </List>
                    </>
                )}
            </CardBody>
            <CardFooter>
                <Button variant="link" isInline onClick={() => go("hosts")}>{t("Show the hosts")}</Button>
            </CardFooter>
        </Card>
    );
}

function RunsCard({ data, days }: { data: Overview; days: Window }) {
    const { t, i18n } = useTranslation();
    const runs = data.runs;
    const columns: [keyof RunDay & string, string][] = [["successful", t("Successful")], ["failed", t("Failed")], ["stopped", t("Stopped")]];
    return (
        <Card isFullHeight>
            <CardTitle component="h2">{t("runs_of_last_days", { count: days })}</CardTitle>
            <CardBody>
                {anyDay(runs.days, ["successful", "failed", "stopped"])
                    ? (
                        <>
                            <Suspense fallback={<Spinner size="lg" aria-label={t("Loading")} />}><RunsChart days={runs.days} /></Suspense>
                            <DaysTable caption={t("Runs by day and outcome")} days={runs.days} columns={columns} />
                            <DescriptionList isHorizontal isCompact>
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Usual duration")}</DescriptionListTerm>
                                    <DescriptionListDescription>{runs.duration_median === null ? "–" : formatDuration(runs.duration_median)}</DescriptionListDescription>
                                </DescriptionListGroup>
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Longest")}</DescriptionListTerm>
                                    <DescriptionListDescription>{runs.duration_max === null ? "–" : formatDuration(runs.duration_max)}</DescriptionListDescription>
                                </DescriptionListGroup>
                            </DescriptionList>
                        </>
                    )
                    : <Empty>{t("No runs in these days.")}</Empty>}
                {runs.recent_failures.length > 0 && (
                    <>
                        <Title headingLevel="h3" size="md" style={{ marginBlock: "1rem 0.5rem" }}>{t("Latest failures")}</Title>
                        <List isPlain>
                            {runs.recent_failures.map(run => (
                                <ListItem key={run.id}>
                                    <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                                        <RunStatusLabel status={run.status} />
                                        <Button variant="link" isInline onClick={() => openRun(run.id)}>{run.title}</Button>
                                        <Content component="small">{formatTime(run.finished_at, i18n.language)}</Content>
                                    </Flex>
                                </ListItem>
                            ))}
                        </List>
                    </>
                )}
            </CardBody>
            <CardFooter>
                <Button variant="link" isInline onClick={() => go("runs")}>{t("Show the runs")}</Button>
            </CardFooter>
        </Card>
    );
}

function AlertsCard({ data, days }: { data: Overview; days: Window }) {
    const { t } = useTranslation();
    const alerts = data.alerts;
    const columns: [keyof AlertDay & string, string][] = [["problems", t("Problems")], ["recoveries", t("Recoveries")]];
    return (
        <Card isFullHeight>
            <CardTitle component="h2">{t("Problems and recoveries")}</CardTitle>
            <CardBody>
                {anyDay(alerts.days, ["problems", "recoveries"])
                    ? (
                        <>
                            <Suspense fallback={<Spinner size="lg" aria-label={t("Loading")} />}><AlertsChart days={alerts.days} /></Suspense>
                            <DaysTable caption={t("Problems and recoveries by day")} days={alerts.days} columns={columns} />
                        </>
                    )
                    : <Empty>{t("no_alerts_in_days", { count: days })}</Empty>}
                <DescriptionList isHorizontal isCompact>
                    <DescriptionListGroup>
                        <DescriptionListTerm>{t("Mean time to recover")}</DescriptionListTerm>
                        <DescriptionListDescription>
                            {alerts.time_to_recover === null
                                ? "–"
                                : t("time_to_recover_over", { duration: formatDuration(alerts.time_to_recover), count: alerts.recovered })}
                        </DescriptionListDescription>
                    </DescriptionListGroup>
                </DescriptionList>
            </CardBody>
            <CardFooter>
                <Button variant="link" isInline onClick={() => go("checks")}>{t("Go to the checks")}</Button>
            </CardFooter>
        </Card>
    );
}

function FailingCard({ data }: { data: Overview }) {
    const { t } = useTranslation();
    const { templates, hosts } = data.runs;
    return (
        <Card isFullHeight>
            <CardTitle component="h2">{t("Failing most")}</CardTitle>
            <CardBody>
                {templates.length === 0 && hosts.length === 0 && <Empty>{t("Nothing failed in these days.")}</Empty>}
                {templates.length > 0 && (
                    <>
                        <Title headingLevel="h3" size="md" style={{ marginBlockEnd: "0.5rem" }}>{t("Job templates")}</Title>
                        <List isPlain>
                            {templates.map(item => (
                                <ListItem key={item.id}>
                                    <Button variant="link" isInline onClick={() => go("templates")}>{item.name}</Button>{" "}
                                    <Content component="small">{t("failed {{failed}} of {{runs}}", { failed: item.failed, runs: item.runs })}</Content>
                                </ListItem>
                            ))}
                        </List>
                    </>
                )}
                {hosts.length > 0 && (
                    <>
                        <Title headingLevel="h3" size="md" style={{ marginBlock: "1rem 0.5rem" }}>{t("Hosts")}</Title>
                        <List isPlain>
                            {hosts.map(item => (
                                <ListItem key={item.name}>
                                    {item.name}{" "}
                                    <Content component="small">{t("failed_in_runs", { count: item.failed })}</Content>
                                </ListItem>
                            ))}
                        </List>
                    </>
                )}
            </CardBody>
        </Card>
    );
}

function UpcomingCard({ data }: { data: Overview }) {
    const { t, i18n } = useTranslation();
    const { schedules, maintenances } = data.upcoming;
    return (
        <Card isFullHeight>
            <CardTitle component="h2">{t("Coming up")}</CardTitle>
            <CardBody>
                <Title headingLevel="h3" size="md" style={{ marginBlockEnd: "0.5rem" }}>{t("Schedules")}</Title>
                {schedules.length === 0
                    ? <Empty>{t("No schedule is enabled.")}</Empty>
                    : (
                        <List isPlain>
                            {schedules.map(schedule => (
                                <ListItem key={schedule.id}>
                                    <Button variant="link" isInline onClick={() => go("schedules")}>{schedule.name}</Button>{" "}
                                    <Content component="small">{formatTime(schedule.next_at, i18n.language)} · {schedule.template}</Content>
                                </ListItem>
                            ))}
                        </List>
                    )}
                <Title headingLevel="h3" size="md" style={{ marginBlock: "1rem 0.5rem" }}>{t("Maintenance windows")}</Title>
                {maintenances.length === 0
                    ? <Empty>{t("None open or to come.")}</Empty>
                    : (
                        <List isPlain>
                            {maintenances.map(window => (
                                <ListItem key={window.id}>
                                    {window.active && <><Label isCompact color="blue">{t("Under way")}</Label>{" "}</>}
                                    {window.host || window.host_group || t("all hosts")} · {window.inventory}{" "}
                                    <Content component="small">
                                        {window.active
                                            ? t("until {{time}}", { time: formatTime(window.ends_at, i18n.language) })
                                            : t("from {{start}} to {{end}}", { start: formatTime(window.starts_at, i18n.language), end: formatTime(window.ends_at, i18n.language) })}
                                    </Content>
                                </ListItem>
                            ))}
                        </List>
                    )}
            </CardBody>
        </Card>
    );
}

function HostsCard({ data }: { data: Overview }) {
    const { t } = useTranslation();
    const { hosts, updates, compliance } = data;
    const systems = topSystems(hosts.systems);
    return (
        <Card isFullHeight>
            <CardTitle component="h2">{t("What the hosts are")}</CardTitle>
            <CardBody>
                {hosts.total === 0
                    ? <Empty>{t("No host yet: they come from the inventories that checks use.")}</Empty>
                    : (
                        <DescriptionList isHorizontal isCompact>
                            {systems.map(system => (
                                <DescriptionListGroup key={`${system.name}/${system.other ? 1 : 0}`}>
                                    <DescriptionListTerm>{system.other ? t("Other systems") : system.name || t("Not known yet")}</DescriptionListTerm>
                                    <DescriptionListDescription>{t("hosts_count", { count: system.hosts })}</DescriptionListDescription>
                                </DescriptionListGroup>
                            ))}
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Updates")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {updates.known === 0
                                        ? t("Not collected yet")
                                        : updates.updates === 0
                                            ? <StatusLabel tone="good">{t("Up to date")}</StatusLabel>
                                            : (
                                                <Flex gap={{ default: "gapSm" }}>
                                                    <span>{t("updates_on_hosts", { count: updates.updates, hosts: updates.hosts })}</span>
                                                    {updates.security > 0 && (
                                                        <StatusLabel tone="bad">{t("security_updates_count", { count: updates.security })}</StatusLabel>
                                                    )}
                                                </Flex>
                                            )}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            {compliance.compliant + compliance.drifted > 0 && (
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Compliance")}</DescriptionListTerm>
                                    <DescriptionListDescription>
                                        {compliance.drifted > 0
                                            ? <StatusLabel tone="warning">{t("hosts_drifted_count", { count: compliance.drifted })}</StatusLabel>
                                            : <StatusLabel tone="good">{t("All compliant")}</StatusLabel>}
                                    </DescriptionListDescription>
                                </DescriptionListGroup>
                            )}
                            {hosts.uptime.known > 0 && (
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Not restarted for {{days}} days", { days: hosts.uptime.days })}</DescriptionListTerm>
                                    <DescriptionListDescription>{t("hosts_of_known", { count: hosts.uptime.longer, known: hosts.uptime.known })}</DescriptionListDescription>
                                </DescriptionListGroup>
                            )}
                        </DescriptionList>
                    )}
            </CardBody>
            <CardFooter>
                <Button variant="link" isInline onClick={() => go("assets")}>{t("Show the assets")}</Button>
            </CardFooter>
        </Card>
    );
}

// Who is looking and what the installation says: the content of the page
// before it had figures, kept at its bottom.
function AccessCard({ session, data }: { session: Session; data: Overview | null }) {
    const { t } = useTranslation();
    const checks = useLoad(() => call<{ checks: Check[] }>("selfcheck"));
    const roles = {
        viewer: {
            name: t("Viewer"),
            allows: t("See the state of the fleet, the history of runs and the collected data."),
        },
        operator: {
            name: t("Operator"),
            allows: t("Everything a viewer can do, plus launching the existing job templates and acknowledging alerts."),
        },
        admin: {
            name: t("Administrator"),
            allows: t("Everything an operator can do, plus configuring projects, inventories, credentials, schedules and notifications."),
        },
    };
    // The page is not shown to an approver without a role.
    const role = roles[session.role ?? "viewer"];
    const approves = session.rights.includes("approve");
    const count = (status: Check["status"]) => checks.data?.checks.filter(check => check.status === status).length ?? 0;
    const controller = data?.controller;

    return (
        <Card>
            <CardTitle component="h2">{t("Your access and the installation")}</CardTitle>
            <CardBody>
                <Grid hasGutter>
                    <GridItem md={6}>
                        <DescriptionList isHorizontal isCompact>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("User")}</DescriptionListTerm>
                                <DescriptionListDescription>{session.user}</DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Role")}</DescriptionListTerm>
                                <DescriptionListDescription>{approves ? t("{{role}}, approver", { role: role.name }) : role.name}</DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Allows")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {role.allows}
                                    {approves && <> {t("Also approving the runs of the job templates that need it, except those you launched.")}</>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        </DescriptionList>
                    </GridItem>
                    <GridItem md={6}>
                        <DescriptionList isHorizontal isCompact>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Version")}</DescriptionListTerm>
                                <DescriptionListDescription>{session.version}</DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Installation")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {checks.loading && <Spinner size="md" aria-label={t("Loading")} />}
                                    {checks.error && <StatusLabel tone="bad">{checks.error.message}</StatusLabel>}
                                    {checks.data && !checks.loading && (
                                        <Flex gap={{ default: "gapSm" }}>
                                            {count("fail") > 0 && <StatusLabel tone="bad">{t("checks_failed_count", { count: count("fail") })}</StatusLabel>}
                                            {count("warn") > 0 && <StatusLabel tone="warning">{t("checks_warning_count", { count: count("warn") })}</StatusLabel>}
                                            <StatusLabel tone="good">{t("checks_passed_count", { count: count("ok") })}</StatusLabel>
                                        </Flex>
                                    )}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            {controller && (
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Keeping up")}</DescriptionListTerm>
                                    <DescriptionListDescription>
                                        {controllerTrouble(controller)
                                            ? (
                                                <Flex gap={{ default: "gapSm" }}>
                                                    {controller.behind > 0 && <StatusLabel tone="bad">{t("cycles_behind_count", { count: controller.behind })}</StatusLabel>}
                                                    {controller.unprepared > 0 && <StatusLabel tone="bad">{t("cycles_unprepared_count", { count: controller.unprepared })}</StatusLabel>}
                                                    {controller.notifications.failed > 0 && (
                                                        <StatusLabel tone="warning">{t("notifications_failed_count", { count: controller.notifications.failed })}</StatusLabel>
                                                    )}
                                                </Flex>
                                            )
                                            : <StatusLabel tone="good">{t("Yes")}</StatusLabel>}
                                    </DescriptionListDescription>
                                </DescriptionListGroup>
                            )}
                        </DescriptionList>
                        <Button variant="link" isInline onClick={() => go("system")} style={{ marginBlockStart: "0.5rem" }}>
                            {t("Show the checks of the system")}
                        </Button>
                    </GridItem>
                </Grid>
            </CardBody>
        </Card>
    );
}

export function OverviewPage({ session }: { session: Session }) {
    const { t, i18n } = useTranslation();
    const [days, setDays] = useStoredState<Window>("fleet-overview-days", readWindow);
    const overview = useLoad(
        () => call<Overview>("overview.get", { days, tz_offset: tzOffset(new Date()) }),
        [days],
    );
    usePolling(overview.reload, POLL, true);
    const data = overview.data;

    return (
        <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
            <Flex alignItems={{ default: "alignItemsCenter" }} gap={{ default: "gapMd" }}>
                <FlexItem>
                    <ToggleGroup isCompact aria-label={t("Days shown")}>
                        {WINDOWS.map(window => (
                            <ToggleGroupItem key={window} text={t("days_count", { count: window })}
                                isSelected={days === window} onChange={() => setDays(window)} />
                        ))}
                    </ToggleGroup>
                </FlexItem>
                {overview.loading && <FlexItem><Spinner size="md" aria-label={t("Loading")} /></FlexItem>}
            </Flex>
            {overview.error && <Alert variant="danger" isInline title={overview.error.message} />}
            {!data && !overview.error && <Spinner aria-label={t("Loading")} />}
            {data && (
                <>
                    <Gallery hasGutter minWidths={{ default: "12rem" }}>
                        <Figure title={t("Hosts")} value={String(data.hosts.total)} section="hosts"
                            tone={data.hosts.unreachable > 0 ? "bad" : undefined}>
                            {data.hosts.total === 0
                                ? t("none yet")
                                : [
                                    data.hosts.unreachable > 0 ? t("unreachable_count", { count: data.hosts.unreachable }) : t("all reachable"),
                                    data.hosts.in_maintenance > 0 ? t("in_maintenance_count", { count: data.hosts.in_maintenance }) : "",
                                ].filter(Boolean).join(" · ")}
                        </Figure>
                        <Figure title={t("Open problems")} value={String(data.problems.unacknowledged)} section="checks"
                            tone={data.problems.unacknowledged > 0 ? "bad" : data.problems.open > 0 ? "warning" : undefined}>
                            {data.problems.open === 0 ? t("none") : t("not_acknowledged_of", { count: data.problems.open })}
                        </Figure>
                        <Figure title={t("Successful runs")} value={formatRate(data.runs.success_rate, i18n.language)} section="runs"
                            tone={data.runs.failed > 0 ? "warning" : undefined}>
                            {t("failed_of_runs_in_days", { failed: data.runs.failed, count: data.runs.successful + data.runs.failed, days })}
                        </Figure>
                        <Figure title={t("Queue")} value={String(data.runs.queue.running + data.runs.queue.queued)} section="runs">
                            {[
                                t("running_count", { count: data.runs.queue.running }),
                                t("queued_count", { count: data.runs.queue.queued }),
                                data.runs.queue.pending_approval > 0 ? t("awaiting_approval_count", { count: data.runs.queue.pending_approval }) : "",
                            ].filter(Boolean).join(" · ")}
                        </Figure>
                        <Figure title={t("Security updates")} value={data.updates.known === 0 ? "–" : String(data.updates.security)} section="assets"
                            tone={data.updates.security > 0 ? "bad" : undefined}>
                            {data.updates.known === 0
                                ? t("not collected yet")
                                : t("on_hosts_count", { count: data.updates.security_hosts })}
                        </Figure>
                    </Gallery>
                    <Gallery hasGutter minWidths={{ default: "24rem" }}>
                        <ChecksCard data={data} />
                        <RunsCard data={data} days={days} />
                        <AlertsCard data={data} days={days} />
                        <FailingCard data={data} />
                        <UpcomingCard data={data} />
                        <HostsCard data={data} />
                    </Gallery>
                </>
            )}
            <AccessCard session={session} data={data} />
        </Flex>
    );
}
