import React, { Suspense, lazy, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Content,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    Flex,
    FlexItem,
    FormGroup,
    FormSelect,
    FormSelectOption,
    Label,
    TextInput,
    Title,
    ToggleGroup,
    ToggleGroupItem,
    Tooltip,
} from "@patternfly/react-core";
import { ExpandableRowContent, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { bySeverity, formatMetric, worstState } from "../lib/checks.ts";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { CheckState, FleetHost, HostDetail, Inventory, StateCounts } from "../lib/types.ts";
import { HostInventory } from "./AssetsPage.tsx";
import { MaintenanceModal, StateCountLabels, StateLabel, useStateNames } from "./ChecksPage.tsx";
import { FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { Alert, AlertActionCloseButton, ExpandCell, Spinner } from "./pf.tsx";

const POLL = 15_000;

function ChartsMissing() {
    const { t } = useTranslation();
    return <Alert variant="warning" isInline isPlain title={t("The charts could not be loaded: reload the page.")} />;
}

// The charts bring their own library, loaded the first time one is shown. A
// page loaded before an upgrade asks for a chunk that is gone: say so.
const MetricHistory = lazy(() => import("./MetricHistory.tsx").then(
    module => ({ default: module.MetricHistory }),
    () => ({ default: ChartsMissing }),
));

function Flags({ state }: { state: CheckState }) {
    const { t } = useTranslation();
    return (
        <Flex gap={{ default: "gapXs" }} style={{ display: "inline-flex", marginInlineStart: "0.5rem" }}>
            {state.state_type === "soft" && state.state !== "ok" && (
                <Tooltip content={t("Not confirmed yet: it is checked again before it is told about.")}>
                    <Label isCompact color="grey">{t("attempt {{attempt}} of {{max}}", { attempt: state.attempt, max: state.max_attempts })}</Label>
                </Tooltip>
            )}
            {state.acknowledged && (
                <Tooltip content={state.ack_comment ? `${state.ack_by}: ${state.ack_comment}` : state.ack_by}>
                    <Label isCompact color="blue">{t("Acknowledged")}</Label>
                </Tooltip>
            )}
            {state.in_maintenance && <Label isCompact color="blue">{t("In maintenance")}</Label>}
            {state.flapping && (
                <Tooltip content={t("It keeps changing: nothing is told about it until it settles.")}>
                    <Label isCompact color="orange">{t("Flapping")}</Label>
                </Tooltip>
            )}
            {state.suppressed && (
                <Tooltip content={t("The host cannot be reached: only its reachability is told about.")}>
                    <Label isCompact color="grey">{t("Host unreachable")}</Label>
                </Tooltip>
            )}
            {state.running && <Label isCompact color="grey">{t("Being checked")}</Label>}
        </Flex>
    );
}

function AckModal({ state, onClose, onDone }: { state: CheckState; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [comment, setComment] = useState("");
    return (
        <FormModal title={t("Acknowledge {{check}} on {{host}}", { check: state.check, host: state.host })} submitLabel={t("Acknowledge")}
            onSubmit={async () => { await call("state.ack", { host_id: state.host_id, check_id: state.check_id, comment: comment.trim() }); onDone(); onClose(); }}
            onClose={onClose}>
            <Content component="p">{t("Someone is on it: no reminder is sent while the problem lasts. The acknowledgement ends when the check is OK again.")}</Content>
            <FormGroup label={t("Comment")} fieldId="ack-comment">
                <TextInput id="ack-comment" value={comment} onChange={(_e, value) => setComment(value)} placeholder={t("Disk being replaced")} />
            </FormGroup>
        </FormModal>
    );
}

// What can be done about one state: acknowledge it, or check it again now.
function StateActions({ state, onChanged, onError }: { state: CheckState; onChanged: () => void; onError: (text: string) => void }) {
    const { t } = useTranslation();
    const can = useCan();
    const [acking, setAcking] = useState(false);

    async function act(command: string, params: Record<string, unknown>) {
        try {
            await call(command, params);
        } catch (ex) {
            onError(message(ex));
        }
        onChanged();
    }

    const problem = state.hard_state !== "ok";
    return (
        <Flex gap={{ default: "gapSm" }} flexWrap={{ default: "nowrap" }}>
            {problem && !state.acknowledged && can("state.ack") && (
                <Button variant="secondary" size="sm" onClick={() => setAcking(true)}>{t("Acknowledge")}</Button>
            )}
            {state.acknowledged && can("state.unack") && (
                <Button variant="link" size="sm" onClick={() => act("state.unack", { host_id: state.host_id, check_id: state.check_id })}>{t("Take back")}</Button>
            )}
            {can("check.run") && !state.running && (
                <Button variant="link" size="sm" onClick={() => act("check.run", { id: state.check_id, host_id: state.host_id })}>{t("Check now")}</Button>
            )}
            {acking && <AckModal state={state} onClose={() => setAcking(false)} onDone={onChanged} />}
        </Flex>
    );
}

function Since({ seconds }: { seconds: number | null }) {
    const { i18n } = useTranslation();
    return <>{seconds ? formatTime(seconds, i18n.language) : ""}</>;
}

function Counters({ counts }: { counts: StateCounts }) {
    const { t } = useTranslation();
    const names = useStateNames();
    const tiles: [keyof StateCounts, string][] = [
        ["critical", names.critical], ["unknown", names.unknown], ["warning", names.warning], ["ok", names.ok], ["pending", names.pending],
    ];
    return (
        <Flex gap={{ default: "gapLg" }} style={{ marginBlockEnd: "1rem" }} aria-label={t("States")}>
            {tiles.map(([key, label]) => (
                <FlexItem key={key}>
                    <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                        <Title headingLevel="h3" size="2xl">{counts[key]}</Title>
                        <StateLabel state={key === "pending" ? null : key} />
                    </Flex>
                    <span className="pf-v6-screen-reader">{label}</span>
                </FlexItem>
            ))}
        </Flex>
    );
}

function Problems({ states, onChanged, onError }: { states: CheckState[]; onChanged: () => void; onError: (text: string) => void }) {
    const { t } = useTranslation();
    const problems = bySeverity(states.filter(state => state.state !== null && state.state !== "ok"));
    if (problems.length === 0) {
        return <Alert variant="success" isInline isPlain title={t("No problems: every check is OK, or not checked yet.")} />;
    }
    return (
        <Table variant="compact" aria-label={t("Problems")}>
            <Thead>
                <Tr><Th>{t("State")}</Th><Th>{t("Host")}</Th><Th>{t("Which check")}</Th><Th>{t("What it found")}</Th><Th>{t("Since")}</Th><Th screenReaderText={t("Actions")} /></Tr>
            </Thead>
            <Tbody>
                {problems.map(state => (
                    <Tr key={`${state.host_id}-${state.check_id}`}>
                        <Td dataLabel={t("State")}><StateLabel state={state.state} /><Flags state={state} /></Td>
                        <Td dataLabel={t("Host")}>
                            <Button variant="link" isInline onClick={() => cockpit.location.go(["hosts", state.host_id])}>{state.host}</Button>
                        </Td>
                        <Td dataLabel={t("Which check")}>{state.check}</Td>
                        <Td dataLabel={t("What it found")}>{state.summary}</Td>
                        <Td dataLabel={t("Since")}><Since seconds={state.changed_at ?? state.checked_at} /></Td>
                        <Td isActionCell><StateActions state={state} onChanged={onChanged} onError={onError} /></Td>
                    </Tr>
                ))}
            </Tbody>
        </Table>
    );
}

function HostList({ hosts }: { hosts: FleetHost[] }) {
    const { t } = useTranslation();
    const sorted = [...hosts].sort((a, b) => {
        const rank = (host: FleetHost) => ["ok", "warning", "unknown", "critical"].indexOf(worstState(host.states) ?? "ok");
        return rank(b) - rank(a) || a.name.localeCompare(b.name);
    });
    return (
        <Table variant="compact" aria-label={t("Hosts")}>
            <Thead>
                <Tr><Th>{t("Host")}</Th><Th>{t("Inventory")}</Th><Th>{t("Groups")}</Th><Th>{t("System")}</Th><Th>{t("Checks")}</Th></Tr>
            </Thead>
            <Tbody>
                {sorted.map(host => (
                    <Tr key={host.id}>
                        <Td dataLabel={t("Host")}>
                            <Button variant="link" isInline onClick={() => cockpit.location.go(["hosts", host.id])}>{host.name}</Button>
                            {host.reachable === false && <Label isCompact color="red" style={{ marginInlineStart: "0.5rem" }}>{t("Not reachable")}</Label>}
                        </Td>
                        <Td dataLabel={t("Inventory")}>{host.inventory}</Td>
                        <Td dataLabel={t("Groups")}>{host.groups.join(", ")}</Td>
                        <Td dataLabel={t("System")}>{host.os || <Content component="small">{t("Not known yet")}</Content>}</Td>
                        <Td dataLabel={t("Checks")}><StateCountLabels counts={host.states} /></Td>
                    </Tr>
                ))}
            </Tbody>
        </Table>
    );
}

// Hosts down, checks across: one cell per state.
function Grid({ hosts, states }: { hosts: FleetHost[]; states: CheckState[] }) {
    const { t } = useTranslation();
    const checks = [...new Map(states.map(state => [state.check_id, state.check])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
    const cell = new Map(states.map(state => [`${state.host_id} ${state.check_id}`, state]));
    const rows = hosts.filter(host => states.some(state => state.host_id === host.id));
    return (
        <div style={{ overflowX: "auto" }}>
            <Table variant="compact" aria-label={t("Hosts and checks")}>
                <Thead>
                    <Tr><Th>{t("Host")}</Th>{checks.map(([id, name]) => <Th key={id}>{name}</Th>)}</Tr>
                </Thead>
                <Tbody>
                    {rows.map(host => (
                        <Tr key={host.id}>
                            <Td dataLabel={t("Host")}>
                                <Button variant="link" isInline onClick={() => cockpit.location.go(["hosts", host.id])}>{host.name}</Button>
                            </Td>
                            {checks.map(([id, name]) => {
                                const state = cell.get(`${host.id} ${id}`);
                                return (
                                    <Td key={id} dataLabel={name}>
                                        {state && (
                                            <Tooltip content={state.summary || t("Not checked yet")}>
                                                <span tabIndex={0}><StateLabel state={state.state} /></span>
                                            </Tooltip>
                                        )}
                                    </Td>
                                );
                            })}
                        </Tr>
                    ))}
                </Tbody>
            </Table>
        </div>
    );
}

type View = "problems" | "hosts" | "grid";

export function HostsPage() {
    const { t } = useTranslation();
    const [view, setView] = useState<View>("problems");
    const [inventoryId, setInventoryId] = useState("");
    const [error, setError] = useState<string | null>(null);
    const inventories = useLoad(() => call<{ inventories: Inventory[] }>("inventory.list"));
    const data = useLoad(async () => {
        const filter = inventoryId ? { inventory_id: inventoryId } : {};
        // Only the grid shows every state: at hundreds of hosts they are thousands.
        const [states, hosts] = await Promise.all([
            call<{ states: CheckState[]; counts: StateCounts }>("state.list", { ...filter, problems: view !== "grid" }),
            call<{ hosts: FleetHost[] }>("host.list", filter),
        ]);
        return { ...states, hosts: hosts.hosts, every: view === "grid" };
    }, [inventoryId, view]);
    usePolling(() => data.reload(), POLL, true);

    return (
        <>
            <SectionHeader title={t("Hosts")}>
                {(inventories.data?.inventories.length ?? 0) > 1 && (
                    <FormSelect aria-label={t("Inventory")} value={inventoryId} style={{ minWidth: "12rem" }}
                        onChange={(_e, value) => setInventoryId(value)}>
                        <FormSelectOption value="" label={t("Every inventory")} />
                        {inventories.data!.inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                    </FormSelect>
                )}
                <ToggleGroup aria-label={t("View")}>
                    <ToggleGroupItem text={t("Problems")} isSelected={view === "problems"} onChange={() => setView("problems")} />
                    <ToggleGroupItem text={t("Hosts")} isSelected={view === "hosts"} onChange={() => setView("hosts")} />
                    <ToggleGroupItem text={t("Grid")} isSelected={view === "grid"} onChange={() => setView("grid")} />
                </ToggleGroup>
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} actionClose={<AlertActionCloseButton onClose={() => setError(null)} />} />}
            <ListState loaded={data} isEmpty={loaded => loaded.hosts.length === 0}
                emptyTitle={t("No hosts yet")}
                emptyBody={t("The hosts of an inventory are listed here once it has been read. Configure what to check on them in Checks.")}>
                {loaded => (
                    <>
                        <Counters counts={loaded.counts} />
                        {view === "problems" && <Problems states={loaded.states} onChanged={data.reload} onError={setError} />}
                        {view === "hosts" && <HostList hosts={loaded.hosts} />}
                        {view === "grid" && loaded.every && <Grid hosts={loaded.hosts} states={loaded.states} />}
                    </>
                )}
            </ListState>
        </>
    );
}

function Metrics({ state }: { state: CheckState }) {
    const { t } = useTranslation();
    if (state.metrics.length === 0) return <Content component="small">{t("No measures")}</Content>;
    return (
        <Table variant="compact" borders={false} aria-label={t("Measures")}>
            <Thead><Tr><Th>{t("Measure")}</Th><Th>{t("Of")}</Th><Th>{t("Value")}</Th></Tr></Thead>
            <Tbody>
                {state.metrics.map((metric, index) => (
                    <Tr key={index}>
                        <Td><code>{metric.name}</code></Td>
                        <Td>{metric.instance ?? ""}</Td>
                        <Td>{formatMetric(metric)}</Td>
                    </Tr>
                ))}
            </Tbody>
        </Table>
    );
}

export function HostDetailPage({ id }: { id: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const names = useStateNames();
    const host = useLoad(() => call<HostDetail>("host.get", { id }));
    const refs = useLoad(async () => {
        const [inventories, hosts] = await Promise.all([
            call<{ inventories: Inventory[] }>("inventory.list"),
            call<{ hosts: FleetHost[] }>("host.list"),
        ]);
        return { inventories: inventories.inventories, hosts: hosts.hosts };
    });
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [error, setError] = useState<string | null>(null);
    const [maintaining, setMaintaining] = useState(false);
    usePolling(() => host.reload(), POLL, true);

    if (host.error) return <Alert variant="danger" isInline title={host.error.message} />;
    if (!host.data) return null;
    const data = host.data;
    const toggle = (key: string) => setExpanded(current => {
        const next = new Set(current);
        if (next.has(key)) next.delete(key); else next.add(key);
        return next;
    });

    return (
        <>
            <SectionHeader title={data.name}>
                {can("maintenance.create") && (
                    <Button variant="secondary" onClick={() => setMaintaining(true)} isDisabled={!refs.data}>{t("Maintenance…")}</Button>
                )}
                <Button variant="secondary" onClick={() => cockpit.location.go(["hosts"])}>{t("All hosts")}</Button>
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {data.in_maintenance && <Alert variant="info" isInline title={t("This host is in maintenance: nothing about it is told until the window ends.")} style={{ marginBottom: "1rem" }} />}
            <Flex direction={{ default: "column" }} gap={{ default: "gapLg" }}>
                <FlexItem>
                    <DescriptionList isHorizontal isCompact columnModifier={{ default: "2Col" }}>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Inventory")}</DescriptionListTerm>
                            <DescriptionListDescription>{data.inventory}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Address")}</DescriptionListTerm>
                            <DescriptionListDescription>{data.address || data.name}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Groups")}</DescriptionListTerm>
                            <DescriptionListDescription>{data.groups.join(", ") || "—"}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Reachable")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {data.reachable === null ? t("Not known yet") : data.reachable ? t("Yes") : <Label isCompact color="red">{t("No")}</Label>}
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("System")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {data.os || t("Not known yet")}
                                {data.facts.kernel && <Content component="small"> · {data.facts.kernel} {data.facts.arch}</Content>}
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Last seen")}</DescriptionListTerm>
                            <DescriptionListDescription><Since seconds={data.facts_at} /></DescriptionListDescription>
                        </DescriptionListGroup>
                    </DescriptionList>
                </FlexItem>
                <FlexItem>
                    <Title headingLevel="h3" size="md" style={{ marginBlockEnd: "0.5rem" }}>{t("Checks")}</Title>
                    {data.checks.length === 0 ? (
                        <Content component="p">{t("No check applies to this host.")}</Content>
                    ) : (
                        <Table variant="compact" aria-label={t("Checks")}>
                            <Thead>
                                <Tr><Th screenReaderText={t("Details")} /><Th>{t("State")}</Th><Th>{t("Which check")}</Th><Th>{t("What it found")}</Th><Th>{t("Checked")}</Th><Th>{t("Next")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                            </Thead>
                            {bySeverity(data.checks).map((state, index) => {
                                const key = state.check_id;
                                const open = expanded.has(key);
                                return (
                                    <Tbody key={key} isExpanded={open}>
                                        <Tr>
                                            <ExpandCell isExpanded={open} onToggle={() => toggle(key)} />
                                            <Td dataLabel={t("State")}><StateLabel state={state.state} /><Flags state={state} /></Td>
                                            <Td dataLabel={t("Which check")}>{state.check}</Td>
                                            <Td dataLabel={t("What it found")}>{state.summary}</Td>
                                            <Td dataLabel={t("Checked")}><Since seconds={state.checked_at} /></Td>
                                            <Td dataLabel={t("Next")}><Since seconds={state.next_at} /></Td>
                                            <Td isActionCell><StateActions state={state} onChanged={host.reload} onError={setError} /></Td>
                                        </Tr>
                                        <Tr isExpanded={open}>
                                            <Td colSpan={7}>
                                                <ExpandableRowContent>
                                                    <Metrics state={state} />
                                                    {state.hard_state !== (state.state ?? "ok") && (
                                                        <Content component="small">{t("Confirmed state: {{state}}", { state: names[state.hard_state] })}</Content>
                                                    )}
                                                    {open && state.metrics.length > 0 && (
                                                        <Suspense fallback={<Spinner size="md" aria-label={t("Loading")} />}>
                                                            <MetricHistory hostId={id} checkId={state.check_id} version={state.checked_at} />
                                                        </Suspense>
                                                    )}
                                                </ExpandableRowContent>
                                            </Td>
                                        </Tr>
                                    </Tbody>
                                );
                            })}
                        </Table>
                    )}
                </FlexItem>
                <FlexItem>
                    <Title headingLevel="h3" size="md" style={{ marginBlockEnd: "0.5rem" }}>{t("What it is made of")}</Title>
                    <HostInventory hostId={id} />
                </FlexItem>
                <FlexItem>
                    <Title headingLevel="h3" size="md" style={{ marginBlockEnd: "0.5rem" }}>{t("Changes of state")}</Title>
                    {data.changes.length === 0 ? (
                        <Content component="p">{t("No change of state yet.")}</Content>
                    ) : (
                        <Table variant="compact" aria-label={t("Changes of state")}>
                            <Thead><Tr><Th>{t("When")}</Th><Th>{t("Which check")}</Th><Th>{t("State")}</Th><Th>{t("What it found")}</Th></Tr></Thead>
                            <Tbody>
                                {data.changes.map((change, index) => (
                                    <Tr key={index}>
                                        <Td dataLabel={t("When")}>{formatTime(change.ts, i18n.language)}</Td>
                                        <Td dataLabel={t("Which check")}>{change.check}</Td>
                                        <Td dataLabel={t("State")}>
                                            <StateLabel state={change.state} />
                                            {change.state_type === "soft" && <Label isCompact color="grey" style={{ marginInlineStart: "0.5rem" }}>{t("Not confirmed")}</Label>}
                                        </Td>
                                        <Td dataLabel={t("What it found")}>{change.summary}</Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                    )}
                </FlexItem>
            </Flex>
            {maintaining && refs.data && (
                <MaintenanceModal inventories={refs.data.inventories} hosts={refs.data.hosts} host={data}
                    onClose={() => setMaintaining(false)} onDone={host.reload} />
            )}
        </>
    );
}
