import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
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
    Radio,
    TextArea,
    TextInput,
    ToggleGroup,
    ToggleGroupItem,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { CheckCircleIcon, ExclamationCircleIcon, ExclamationTriangleIcon, OutlinedClockIcon, QuestionCircleIcon } from "@patternfly/react-icons";
import { call } from "../lib/fleet.ts";
import {
    filterChecks, formatInterval, fromLocalInput, groupChecks, isGroupOpen, KIND_CATEGORIES, linesOf, ownThresholds,
    readView, sortChecks, sumCounts, withGroupsOpen, withoutFilters, thresholdFields, thresholdsValid, toLocalInput,
    type CheckGroup, type ChecksView, type EnabledFilter, type KindCategory, type SortColumn, type StateFilter, type ThresholdField,
} from "../lib/checks.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { CatalogEntry, CheckDef, CheckKind, Credential, FleetHost, Inventory, Maintenance, Project, StateCounts, StateName } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { FilterChips, GroupByChoice, GroupRow, NothingMatches, useStoredView } from "./ListView.tsx";
import { ActionsColumn, Alert, SearchInput } from "./pf.tsx";

const LOOK = {
    ok: { color: "green", icon: <CheckCircleIcon /> },
    warning: { color: "orange", icon: <ExclamationTriangleIcon /> },
    critical: { color: "red", icon: <ExclamationCircleIcon /> },
    unknown: { color: "purple", icon: <QuestionCircleIcon /> },
    pending: { color: "grey", icon: <OutlinedClockIcon /> },
} as const;

export function useStateNames(): Record<StateName | "pending", string> {
    const { t } = useTranslation();
    return { ok: t("OK"), warning: t("Warning"), critical: t("Critical"), unknown: t("Unknown"), pending: t("Not checked yet") };
}

export function StateLabel({ state, count }: { state: StateName | null; count?: number }) {
    const names = useStateNames();
    const key = state ?? "pending";
    return (
        <Label color={LOOK[key].color} icon={LOOK[key].icon} isCompact>
            {count === undefined ? names[key] : `${count} ${names[key]}`}
        </Label>
    );
}

// The states of a set of checks, the worst first; nothing for a count of zero.
export function StateCountLabels({ counts }: { counts: StateCounts }) {
    const order: (StateName | "pending")[] = ["critical", "unknown", "warning", "ok", "pending"];
    return (
        <Flex gap={{ default: "gapXs" }}>
            {order.filter(state => counts[state] > 0).map(state => (
                <StateLabel key={state} state={state === "pending" ? null : state} count={counts[state]} />
            ))}
        </Flex>
    );
}

export function useKindNames(): Record<CheckKind, string> {
    const { t } = useTranslation();
    return {
        reachability: t("Reachability"), disk: t("Disk space"), cpu: t("Processor"), memory: t("Memory"),
        services: t("Services"), uptime: t("Uptime and reboot"), certificates: t("Certificate expiry"), clock: t("Clock"),
        cmdb: t("Hardware and software"), updates: t("Updates"),
        playbook: t("A playbook of a project"), compliance: t("Compliance of a playbook"),
    };
}

function InputGroupFlex({ children }: { children: React.ReactNode }) {
    return <Flex gap={{ default: "gapSm" }} style={{ marginBlockStart: "0.5rem" }}>{children}</Flex>;
}

function Help({ children }: { children: React.ReactNode }) {
    return <FormHelperText><HelperText><HelperTextItem>{children}</HelperTextItem></HelperText></FormHelperText>;
}

const MINUTE = 60;

// A check of the user has no entry in the catalog: no parameter, and no default
// threshold but that of compliance, on the tasks that would change.
const OWN: CatalogEntry = { name: "playbook", description: "", params: {}, required: [], thresholds: {}, slow: false, interval: 300 };
const COMPLIANCE: CatalogEntry = {
    ...OWN, name: "compliance", slow: true, interval: 3600, thresholds: { changed: { warning: 1, critical: null } },
};

function entryOf(catalog: CatalogEntry[], kind: CheckKind): CatalogEntry {
    return catalog.find(item => item.name === kind) ?? (kind === "compliance" ? COMPLIANCE : OWN);
}

function CheckModal({ check, catalog, inventories, credentials, hosts, projects, onClose, onDone }: {
    check?: CheckDef;
    catalog: CatalogEntry[];
    inventories: Inventory[];
    credentials: Credential[];
    hosts: FleetHost[];
    projects: Project[];
    onClose: () => void;
    onDone: () => void;
}) {
    const { t } = useTranslation();
    const kindNames = useKindNames();
    const [kind, setKind] = useState<CheckKind>(check?.kind ?? "disk");
    const entry = entryOf(catalog, kind);
    const own = kind === "playbook" || kind === "compliance";
    const [projectId, setProjectId] = useState(check?.project_id ?? projects[0]?.id ?? "");
    const [playbook, setPlaybook] = useState(check?.playbook ?? "");
    const [newMetric, setNewMetric] = useState("");
    const [name, setName] = useState(check?.name ?? "");
    const [inventoryId, setInventoryId] = useState(check?.inventory_id ?? inventories[0]?.id ?? "");
    const [group, setGroup] = useState(check?.group ?? "all");
    const [interval, setInterval] = useState(String((check?.interval ?? 300) / MINUTE));
    const [retry, setRetry] = useState(String(check?.retry_interval ?? 60));
    const [attempts, setAttempts] = useState(String(check?.max_attempts ?? 3));
    const [credentialId, setCredentialId] = useState(check?.machine_credential_id ?? "");
    const [become, setBecome] = useState(check?.become ?? false);
    const [enabled, setEnabled] = useState(check?.enabled ?? true);
    const [params, setParams] = useState<Record<string, string>>(() =>
        Object.fromEntries(Object.entries(check?.params ?? {}).filter(([, value]) => Array.isArray(value))
            .map(([key, value]) => [key, (value as string[]).join("\n")])));
    const [flags, setFlags] = useState<Record<string, boolean>>(() =>
        Object.fromEntries(Object.entries(check?.params ?? {}).filter(([, value]) => value === true)) as Record<string, boolean>);
    const [fields, setFields] = useState<Record<string, ThresholdField>>(() =>
        thresholdFields(check?.effective_thresholds ?? entry.thresholds));
    const groups = [...new Set(hosts.filter(host => host.inventory_id === inventoryId).flatMap(host => host.groups))].sort();
    const missing = entry.required.some(param => linesOf(params[param] ?? "").length === 0);

    function changeKind(value: CheckKind) {
        const next = entryOf(catalog, value);
        setKind(value);
        setParams({});
        setFlags({});
        setFields(thresholdFields(next.thresholds));
        setInterval(String(next.interval / MINUTE));
    }

    async function submit() {
        const values = {
            name: name.trim(), inventory_id: inventoryId, group, interval: Math.round(Number(interval) * MINUTE),
            retry_interval: Number(retry), max_attempts: Number(attempts), become, enabled,
            params: Object.fromEntries(Object.entries(entry.params).map(([key, param]) =>
                [key, param.kind === "flag" ? flags[key] ?? false : linesOf(params[key] ?? "")])),
            thresholds: ownThresholds(entry.thresholds, fields),
            ...(credentialId ? { machine_credential_id: credentialId } : {}),
            ...(own ? { project_id: projectId, playbook: playbook.trim() } : {}),
        };
        await call(check ? "check.update" : "check.create", check ? { id: check.id, ...values } : { kind, ...values });
        onDone();
        onClose();
    }

    const thresholdNames: Record<string, string> = {
        used_pct: t("Space used (%)"), inodes_used_pct: t("Inodes used (%)"), cpu_pct: t("Processor busy (%)"),
        memory_used_pct: t("Memory used (%)"), swap_used_pct: t("Swap used (%)"), not_running: t("Services not running"),
        reboot_required: t("Reboot required (1 = yes)"), days_left: t("Days before expiry"), offset: t("Offset (seconds)"),
        security_updates: t("Security updates"), updates: t("Updates"), changed: t("Tasks that would change"),
    };
    const descriptions: Record<CheckKind, string> = {
        reachability: t("Whether Ansible reaches the host. A host it does not reach has its other checks unknown, and only this one is told about: apply it to every host."),
        disk: t("Space and inodes used on each filesystem of the host, each fixed drive on Windows."),
        cpu: t("Processor time used over one second (its load on Windows), and the load averages on Linux."),
        memory: t("Memory and swap in use."),
        services: t("Whether the services named are running: systemd units on Linux, services on Windows."),
        uptime: t("Time since the last boot, and whether updates wait for a reboot."),
        certificates: t("Days before certificates expire: files, directories of them, or a Windows store."),
        clock: t("How far the clock of the host is from the controller's, network delay included."),
        cmdb: t("What the host is made of: hardware, installed packages and services, kept in Assets with their changes. A change of hardware is a warning for one cycle."),
        updates: t("Updates available, security ones apart: apt, dnf and zypper, or Windows Update, searched without installing anything."),
        playbook: t("A check of your own: a playbook of a project whose last task, lineadicomando.fleet.fleet_result, gives a state, a summary and metrics for each host."),
        compliance: t("A playbook of a project that describes how the hosts should be, run in check mode: a host on which a task would change something has drifted. The differences are kept, for operators."),
    };
    const paramHelp: Record<string, string> = {
        mounts: t("All of them when empty; C: and D: on Windows."),
        exclude_types: t("Beyond the pseudo filesystems, which are always left out."),
        names: t("Unit names on Linux (ssh, cron), service names on Windows (sshd, W32Time)."),
        paths: t("Files in PEM or DER, directories of them, or a store such as Cert:\\LocalMachine\\My."),
        refresh: t("Without it the metadata the host already has is used. Not needed on Windows."),
    };
    const paramNames: Record<string, string> = {
        mounts: t("Only these mount points"), exclude_types: t("Leave out these filesystem types"),
        names: t("Services"), paths: t("Certificates"), refresh: t("Refresh the package metadata first (needs become)"),
    };

    return (
        <FormModal title={check ? t("Edit check") : t("New check")} submitLabel={check ? t("Save") : t("Create")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || !inventoryId || missing || !thresholdsValid(fields) || !(Number(interval) >= 1) || (own && (!projectId || !playbook.trim()))}>
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("What to check")} isRequired fieldId="chk-kind">
                        <FormSelect id="chk-kind" value={kind} onChange={(_e, value) => changeKind(value as CheckKind)} isDisabled={!!check}>
                            {catalog.map(item => <FormSelectOption key={item.name} value={item.name} label={kindNames[item.name] ?? item.name} />)}
                            <FormSelectOption value="playbook" label={kindNames.playbook} />
                            <FormSelectOption value="compliance" label={kindNames.compliance} />
                        </FormSelect>
                        <Help>{descriptions[kind]}</Help>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Name")} isRequired fieldId="chk-name">
                        <TextInput id="chk-name" value={name} onChange={(_e, value) => setName(value)} isRequired placeholder={kindNames[kind]} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Inventory")} isRequired fieldId="chk-inventory">
                        <FormSelect id="chk-inventory" value={inventoryId} onChange={(_e, value) => { setInventoryId(value); setGroup("all"); }}>
                            {inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Hosts")} isRequired fieldId="chk-group">
                        <FormSelect id="chk-group" value={group} onChange={(_e, value) => setGroup(value)}>
                            <FormSelectOption value="all" label={t("Every host")} />
                            {groups.map(item => <FormSelectOption key={item} value={item} label={t("Group {{group}}", { group: item })} />)}
                        </FormSelect>
                        {groups.length === 0 && <Help>{t("The groups appear once the inventory has been read.")}</Help>}
                    </FormGroup>
                </GridItem>
            </Grid>
            {own && (
                <Grid hasGutter md={6}>
                    <GridItem>
                        <FormGroup label={t("Project")} isRequired fieldId="chk-project">
                            <FormSelect id="chk-project" value={projectId} onChange={(_e, value) => setProjectId(value)}>
                                {projects.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                            </FormSelect>
                        </FormGroup>
                    </GridItem>
                    <GridItem>
                        <FormGroup label={t("Playbook")} isRequired fieldId="chk-playbook">
                            <TextInput id="chk-playbook" value={playbook} onChange={(_e, value) => setPlaybook(value)}
                                placeholder={kind === "compliance" ? "baseline.yml" : "checks/mail_queue.yml"} />
                        </FormGroup>
                    </GridItem>
                </Grid>
            )}
            {Object.entries(entry.params).filter(([, param]) => param.kind === "flag").map(([key]) => (
                <FormGroup key={key} fieldId={`chk-param-${key}`}>
                    <Checkbox id={`chk-param-${key}`} label={paramNames[key] ?? key} isChecked={flags[key] ?? false}
                        onChange={(_e, value) => setFlags(current => ({ ...current, [key]: value }))} />
                    <Help>{paramHelp[key] ?? ""}</Help>
                </FormGroup>
            ))}
            {Object.entries(entry.params).filter(([, param]) => param.kind !== "flag").map(([key]) => (
                <FormGroup key={key} label={paramNames[key] ?? key} isRequired={entry.required.includes(key)} fieldId={`chk-param-${key}`}>
                    <TextArea id={`chk-param-${key}`} value={params[key] ?? ""} rows={3} resizeOrientation="vertical"
                        onChange={(_e, value) => setParams(current => ({ ...current, [key]: value }))} />
                    <Help>{paramHelp[key] ?? ""} {t("One per line.")}</Help>
                </FormGroup>
            ))}
            <Grid hasGutter md={4}>
                <GridItem>
                    <FormGroup label={t("Every (minutes)")} isRequired fieldId="chk-interval">
                        <TextInput id="chk-interval" type="number" min={1} value={interval} onChange={(_e, value) => setInterval(value)} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Attempts before a problem")} fieldId="chk-attempts">
                        <TextInput id="chk-attempts" type="number" min={1} max={10} value={attempts} onChange={(_e, value) => setAttempts(value)} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Meanwhile, every (seconds)")} fieldId="chk-retry">
                        <TextInput id="chk-retry" type="number" min={30} value={retry} onChange={(_e, value) => setRetry(value)} />
                    </FormGroup>
                </GridItem>
            </Grid>
            <Help>{t("A problem is confirmed, and told about, once it has been seen that many times in a row; until then it is checked again more often. Checks run on the minute, so shorter times are rounded up to it.")}</Help>
            {(Object.keys(fields).length > 0 || own) && (
                <FormGroup label={t("Thresholds")} role="group" fieldId="chk-thresholds">
                    <Table variant="compact" borders={false} aria-label={t("Thresholds")}>
                        <Thead><Tr><Th>{t("Measure")}</Th><Th>{t("Warning at")}</Th><Th>{t("Critical at")}</Th></Tr></Thead>
                        <Tbody>
                            {Object.entries(fields).map(([metric, field]) => (
                                <Tr key={metric}>
                                    <Td>{thresholdNames[metric] ?? metric}</Td>
                                    {(["warning", "critical"] as const).map(level => (
                                        <Td key={level}>
                                            <TextInput aria-label={`${metric} ${level}`} value={field[level]} style={{ maxWidth: "8rem" }}
                                                onChange={(_e, value) => setFields(current => ({ ...current, [metric]: { ...current[metric], [level]: value } }))} />
                                        </Td>
                                    ))}
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                    {kind === "playbook" && (
                        <InputGroupFlex>
                            <TextInput aria-label={t("Name of a metric")} value={newMetric} placeholder="queued" style={{ maxWidth: "16rem" }}
                                onChange={(_e, value) => setNewMetric(value.trim())} />
                            <Button variant="secondary" isDisabled={!/^[a-z][a-z0-9_]{0,63}$/.test(newMetric) || newMetric in fields}
                                onClick={() => { setFields(current => ({ ...current, [newMetric]: { warning: "", critical: "" } })); setNewMetric(""); }}>
                                {t("Add a threshold")}
                            </Button>
                        </InputGroupFlex>
                    )}
                    <Help>{kind === "playbook"
                        ? t("For the metrics the playbook gives to fleet_result, by name: lowercase letters, digits and _. The state it gives itself counts too: the worse of the two is kept.")
                        : t("Empty means no threshold. When the critical one is below the warning, lower is worse, as for the days before a certificate expires.")}</Help>
                </FormGroup>
            )}
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("Machine credential")} fieldId="chk-credential">
                        <FormSelect id="chk-credential" value={credentialId} onChange={(_e, value) => setCredentialId(value)}>
                            <FormSelectOption value="" label={t("None: as the inventory says")} />
                            {credentials.filter(item => item.kind === "machine").map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup fieldId="chk-flags" role="group" label=" ">
                        <Checkbox id="chk-become" label={t("Become root (some checks need it)")} isChecked={become} onChange={(_e, value) => setBecome(value)} />
                        <Checkbox id="chk-enabled" label={t("Enabled")} isChecked={enabled} onChange={(_e, value) => setEnabled(value)} />
                    </FormGroup>
                </GridItem>
            </Grid>
        </FormModal>
    );
}

export function MaintenanceModal({ inventories, hosts, host, onClose, onDone }: {
    inventories: Inventory[];
    hosts: FleetHost[];
    // When opened from a host: the window is for it.
    host?: FleetHost;
    onClose: () => void;
    onDone: () => void;
}) {
    const { t } = useTranslation();
    const now = Math.floor(Date.now() / 1000);
    const [inventoryId, setInventoryId] = useState(host?.inventory_id ?? inventories[0]?.id ?? "");
    const [target, setTarget] = useState<"host" | "group">("host");
    const [hostId, setHostId] = useState(host?.id ?? "");
    const [group, setGroup] = useState("all");
    const [starts, setStarts] = useState(toLocalInput(now));
    const [ends, setEnds] = useState(toLocalInput(now + 3600));
    const [comment, setComment] = useState("");
    const ofInventory = hosts.filter(item => item.inventory_id === inventoryId);
    const groups = [...new Set(ofInventory.flatMap(item => item.groups))].sort();
    const startsAt = fromLocalInput(starts);
    const endsAt = fromLocalInput(ends);

    async function submit() {
        await call("maintenance.create", {
            inventory_id: inventoryId, comment: comment.trim(), starts_at: startsAt, ends_at: endsAt,
            ...(target === "host" ? { host_id: hostId } : { group }),
        });
        onDone();
        onClose();
    }

    return (
        <FormModal title={host ? t("Maintenance of {{host}}", { host: host.name }) : t("New maintenance window")} submitLabel={t("Create")}
            onSubmit={submit} onClose={onClose}
            isDisabled={!inventoryId || (target === "host" && !hostId) || startsAt === null || endsAt === null || endsAt <= Math.max(startsAt, now)}>
            <Content component="p">{t("While a host is in maintenance nothing about it is told. Its checks still run, and what is still a problem when the window ends is told then.")}</Content>
            {!host && (
                <>
                    <FormGroup label={t("Inventory")} isRequired fieldId="mnt-inventory">
                        <FormSelect id="mnt-inventory" value={inventoryId} onChange={(_e, value) => { setInventoryId(value); setHostId(""); setGroup("all"); }}>
                            {inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FormGroup>
                    <FormGroup role="radiogroup" fieldId="mnt-target" label={t("For")}>
                        <Radio id="mnt-host" name="mnt-target" label={t("One host")} isChecked={target === "host"} onChange={() => setTarget("host")} />
                        <Radio id="mnt-group" name="mnt-target" label={t("A group of hosts")} isChecked={target === "group"} onChange={() => setTarget("group")} />
                    </FormGroup>
                    {target === "host" ? (
                        <FormGroup label={t("Host")} isRequired fieldId="mnt-hostid">
                            <FormSelect id="mnt-hostid" value={hostId} onChange={(_e, value) => setHostId(value)}>
                                <FormSelectOption value="" label={t("Choose…")} isPlaceholder />
                                {ofInventory.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                            </FormSelect>
                        </FormGroup>
                    ) : (
                        <FormGroup label={t("Group")} isRequired fieldId="mnt-groupname">
                            <FormSelect id="mnt-groupname" value={group} onChange={(_e, value) => setGroup(value)}>
                                <FormSelectOption value="all" label={t("Every host")} />
                                {groups.map(item => <FormSelectOption key={item} value={item} label={item} />)}
                            </FormSelect>
                        </FormGroup>
                    )}
                </>
            )}
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("Begins")} isRequired fieldId="mnt-starts">
                        <TextInput id="mnt-starts" type="datetime-local" value={starts} onChange={(_e, value) => setStarts(value)} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Ends")} isRequired fieldId="mnt-ends">
                        <TextInput id="mnt-ends" type="datetime-local" value={ends} onChange={(_e, value) => setEnds(value)} />
                    </FormGroup>
                </GridItem>
            </Grid>
            <FormGroup label={t("Why")} fieldId="mnt-comment">
                <TextInput id="mnt-comment" value={comment} onChange={(_e, value) => setComment(value)} placeholder={t("Disk replacement")} />
            </FormGroup>
        </FormModal>
    );
}

function useCategoryNames(): Record<KindCategory, string> {
    const { t } = useTranslation();
    return {
        resources: t("Resources"), system: t("Services and system"), inventory: t("Inventory and compliance"), own: t("Playbooks of your own"),
    };
}

// The filters and the grouping above the list of checks, and what they leave out.
function ChecksToolbar({ view, onChange, inventories, groups, shown, total }: {
    view: ChecksView;
    onChange: (view: ChecksView) => void;
    inventories: { id: string; name: string }[];
    // The groups the table shows, to open or close them all.
    groups: CheckGroup[];
    shown: number;
    total: number;
}) {
    const { t } = useTranslation();
    const categoryNames = useCategoryNames();
    const set = (change: Partial<ChecksView>) => onChange({ ...view, ...change });
    const stateNames: Record<Exclude<StateFilter, "">, string> = {
        problems: t("With problems"), critical: t("Critical only"), ok: t("All OK"), pending: t("Not checked yet"),
    };
    const enabledNames: Record<Exclude<EnabledFilter, "">, string> = { enabled: t("Enabled only"), disabled: t("Not enabled only") };
    const allOpen = groups.every(group => isGroupOpen(view, group, groups.length));
    const inventoryName = inventories.find(item => item.id === view.inventory)?.name ?? "";
    const chip = (key: string, text: string, clear: Partial<ChecksView>) => ({ key, text, onClear: () => set(clear) });
    const chips = [
        ...(view.search.trim() ? [chip("search", t("Text: {{text}}", { text: view.search.trim() }), { search: "" })] : []),
        ...(view.state ? [chip("state", t("State: {{value}}", { value: stateNames[view.state] }), { state: "" })] : []),
        ...(view.inventory ? [chip("inventory", t("Inventory: {{value}}", { value: inventoryName }), { inventory: "" })] : []),
        ...(view.category ? [chip("category", t("Kind: {{value}}", { value: categoryNames[view.category] }), { category: "" })] : []),
        ...(view.enabled ? [chip("enabled", enabledNames[view.enabled], { enabled: "" })] : []),
    ];

    return (
        <div style={{ marginBlockEnd: "1rem" }}>
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                <FlexItem style={{ width: "18rem" }}>
                    <SearchInput aria-label={t("Filter checks")} placeholder={t("Name, kind, inventory, group…")} value={view.search}
                        onChange={(_e, value) => set({ search: value })} onClear={() => set({ search: "" })} />
                </FlexItem>
                <FlexItem style={{ width: "11rem" }}>
                    <FormSelect aria-label={t("Filter by state")} value={view.state} onChange={(_e, value) => set({ state: value as StateFilter })}>
                        <FormSelectOption value="" label={t("Any state")} />
                        {Object.entries(stateNames).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                    </FormSelect>
                </FlexItem>
                {inventories.length > 1 && (
                    <FlexItem style={{ width: "12rem" }}>
                        <FormSelect aria-label={t("Inventory")} value={view.inventory} onChange={(_e, value) => set({ inventory: value })}>
                            <FormSelectOption value="" label={t("Every inventory")} />
                            {inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FlexItem>
                )}
                <FlexItem style={{ width: "13rem" }}>
                    <FormSelect aria-label={t("Filter by kind")} value={view.category} onChange={(_e, value) => set({ category: value as ChecksView["category"] })}>
                        <FormSelectOption value="" label={t("Any kind")} />
                        {KIND_CATEGORIES.map(item => <FormSelectOption key={item} value={item} label={categoryNames[item]} />)}
                    </FormSelect>
                </FlexItem>
                <FlexItem style={{ width: "11rem" }}>
                    <FormSelect aria-label={t("Enabled or not")} value={view.enabled} onChange={(_e, value) => set({ enabled: value as EnabledFilter })}>
                        <FormSelectOption value="" label={t("Enabled or not")} />
                        {Object.entries(enabledNames).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                    </FormSelect>
                </FlexItem>
                <GroupByChoice id="chk-group-by" value={view.groupBy} onChange={groupBy => set({ groupBy })}
                    options={[["inventory", t("Inventory")], ["kind", t("Kind")], ["state", t("Worst state")], ["none", t("Nothing")]]}
                    groups={groups.length} allOpen={allOpen} onToggleAll={() => onChange(withGroupsOpen(view, groups, !allOpen))} />
            </Flex>
            <FilterChips chips={chips} onClearAll={() => onChange(withoutFilters(view))} shown={shown} total={total} />
        </div>
    );
}

function ChecksTable({ groups, view, onChange, canRun, canEdit, canEditNow, onRun, onEdit, onDelete }: {
    groups: CheckGroup[];
    view: ChecksView;
    onChange: (view: ChecksView) => void;
    canRun: boolean;
    canEdit: boolean;
    // The lists a form needs have arrived.
    canEditNow: boolean;
    onRun: (check: CheckDef) => void;
    onEdit: (check: CheckDef) => void;
    onDelete: (check: CheckDef) => void;
}) {
    const { t } = useTranslation();
    const kindNames = useKindNames();
    const categoryNames = useCategoryNames();
    const stateNames = useStateNames();
    const grouped = view.groupBy !== "none";
    const columns: SortColumn[] = ["name", "interval", "states"];
    const width = grouped ? 8 : 7;

    function sortBy(column: SortColumn, index: number) {
        return {
            columnIndex: index,
            sortBy: { index: columns.indexOf(view.sort), direction: view.descending ? "desc" as const : "asc" as const },
            onSort: (_e: unknown, _index: number, direction: "asc" | "desc") => onChange({ ...view, sort: column, descending: direction === "desc" }),
        };
    }

    const isOpen = (group: CheckGroup) => isGroupOpen(view, group, groups.length);
    const toggle = (group: CheckGroup) => onChange(withGroupsOpen(view, [group], !isOpen(group)));

    function title(group: CheckGroup): string {
        if (view.groupBy === "inventory") return group.name;
        if (view.groupBy === "kind") return categoryNames[group.key as KindCategory];
        return group.key === "disabled" ? t("Not enabled") : stateNames[group.key as StateName | "pending"];
    }

    const row = (check: CheckDef, open: boolean) => (
        <Tr key={check.id} isExpanded={grouped ? open : undefined}>
            {grouped && <Td />}
            <Td dataLabel={t("Name")}>
                {check.name}
                {!check.enabled && <Label isCompact color="grey" style={{ marginInlineStart: "0.5rem" }}>{t("Not enabled")}</Label>}
            </Td>
            <Td dataLabel={t("What")}>{kindNames[check.kind] ?? check.kind}</Td>
            <Td dataLabel={t("Hosts")}>
                {check.inventory}{check.group !== "all" && <Content component="small"> / {check.group}</Content>}
            </Td>
            <Td dataLabel={t("Every")}>{formatInterval(check.interval)}</Td>
            <Td dataLabel={t("States")}><StateCountLabels counts={check.states} /></Td>
            <Td isActionCell style={{ whiteSpace: "nowrap" }}>
                {canRun && check.enabled && <Button variant="secondary" size="sm" onClick={() => onRun(check)}>{t("Check now")}</Button>}
            </Td>
            <Td isActionCell>
                {canEdit && (
                    <ActionsColumn items={[
                        { title: t("Edit"), onClick: () => onEdit(check), isDisabled: !canEditNow },
                        { title: t("Delete"), onClick: () => onDelete(check) },
                    ]} />
                )}
            </Td>
        </Tr>
    );

    return (
        <Table variant="compact" aria-label={t("Checks")}>
            <Thead>
                <Tr>
                    {grouped && <Th screenReaderText={t("Group")} />}
                    <Th sort={sortBy("name", 0)}>{t("Name")}</Th><Th>{t("What")}</Th><Th>{t("Hosts")}</Th>
                    <Th sort={sortBy("interval", 1)}>{t("Every")}</Th><Th sort={sortBy("states", 2)}>{t("States")}</Th>
                    <Th screenReaderText={t("Check now")} /><Th screenReaderText={t("Actions")} />
                </Tr>
            </Thead>
            {grouped ? groups.map(group => {
                const open = isOpen(group);
                return (
                    <Tbody key={group.key} isExpanded={open}>
                        <GroupRow title={title(group)} count={t("checks_count", { count: group.items.length })} open={open}
                            onToggle={() => toggle(group)} width={width}>
                            <StateCountLabels counts={sumCounts(group.items)} />
                        </GroupRow>
                        {group.items.map(check => row(check, open))}
                    </Tbody>
                );
            }) : <Tbody>{groups[0].items.map(check => row(check, true))}</Tbody>}
        </Table>
    );
}

export function ChecksPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const kindNames = useKindNames();
    const [stored, setView] = useStoredView("checks-view", readView);
    const list = useLoad(() => call<{ checks: CheckDef[] }>("check.list"));
    const windows = useLoad(() => call<{ maintenances: Maintenance[]; now: number }>("maintenance.list"));
    const refs = useLoad(async () => {
        const [catalog, inventories, hosts, credentials, projects] = await Promise.all([
            call<{ catalog: CatalogEntry[] }>("check.catalog"),
            call<{ inventories: Inventory[] }>("inventory.list"),
            call<{ hosts: FleetHost[] }>("host.list"),
            call<{ credentials: Credential[] }>("credential.list"),
            call<{ projects: Project[] }>("project.list"),
        ]);
        return { catalog: catalog.catalog, inventories: inventories.inventories, hosts: hosts.hosts, credentials: credentials.credentials, projects: projects.projects };
    });
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<CheckDef | null>(null);
    const [deleting, setDeleting] = useState<CheckDef | null>(null);
    const [opening, setOpening] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    // The inventories that have checks; a stored one that has none any more is not a filter.
    const inventories = [...new Map((list.data?.checks ?? []).map(check => [check.inventory_id, check.inventory])).entries()]
        .map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
    const view = !list.data || stored.inventory === "" || inventories.some(item => item.id === stored.inventory)
        ? stored : { ...stored, inventory: "" };

    async function runNow(check: CheckDef) {
        setError(null);
        setNotice(null);
        try {
            await call("check.run", { id: check.id });
            setNotice(t("{{name}} is being checked on its hosts: the results appear in Hosts.", { name: check.name }));
        } catch (ex) {
            setError(message(ex));
        }
    }

    async function close(item: Maintenance) {
        setError(null);
        try {
            await call("maintenance.delete", { id: item.id });
        } catch (ex) {
            setError(message(ex));
        }
        windows.reload();
    }

    function reloadAll() {
        list.reload();
        refs.reload();
    }

    const now = windows.data?.now ?? Date.now() / 1000;
    const current = (windows.data?.maintenances ?? []).filter(item => item.starts_at <= now && now < item.ends_at).length;
    const planned = (windows.data?.maintenances ?? []).filter(item => now < item.starts_at).length;

    return (
        <>
            <SectionHeader title={t("Checks")}>
                {can("check.create") && (
                    <Button variant="primary" onClick={() => setCreating(true)} isDisabled={!refs.data || refs.data.inventories.length === 0}>{t("New check")}</Button>
                )}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {notice && <Alert variant="info" isInline title={notice} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.checks.length === 0}
                emptyTitle={t("No checks")}
                emptyBody={t("A check looks at the hosts of a group of an inventory at regular intervals: disk space, memory, services, certificates. Ansible does it over the connection the inventory gives, with nothing to install on the hosts.")}>
                {data => {
                    const shown = filterChecks(data.checks, view, kind => kindNames[kind] ?? kind);
                    const groups = groupChecks(sortChecks(shown, view.sort, view.descending), view.groupBy);
                    return (
                        <>
                            <ChecksToolbar view={view} onChange={setView} inventories={inventories} groups={groups} shown={shown.length} total={data.checks.length} />
                            {shown.length === 0 ? (
                                <NothingMatches title={t("No check matches the filters")} onClearAll={() => setView(withoutFilters(view))} />
                            ) : (
                                <ChecksTable groups={groups} view={view} onChange={setView}
                                    canRun={can("check.run")} canEdit={can("check.update")} canEditNow={!!refs.data}
                                    onRun={runNow} onEdit={setEditing} onDelete={setDeleting} />
                            )}
                        </>
                    );
                }}
            </ListState>

            <div style={{ marginBlockStart: "2rem" }}>
                <SectionHeader title={t("Maintenance windows")}>
                    {windows.data && windows.data.maintenances.length > 0 && (
                        <>
                            <Content component="small" style={{ alignSelf: "center" }}>
                                {t("Now: {{now}} · Planned: {{planned}}", { now: current, planned })}
                            </Content>
                            <ToggleGroup aria-label={t("Which maintenance windows")} isCompact>
                                <ToggleGroupItem text={t("Current and planned")} isSelected={!view.ended} onChange={() => setView({ ...view, ended: false })} />
                                <ToggleGroupItem text={t("Ended too")} isSelected={view.ended} onChange={() => setView({ ...view, ended: true })} />
                            </ToggleGroup>
                        </>
                    )}
                    {can("maintenance.create") && (
                        <Button variant="secondary" onClick={() => setOpening(true)} isDisabled={!refs.data || refs.data.hosts.length === 0}>{t("New maintenance window")}</Button>
                    )}
                </SectionHeader>
                <ListState loaded={windows} isEmpty={data => data.maintenances.length === 0}
                    emptyTitle={t("No maintenance windows")}
                    emptyBody={t("During a maintenance window nothing is told about its hosts: for a planned reboot, or work on a disk.")}>
                    {data => {
                        const shown = data.maintenances.filter(item => view.ended || now < item.ends_at);
                        if (shown.length === 0)
                            return <Content component="p">{t("No window is open or planned. Choose \"Ended too\" to see those that have ended.")}</Content>;
                        return (
                            <Table variant="compact" aria-label={t("Maintenance windows")}>
                                <Thead>
                                    <Tr><Th>{t("For")}</Th><Th>{t("Begins")}</Th><Th>{t("Ends")}</Th><Th>{t("Why")}</Th><Th>{t("By")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                                </Thead>
                                <Tbody>
                                    {shown.map(item => {
                                        const open = item.starts_at <= now && now < item.ends_at;
                                        const over = item.ends_at <= now;
                                        return (
                                            <Tr key={item.id}>
                                                <Td dataLabel={t("For")}>
                                                    {item.host_id ? item.host : (item.group === "all" ? t("Every host") : t("Group {{group}}", { group: item.group }))}
                                                    <Content component="small"> ({item.inventory})</Content>
                                                    {open && <Label isCompact color="blue" style={{ marginInlineStart: "0.5rem" }}>{t("Now")}</Label>}
                                                    {over && <Label isCompact color="grey" style={{ marginInlineStart: "0.5rem" }}>{t("Ended")}</Label>}
                                                </Td>
                                                <Td dataLabel={t("Begins")}>{formatTime(item.starts_at, i18n.language)}</Td>
                                                <Td dataLabel={t("Ends")}>{formatTime(item.ends_at, i18n.language)}</Td>
                                                <Td dataLabel={t("Why")}>{item.comment}</Td>
                                                <Td dataLabel={t("By")}>{item.created_by}</Td>
                                                <Td isActionCell>
                                                    {can("maintenance.delete") && !over && (
                                                        <Button variant="secondary" size="sm" onClick={() => close(item)}>{open ? t("End now") : t("Take back")}</Button>
                                                    )}
                                                </Td>
                                            </Tr>
                                        );
                                    })}
                                </Tbody>
                            </Table>
                        );
                    }}
                </ListState>
            </div>

            {(creating || editing) && refs.data && (
                <CheckModal check={editing ?? undefined} catalog={refs.data.catalog} inventories={refs.data.inventories}
                    credentials={refs.data.credentials} hosts={refs.data.hosts} projects={refs.data.projects}
                    onClose={() => { setCreating(false); setEditing(null); }} onDone={reloadAll} />
            )}
            {opening && refs.data && (
                <MaintenanceModal inventories={refs.data.inventories} hosts={refs.data.hosts} onClose={() => setOpening(false)} onDone={windows.reload} />
            )}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The check is deleted, with the states of its hosts and their history.")}
                    onDelete={async () => { await call("check.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
