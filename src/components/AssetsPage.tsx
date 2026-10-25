import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Content,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    Flex,
    FormSelect,
    FormSelectOption,
    Label,
    Tab,
    TabTitleText,
    Title,
    Tooltip,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { formatBytes } from "../lib/checks.ts";
import { bySystem, download, hostRows, matches, toCsv } from "../lib/cmdb.ts";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { CmdbChange, CmdbDetail, CmdbHost, Drift, Inventory, PackageHit, Template } from "../lib/types.ts";
import { ListState, SectionHeader, message } from "./common.tsx";
import { LaunchModal } from "./TemplatesPage.tsx";
import { Alert, SearchInput, TabPanel, Tabs } from "./pf.tsx";

// Long lists are shown this many rows at a time, with a filter.
const PAGE = 200;

function Hardware({ host }: { host: CmdbHost }) {
    const hardware = host.hardware;
    const what = [hardware.vendor, hardware.model].filter(Boolean).join(" ");
    return (
        <>
            {what}
            {hardware.virtual && <Label isCompact color="grey" style={{ marginInlineStart: "0.5rem" }}>{hardware.virtual}</Label>}
        </>
    );
}

function Updates({ host }: { host: CmdbHost }) {
    const { t } = useTranslation();
    if (!host.hardware.updates_at) return null;
    if (host.updates === 0) return <Label isCompact color="green">{t("Up to date")}</Label>;
    return (
        <Flex gap={{ default: "gapXs" }}>
            <Label isCompact color="blue">{t("updates_count", { count: host.updates })}</Label>
            {host.security_updates > 0 && <Label isCompact color="orange">{t("security_updates_count", { count: host.security_updates })}</Label>}
        </Flex>
    );
}

export function Compliance({ compliant }: { compliant: boolean | null }) {
    const { t } = useTranslation();
    if (compliant === null) return null;
    return compliant
        ? <Label isCompact color="green">{t("Compliant")}</Label>
        : <Label isCompact color="orange">{t("Drifted")}</Label>;
}

function PackageSearch({ inventoryId }: { inventoryId: string }) {
    const { t } = useTranslation();
    const [name, setName] = useState("");
    const [found, setFound] = useState<{ packages: PackageHit[]; more: boolean } | null>(null);
    const [error, setError] = useState<string | null>(null);

    async function search() {
        setError(null);
        try {
            setFound(await call<{ packages: PackageHit[]; more: boolean }>("cmdb.packages", {
                name: name.trim(), ...(inventoryId ? { inventory_id: inventoryId } : {}),
            }));
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            <Flex gap={{ default: "gapSm" }} style={{ marginBlockEnd: "1rem" }}>
                <SearchInput aria-label={t("Package")} placeholder={t("Name of a package, or part of it")} value={name} style={{ minWidth: "20rem" }}
                    onChange={(_e, value) => setName(value)} onSearch={search} onClear={() => { setName(""); setFound(null); }} />
                <Button variant="secondary" onClick={search} isDisabled={!name.trim()}>{t("Find")}</Button>
            </Flex>
            {error && <Alert variant="danger" isInline title={error} />}
            {found && (found.packages.length === 0
                ? <Content component="p">{t("No host has a package by that name.")}</Content>
                : (
                    <>
                        {found.more && <Alert variant="info" isInline isPlain title={t("Only the first 5000 are shown: be more precise.")} />}
                        <Table variant="compact" aria-label={t("Packages found")}>
                            <Thead><Tr><Th>{t("Package")}</Th><Th>{t("Version")}</Th><Th>{t("Architecture")}</Th><Th>{t("Host")}</Th><Th>{t("Inventory")}</Th></Tr></Thead>
                            <Tbody>
                                {found.packages.map(item => (
                                    <Tr key={`${item.host_id} ${item.name} ${item.arch}`}>
                                        <Td dataLabel={t("Package")}>{item.name}</Td>
                                        <Td dataLabel={t("Version")}>{item.version}</Td>
                                        <Td dataLabel={t("Architecture")}>{item.arch}</Td>
                                        <Td dataLabel={t("Host")}>
                                            <Button variant="link" isInline onClick={() => cockpit.location.go(["hosts", item.host_id])}>{item.host}</Button>
                                        </Td>
                                        <Td dataLabel={t("Inventory")}>{item.inventory}</Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                    </>
                ))}
        </>
    );
}

export function AssetsPage() {
    const { t, i18n } = useTranslation();
    const [inventoryId, setInventoryId] = useState("");
    const [filter, setFilter] = useState("");
    const [tab, setTab] = useState<string | number>("hosts");
    const inventories = useLoad(() => call<{ inventories: Inventory[] }>("inventory.list"));
    const data = useLoad(() => call<{ hosts: CmdbHost[] }>("cmdb.list", inventoryId ? { inventory_id: inventoryId } : {}));
    useEffect(data.reload, [inventoryId]);

    const shown = (data.data?.hosts ?? []).filter(host => matches(host, filter));
    const stamp = new Date().toISOString().slice(0, 10);

    return (
        <>
            <SectionHeader title={t("Assets")}>
                {(inventories.data?.inventories.length ?? 0) > 1 && (
                    <FormSelect aria-label={t("Inventory")} value={inventoryId} style={{ minWidth: "12rem" }}
                        onChange={(_e, value) => setInventoryId(value)}>
                        <FormSelectOption value="" label={t("Every inventory")} />
                        {inventories.data!.inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                    </FormSelect>
                )}
                <Button variant="secondary" isDisabled={shown.length === 0}
                    onClick={() => download(toCsv(hostRows(shown)), `cockpit-fleet-assets-${stamp}.csv`, "text/csv")}>
                    {t("Export CSV")}
                </Button>
                <Button variant="secondary" isDisabled={shown.length === 0}
                    onClick={() => download(JSON.stringify(shown, null, 2) + "\n", `cockpit-fleet-assets-${stamp}.json`, "application/json")}>
                    {t("Export JSON")}
                </Button>
            </SectionHeader>
            <Tabs panelId="assets-view" activeKey={tab} onSelect={(_e, key) => setTab(key)} isSubtab style={{ marginBlockEnd: "1rem" }}>
                <Tab eventKey="hosts" title={<TabTitleText>{t("Hosts")}</TabTitleText>} />
                <Tab eventKey="packages" title={<TabTitleText>{t("Find a package")}</TabTitleText>} />
            </Tabs>
            <TabPanel panelId="assets-view" active={tab}>
                {tab === "packages" && <PackageSearch inventoryId={inventoryId} />}
                {tab === "hosts" && (
                    <ListState loaded={data} isEmpty={loaded => loaded.hosts.length === 0}
                        emptyTitle={t("No hosts yet")}
                        emptyBody={t("The hosts of an inventory are listed here once it has been read. What they are made of comes from a check of the kind Hardware and software, their updates from one of the kind Updates.")}>
                        {loaded => (
                            <>
                                <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }} style={{ marginBlockEnd: "1rem" }}>
                                    <SearchInput aria-label={t("Filter")} placeholder={t("Name, system, model, address…")} value={filter}
                                        style={{ minWidth: "20rem" }} onChange={(_e, value) => setFilter(value)} onClear={() => setFilter("")} />
                                    {bySystem(loaded.hosts).map(([system, count]) => (
                                        <Label key={system} isCompact color={system ? "blue" : "grey"} onClick={() => setFilter(system)}>
                                            {`${system || t("Not known yet")}: ${count}`}
                                        </Label>
                                    ))}
                                </Flex>
                                {!loaded.hosts.some(host => host.hardware.collected_at) && (
                                    <Alert variant="info" isInline isPlain style={{ marginBlockEnd: "1rem" }}
                                        title={t("Nothing collected yet: apply a check of the kind Hardware and software to the hosts, in Checks.")} />
                                )}
                                <Table variant="compact" aria-label={t("Assets")}>
                                    <Thead>
                                        <Tr>
                                            {[t("Host"), t("System"), t("Hardware"), t("CPUs"), t("Memory"), t("Packages"), t("Updates"), t("Compliance"), t("Collected")]
                                                .map(name => <Th key={name} modifier="wrap">{name}</Th>)}
                                        </Tr>
                                    </Thead>
                                    <Tbody>
                                        {shown.map(host => (
                                            <Tr key={host.id}>
                                                <Td dataLabel={t("Host")}>
                                                    <Button variant="link" isInline onClick={() => cockpit.location.go(["hosts", host.id])}>{host.name}</Button>
                                                    {(inventories.data?.inventories.length ?? 0) > 1 && <Content component="small"> ({host.inventory})</Content>}
                                                </Td>
                                                <Td dataLabel={t("System")}>{host.os || <Content component="small">{t("Not known yet")}</Content>}</Td>
                                                <Td dataLabel={t("Hardware")}><Hardware host={host} /></Td>
                                                <Td dataLabel={t("CPUs")}>{host.hardware.cpus ?? ""}</Td>
                                                <Td dataLabel={t("Memory")}>{host.hardware.memory ? formatBytes(host.hardware.memory) : ""}</Td>
                                                <Td dataLabel={t("Packages")}>{host.hardware.collected_at ? host.packages : ""}</Td>
                                                <Td dataLabel={t("Updates")}><Updates host={host} /></Td>
                                                <Td dataLabel={t("Compliance")}><Compliance compliant={host.compliant} /></Td>
                                                <Td dataLabel={t("Collected")}>{formatTime(host.hardware.collected_at, i18n.language)}</Td>
                                            </Tr>
                                        ))}
                                    </Tbody>
                                </Table>
                            </>
                        )}
                    </ListState>
                )}
            </TabPanel>
        </>
    );
}

// --- one host -------------------------------------------------------------------

function Filtered<T>({ items, label, keyOf, text, columns, row }: {
    items: T[];
    label: string;
    keyOf: (item: T) => string;
    text: (item: T) => string;
    columns: string[];
    row: (item: T) => React.ReactNode;
}) {
    const { t } = useTranslation();
    const [filter, setFilter] = useState("");
    const needle = filter.trim().toLowerCase();
    const found = needle ? items.filter(item => text(item).toLowerCase().includes(needle)) : items;
    return (
        <>
            <SearchInput aria-label={t("Filter")} placeholder={t("Filter")} value={filter} style={{ maxWidth: "20rem", marginBlockEnd: "0.5rem" }}
                onChange={(_e, value) => setFilter(value)} onClear={() => setFilter("")} />
            <Table variant="compact" aria-label={label}>
                <Thead><Tr>{columns.map(column => <Th key={column}>{column}</Th>)}</Tr></Thead>
                <Tbody>{found.slice(0, PAGE).map(item => <Tr key={keyOf(item)}>{row(item)}</Tr>)}</Tbody>
            </Table>
            {found.length > PAGE && (
                <Content component="small">{t("{{shown}} of {{total}} shown: filter to find the others.", { shown: PAGE, total: found.length })}</Content>
            )}
        </>
    );
}

function HardwareDetail({ host }: { host: CmdbDetail }) {
    const { t, i18n } = useTranslation();
    const hardware = host.hardware;
    const rows: [string, React.ReactNode][] = [
        [t("Vendor and model"), <Hardware key="hw" host={host} />],
        [t("Serial number"), hardware.serial || t("Not read (it needs become)")],
        [t("BIOS"), hardware.bios],
        [t("Processor"), `${hardware.cpu_model ?? ""}${hardware.cpus ? ` × ${hardware.cpus}` : ""}`],
        [t("Memory"), hardware.memory ? formatBytes(hardware.memory) : ""],
        [t("Booted"), formatTime(hardware.boot_at, i18n.language)],
        [t("Disks"), (hardware.disks ?? []).map(disk => `${disk.name} ${formatBytes(disk.size)}${disk.model ? ` (${disk.model})` : ""}`).join(", ")],
        [t("Packages from"), hardware.package_manager],
        [t("Collected"), formatTime(hardware.collected_at, i18n.language)],
    ];
    return (
        <>
            <DescriptionList isHorizontal isCompact columnModifier={{ default: "2Col" }}>
                {rows.map(([term, value]) => (
                    <DescriptionListGroup key={term}>
                        <DescriptionListTerm>{term}</DescriptionListTerm>
                        <DescriptionListDescription>{value || "—"}</DescriptionListDescription>
                    </DescriptionListGroup>
                ))}
            </DescriptionList>
            {(hardware.interfaces ?? []).length > 0 && (
                <Table variant="compact" aria-label={t("Network interfaces")} style={{ marginBlockStart: "1rem" }}>
                    <Thead><Tr><Th>{t("Interface")}</Th><Th>{t("MAC address")}</Th><Th>{t("Addresses")}</Th></Tr></Thead>
                    <Tbody>
                        {hardware.interfaces!.map(nic => (
                            <Tr key={nic.name}>
                                <Td dataLabel={t("Interface")}>{nic.name}</Td>
                                <Td dataLabel={t("MAC address")}><code>{nic.mac}</code></Td>
                                <Td dataLabel={t("Addresses")}>{nic.addresses.join(", ")}</Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
        </>
    );
}

function ChangeList({ changes }: { changes: CmdbChange[] }) {
    const { t, i18n } = useTranslation();
    const areas: Record<CmdbChange["area"], string> = {
        hardware: t("Hardware"), network: t("Network"), package: t("Package"), service: t("Service"),
    };
    if (changes.length === 0) return <Content component="p">{t("No change since the first collection.")}</Content>;
    return (
        <Filtered items={changes} label={t("Changes")} keyOf={change => `${change.ts} ${change.area} ${change.name}`}
            text={change => `${change.area} ${change.name} ${change.before} ${change.after}`}
            columns={[t("When"), t("What"), t("Name"), t("Before"), t("After")]}
            row={change => (
                <>
                    <Td dataLabel={t("When")}>{formatTime(change.ts, i18n.language)}</Td>
                    <Td dataLabel={t("What")}>{areas[change.area] ?? change.area}</Td>
                    <Td dataLabel={t("Name")}>{change.name}</Td>
                    <Td dataLabel={t("Before")}>{change.before || "—"}</Td>
                    <Td dataLabel={t("After")}>{change.after || "—"}</Td>
                </>
            )} />
    );
}

// A template of the host's inventory that applies its updates, launched on it alone.
function ApplyUpdates({ host }: { host: CmdbDetail }) {
    const { t } = useTranslation();
    const templates = useLoad(() => call<{ templates: Template[] }>("template.list"));
    const fitting = (templates.data?.templates ?? []).filter(item => item.inventory_id === host.inventory_id);
    const [chosen, setChosen] = useState("");
    const [launching, setLaunching] = useState(false);
    const guess = fitting.find(item => /updat|upgrad|patch|aggiorn/i.test(item.name)) ?? fitting[0];
    const template = fitting.find(item => item.id === chosen) ?? guess;
    if (!templates.data) return null;
    if (!template) {
        return <Content component="small">{t("To apply them from here, make a job template on the inventory {{inventory}}.", { inventory: host.inventory })}</Content>;
    }
    return (
        <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }} style={{ marginBlockEnd: "1rem" }}>
            <FormSelect aria-label={t("Job template")} value={template.id} style={{ maxWidth: "20rem" }} onChange={(_e, value) => setChosen(value)}>
                {fitting.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
            </FormSelect>
            <Button variant="secondary" onClick={() => setLaunching(true)}>{t("Apply on this host…")}</Button>
            {launching && <LaunchModal template={template} limit={host.name} onClose={() => setLaunching(false)} />}
        </Flex>
    );
}

function DiffText({ text }: { text: string }) {
    const color = (line: string) => line.startsWith("+") && !line.startsWith("+++")
        ? "var(--pf-t--global--color--status--success--default)"
        : line.startsWith("-") && !line.startsWith("---") ? "var(--pf-t--global--color--status--danger--default)" : undefined;
    return (
        <pre style={{ fontSize: "var(--pf-t--global--font--size--sm)", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {text.split("\n").map((line, index) => <div key={index} style={{ color: color(line) }}>{line || " "}</div>)}
        </pre>
    );
}

function DriftList({ drift }: { drift: Drift[] }) {
    const { t, i18n } = useTranslation();
    if (drift.length === 0) return <Content component="p">{t("No compliance check has run on this host yet.")}</Content>;
    return (
        <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
            {drift.map(item => (
                <div key={item.check_id}>
                    <Title headingLevel="h4" size="md">
                        {item.check} <Content component="small">{formatTime(item.ts, i18n.language)}</Content>
                    </Title>
                    {item.changes.length === 0
                        ? <Content component="p">{t("Nothing would change.")}</Content>
                        : item.changes.map((change, index) => (
                            <div key={index} style={{ marginBlockStart: "0.5rem" }}>
                                <strong>{change.task || change.note || t("A task")}</strong>
                                {change.diff ? <DiffText text={change.diff} /> : !change.note && <Content component="small"> · {t("It gives no difference.")}</Content>}
                            </div>
                        ))}
                </div>
            ))}
        </Flex>
    );
}

// What a host is made of, its updates and its drift, as a part of its page.
export function HostInventory({ hostId }: { hostId: string }) {
    const { t } = useTranslation();
    const can = useCan();
    const [chosen, setTab] = useState<string | number>("hardware");
    const detail = useLoad(() => call<CmdbDetail>("cmdb.get", { host_id: hostId }));
    const drift = useLoad(() => can("drift.get")
        ? call<{ drift: Drift[] }>("drift.get", { host_id: hostId })
        : Promise.resolve({ drift: [] as Drift[] }));

    if (detail.error) return <Alert variant="danger" isInline title={detail.error.message} />;
    if (!detail.data) return null;
    const host = detail.data;
    const driftItems = drift.data?.drift ?? [];
    if (!host.hardware.collected_at && !host.hardware.updates_at && driftItems.length === 0) {
        return <Content component="p">{t("Nothing collected yet: apply a check of the kind Hardware and software to this host, in Checks.")}</Content>;
    }
    const collected = !!host.hardware.collected_at;
    const available = [
        ...(collected ? ["hardware", "packages", "services"] : []), ...(host.hardware.updates_at ? ["updates"] : []),
        ...(collected ? ["changes"] : []), ...(can("drift.get") ? ["drift"] : []),
    ];
    const tab = available.includes(String(chosen)) ? chosen : available[0];
    return (
        <>
            <Tabs panelId="host-assets" activeKey={tab} onSelect={(_e, key) => setTab(key)} isSubtab style={{ marginBlockEnd: "1rem" }}>
                {collected && <Tab eventKey="hardware" title={<TabTitleText>{t("Hardware")}</TabTitleText>} />}
                {collected && <Tab eventKey="packages" title={<TabTitleText>{t("Packages ({{n}})", { n: host.packages })}</TabTitleText>} />}
                {collected && <Tab eventKey="services" title={<TabTitleText>{t("Services ({{n}})", { n: host.services })}</TabTitleText>} />}
                {!!host.hardware.updates_at && <Tab eventKey="updates" title={<TabTitleText>{t("Updates ({{n}})", { n: host.updates })}</TabTitleText>} />}
                {collected && <Tab eventKey="changes" title={<TabTitleText>{t("Changes")}</TabTitleText>} />}
                {can("drift.get") && <Tab eventKey="drift" title={<TabTitleText>{t("Drift")}</TabTitleText>} />}
            </Tabs>
            <TabPanel panelId="host-assets" active={tab}>
                {tab === "hardware" && <HardwareDetail host={host} />}
                {tab === "packages" && (
                    <Filtered items={host.package_list} label={t("Packages")} keyOf={item => `${item.name} ${item.arch}`} text={item => item.name}
                        columns={[t("Package"), t("Version"), t("Architecture")]}
                        row={item => <><Td dataLabel={t("Package")}>{item.name}</Td><Td dataLabel={t("Version")}>{item.version}</Td><Td dataLabel={t("Architecture")}>{item.arch}</Td></>} />
                )}
                {tab === "services" && (
                    <Filtered items={host.service_list} label={t("Services")} keyOf={item => item.name} text={item => `${item.name} ${item.state} ${item.enabled}`}
                        columns={[t("Service"), t("State"), t("At boot")]}
                        row={item => <><Td dataLabel={t("Service")}>{item.name}</Td><Td dataLabel={t("State")}>{item.state}</Td><Td dataLabel={t("At boot")}>{item.enabled}</Td></>} />
                )}
                {tab === "updates" && (host.update_list.length === 0
                    ? <Content component="p">{t("Up to date.")}</Content>
                    : (
                        <>
                            {can("run.start") && <ApplyUpdates host={host} />}
                            <Filtered items={host.update_list} label={t("Updates")} keyOf={item => item.name} text={item => `${item.name} ${item.source}`}
                                columns={[t("Update"), t("Installed"), t("Available"), t("From")]}
                                row={item => (
                                    <>
                                        <Td dataLabel={t("Update")}>
                                            {item.name}
                                            {item.security && <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }}>{t("Security")}</Label>}
                                        </Td>
                                        <Td dataLabel={t("Installed")}>{item.current}</Td>
                                        <Td dataLabel={t("Available")}>{item.available}</Td>
                                        <Td dataLabel={t("From")}>
                                            <Tooltip content={item.source}><span>{item.source.length > 40 ? item.source.slice(0, 40) + "…" : item.source}</span></Tooltip>
                                        </Td>
                                    </>
                                )} />
                        </>
                    ))}
                {tab === "changes" && <ChangeList changes={host.changes} />}
                {tab === "drift" && (drift.error ? <Alert variant="danger" isInline title={drift.error.message} /> : <DriftList drift={driftItems} />)}
            </TabPanel>
        </>
    );
}
