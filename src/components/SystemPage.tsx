import React, { useRef, useState } from "react";
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
    Title,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { formatBytes, formatInterval } from "../lib/checks.ts";
import { call } from "../lib/fleet.ts";
import { formatDuration, formatTime } from "../lib/format.ts";
import { keeping, keepingTone, share, type Keeping } from "../lib/health.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Check, CheckStatus, Health, HealthGroup, HealthLane, ImportReport } from "../lib/types.ts";
import { useKindNames } from "./ChecksPage.tsx";
import { message } from "./common.tsx";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";
import { Alert, Spinner } from "./pf.tsx";

const TONE: Record<CheckStatus, Tone> = { ok: "good", warn: "warning", fail: "bad" };
const ORDER: Record<CheckStatus, number> = { fail: 0, warn: 1, ok: 2 };

// How the controller keeps up: the check cycles against their intervals and
// their memory, the queue, the database.
function HealthSection() {
    const { t, i18n } = useTranslation();
    const kindNames = useKindNames() as Record<string, string>;
    const { data, error, loading, reload } = useLoad(() => call<Health>("system.health"));
    usePolling(reload, 30000, true);

    const keepingText: Record<Keeping, string> = {
        idle: t("No cycle yet"),
        ok: t("On time"),
        failing: t("Cycles failed"),
        memory: t("Over its memory"),
        slow: t("Slow"),
        late: t("Skipped due times"),
        behind: t("Falling behind"),
    };
    const laneText: Record<HealthLane["lane"], string> = {
        runs: t("Runs"),
        checks: t("Check cycles"),
        slow: t("Cycles of the slow kinds"),
    };
    const what = (group: HealthGroup) => group.runs === "checks" ? t("The other checks")
        : group.runs === "slow" ? kindNames[group.name] ?? group.name : group.name;
    const seconds = (value: number | null) => value === null ? "–" : formatDuration(value);

    return (
        <>
            <Flex justifyContent={{ default: "justifyContentSpaceBetween" }}>
                <FlexItem>
                    <Title headingLevel="h2" size="lg">{t("Health")}</Title>
                </FlexItem>
                <FlexItem>
                    <Button variant="secondary" onClick={reload} isLoading={loading} isDisabled={loading}>{t("Refresh")}</Button>
                </FlexItem>
            </Flex>
            {error && <Alert variant="danger" isInline title={error.message} />}
            {!data && loading && <Spinner aria-label={t("Loading")} />}
            {data && (
                <>
                    <Content component="p">
                        {t("Check cycles of the last day. A cycle should end within half the shortest interval of what it checks: one still running when its checks are due again makes them skip, and a group that skips {{times}} times in a row is falling behind, which the rules of notifications can tell about.", { times: data.late_after })}
                    </Content>
                    {data.groups.length === 0
                        ? <Content component="p">{t("No check is turned on.")}</Content>
                        : (
                            <Table variant="compact" aria-label={t("Check cycles")}>
                                <Thead>
                                    <Tr>
                                        <Th>{t("Inventory")}</Th>
                                        <Th>{t("Checks")}</Th>
                                        <Th>{t("Interval")}</Th>
                                        <Th>{t("Cycles")}</Th>
                                        <Th>{t("Duration, usual and longest")}</Th>
                                        <Th>{t("Memory peak")}</Th>
                                        <Th>{t("Skipped")}</Th>
                                        <Th>{t("Per cycle")}</Th>
                                        <Th>{t("Status")}</Th>
                                    </Tr>
                                </Thead>
                                <Tbody>
                                    {data.groups.map(group => {
                                        const state = keeping(group, data.memory.budget);
                                        const tone = keepingTone(state);
                                        const memory = share(group.memory_peak, data.memory.budget);
                                        return (
                                            <Tr key={`${group.inventory_id}/${group.group}`}>
                                                <Td dataLabel={t("Inventory")}>{group.inventory}</Td>
                                                <Td dataLabel={t("Checks")}>{what(group)}</Td>
                                                <Td dataLabel={t("Interval")}>{formatInterval(group.interval)}</Td>
                                                <Td dataLabel={t("Cycles")}>
                                                    {group.cycles}
                                                    {group.unsuccessful > 0 && <> ({t("cycles_unsuccessful_count", { count: group.unsuccessful })})</>}
                                                    {group.active > 0 && <> · {t("cycles_active_count", { count: group.active })}</>}
                                                </Td>
                                                <Td dataLabel={t("Duration, usual and longest")}>
                                                    {group.cycles ? `${seconds(group.duration_median)} / ${seconds(group.duration_max)}` : "–"}
                                                </Td>
                                                <Td dataLabel={t("Memory peak")}>
                                                    {group.memory_peak === null ? "–" : formatBytes(group.memory_peak)}
                                                    {memory !== null && <> ({t("{{share}}% of the budget", { share: memory })})</>}
                                                </Td>
                                                <Td dataLabel={t("Skipped")}>
                                                    {group.skipped}
                                                    {group.behind && <> · {t("{{streak}} in a row since {{since}}", { streak: group.behind.streak, since: formatTime(group.behind.since, i18n.language) })}</>}
                                                </Td>
                                                <Td dataLabel={t("Per cycle")}>
                                                    {t("{{forks}} forks, up to {{hosts}} hosts", { forks: group.sizing.forks, hosts: group.sizing.shard_size })}
                                                    {group.sizing.basis === "configured" && <> ({t("as configured")})</>}
                                                </Td>
                                                <Td dataLabel={t("Status")}>
                                                    {tone ? <StatusLabel tone={tone}>{keepingText[state]}</StatusLabel> : keepingText[state]}
                                                </Td>
                                            </Tr>
                                        );
                                    })}
                                </Tbody>
                            </Table>
                        )}
                    <Table variant="compact" aria-label={t("Queue")} style={{ marginBlockStart: "1rem" }}>
                        <Thead>
                            <Tr>
                                <Th>{t("Queue")}</Th>
                                <Th>{t("Running")}</Th>
                                <Th>{t("Waiting")}</Th>
                                <Th>{t("Longest wait")}</Th>
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.queue.map(lane => (
                                <Tr key={lane.lane}>
                                    <Td dataLabel={t("Queue")}>{laneText[lane.lane]}</Td>
                                    <Td dataLabel={t("Running")}>{t("{{running}} of {{limit}}", { running: lane.running, limit: lane.limit })}</Td>
                                    <Td dataLabel={t("Waiting")}>{lane.queued}</Td>
                                    <Td dataLabel={t("Longest wait")}>{seconds(lane.oldest_wait)}</Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                    <DescriptionList isHorizontal isCompact style={{ marginBlockStart: "1rem" }}>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Memory of one cycle")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {formatBytes(data.memory.budget)}{" "}
                                ({data.memory.configured_mb
                                    ? t("{{mb}} MB of the configuration shared by {{cycles}} cycles at once", { mb: data.memory.configured_mb, cycles: data.memory.cycles_at_once })
                                    : t("half the memory of the controller shared by {{cycles}} cycles at once", { cycles: data.memory.cycles_at_once })})
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Database")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {t("{{size}}, of which {{free}} free for new rows; write-ahead log {{wal}}", { size: formatBytes(data.database.bytes), free: formatBytes(data.database.free_bytes), wal: formatBytes(data.database.wal_bytes) })}
                                {!data.database.incremental && <> · {t("free space is not given back to the file system")}</>}
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                    </DescriptionList>
                </>
            )}
        </>
    );
}

// The configuration as one file, to keep it or to take it to another controller.
function Transfer() {
    const { t } = useTranslation();
    const chooser = useRef<HTMLInputElement>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [report, setReport] = useState<ImportReport | null>(null);

    async function act(action: () => Promise<void>) {
        setBusy(true);
        setError(null);
        try {
            await action();
        } catch (ex) {
            setError(message(ex));
        }
        setBusy(false);
    }

    const save = () => act(async () => {
        const document = await call<object>("config.export");
        const link = window.document.createElement("a");
        link.href = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2) + "\n"], { type: "application/json" }));
        link.download = "cockpit-fleet-config.json";
        link.click();
        URL.revokeObjectURL(link.href);
    });

    const load = (file: File) => act(async () => {
        let document: unknown;
        try {
            document = JSON.parse(await file.text());
        } catch {
            throw new Error(t("This file is not a configuration exported from here."));
        }
        setReport(await call<ImportReport>("config.import", { document }));
    });

    const outcomes = { created: t("Created"), exists: t("Already there"), failed: t("Not created") };
    const sections: Record<string, string> = {
        credentials: t("Credential"), projects: t("Project"), inventories: t("Inventory"), host_keys: t("Host key"), templates: t("Job template"),
        schedules: t("Schedule"), checks: t("Which check"), channels: t("Channel"), rules: t("Rule"),
    };

    return (
        <>
            <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} style={{ marginBlockStart: "2rem" }}>
                <FlexItem>
                    <Title headingLevel="h2" size="lg">{t("Configuration")}</Title>
                </FlexItem>
                <Flex gap={{ default: "gapSm" }}>
                    <Button variant="secondary" onClick={save} isDisabled={busy}>{t("Export")}</Button>
                    <Button variant="secondary" onClick={() => chooser.current?.click()} isDisabled={busy} isLoading={busy}>{t("Import…")}</Button>
                    <input ref={chooser} id="config-import-file" type="file" accept="application/json,.json" hidden
                        onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) load(file); }} />
                </Flex>
            </Flex>
            <Content component="p">{t("Credentials, projects, inventories, trusted host keys, job templates, schedules, notification channels and their rules, as one file. It holds no secret: after an import, enter the secrets of the credentials and of the channels again. The files of a project written here or uploaded are not in it.")}</Content>
            {error && <Alert variant="danger" isInline title={error} />}
            {report && (
                <>
                    <Alert variant={report.failed ? "warning" : "success"} isInline
                        title={t("Created {{created}}, already there {{exists}}, not created {{failed}}", { created: report.created, exists: report.exists, failed: report.failed })} />
                    {report.report.length > 0 && (
                        <Table variant="compact" aria-label={t("Import result")}>
                            <Thead><Tr><Th>{t("Kind")}</Th><Th>{t("Name")}</Th><Th>{t("Outcome")}</Th><Th>{t("Details")}</Th></Tr></Thead>
                            <Tbody>
                                {report.report.map((entry, index) => (
                                    <Tr key={index}>
                                        <Td dataLabel={t("Kind")}>{sections[entry.section] ?? entry.section}</Td>
                                        <Td dataLabel={t("Name")}>{entry.name}</Td>
                                        <Td dataLabel={t("Outcome")}>
                                            <StatusLabel tone={entry.outcome === "failed" ? "bad" : entry.outcome === "created" ? "good" : "warning"}>{outcomes[entry.outcome]}</StatusLabel>
                                        </Td>
                                        <Td dataLabel={t("Details")}>{entry.detail}</Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                    )}
                </>
            )}
        </>
    );
}

export function SystemPage() {
    const { t } = useTranslation();
    const can = useCan();
    const { data, error, loading, reload } = useLoad(() => call<{ checks: Check[] }>("selfcheck"));

    const labels: Record<string, string> = {
        ansible: t("Ansible"),
        systemd: t("systemd"),
        database: t("Database"),
        config: t("Configuration file"),
        state_dir: t("State directory"),
        secrets_dir: t("Secret store"),
        run_unit: t("Run unit"),
        scheduler: t("Scheduler"),
        notify: t("Notifications"),
        collection: t("Bundled Ansible collection"),
        project_roots: t("Directories of local projects"),
        git: t("Git"),
        sshpass: t("sshpass, for SSH logins with a password"),
    };
    const statusText: Record<CheckStatus, string> = {
        ok: t("OK"),
        warn: t("Warning"),
        fail: t("Problem"),
    };

    // Problems first, so they are not lost below a long list of passed checks.
    const checks = [...(data?.checks ?? [])].sort((a, b) => ORDER[a.status] - ORDER[b.status]);

    return (
        <>
            <HealthSection />
            <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} style={{ marginBlockStart: "2rem" }}>
                <FlexItem>
                    <Title headingLevel="h2" size="lg">{t("Installation checks")}</Title>
                </FlexItem>
                <FlexItem>
                    <Button variant="secondary" onClick={reload} isLoading={loading} isDisabled={loading}>
                        {t("Check again")}
                    </Button>
                </FlexItem>
            </Flex>
            {error && <Alert variant="danger" isInline title={error.message} />}
            {!data && loading && <Spinner aria-label={t("Loading")} />}
            {data && (
                <Table variant="compact" aria-label={t("Installation checks")}>
                    <Thead>
                        <Tr>
                            <Th>{t("Check")}</Th>
                            <Th>{t("Status")}</Th>
                            <Th>{t("Details")}</Th>
                        </Tr>
                    </Thead>
                    <Tbody>
                        {checks.map(check => (
                            <Tr key={check.id}>
                                <Td dataLabel={t("Check")}>{labels[check.id] ?? check.id}</Td>
                                <Td dataLabel={t("Status")}>
                                    <StatusLabel tone={TONE[check.status]}>{statusText[check.status]}</StatusLabel>
                                </Td>
                                <Td dataLabel={t("Details")}>{check.detail}</Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
            {can("config.import") && <Transfer />}
        </>
    );
}
