import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    Content, Flex, FlexItem, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, Label, LabelGroup, TextInput, Tooltip,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { filterScanHosts, subnetSize } from "../lib/netscan.ts";
import { useCan } from "../lib/session.tsx";
import type { Run, ScanHost, ScanResult, ScanSubnets } from "../lib/types.ts";
import { FormModal, ListState, PageNav, RunStatusLabel, SectionHeader, isActiveRun, openRun } from "./common.tsx";
import { Alert, Button, SearchInput } from "./pf.tsx";

function openScan(id: string) {
    cockpit.location.go(["network", id]);
}

// A scan asks for a subnet among those the controller may scan, or a part
// of one: the list fills the field, which may then be narrowed.
function ScanModal({ onClose }: { onClose: () => void }) {
    const { t } = useTranslation();
    const allowed = useLoad(() => call<ScanSubnets>("netscan.subnets"));
    const [subnet, setSubnet] = useState("");
    const subnets = allowed.data?.subnets ?? [];
    const size = subnetSize(subnet);
    const tooLarge = size !== null && allowed.data !== null && size > allowed.data.max_addresses;

    async function start() {
        const run = await call<Run>("netscan.start", { subnet: subnet.trim() });
        onClose();
        openScan(run.id);
    }

    return (
        <FormModal title={t("Scan a subnet")} submitLabel={t("Scan")} onSubmit={start} onClose={onClose}
            isDisabled={!subnet.trim() || tooLarge || subnets.length === 0}>
            <Content component="p">
                {t("Each address is tried with an ICMP echo and a TCP connection to a few ports; each host that answers is named from the DNS and NetBIOS, with its MAC address when it is on the controller's own segment. Nothing is changed on the hosts.")}
            </Content>
            {allowed.error && <Alert variant="danger" isInline title={allowed.error.message} />}
            {allowed.data && subnets.length === 0 && (
                <Alert variant="warning" isInline title={t("No subnet may be scanned")}>
                    {t("The controller is attached to none, or config.toml turns them off: list the subnets that may be scanned in [network] scan_subnets.")}
                </Alert>
            )}
            {subnets.length > 0 && (
                <FormGroup label={t("Subnets that may be scanned")} fieldId="ns-allowed">
                    <FormSelect id="ns-allowed" value={subnets.some(item => item.subnet === subnet) ? subnet : ""}
                        onChange={(_e, value) => setSubnet(value)}>
                        <FormSelectOption value="" label={t("Choose a subnet")} isPlaceholder />
                        {subnets.map(item => (
                            <FormSelectOption key={item.subnet} value={item.subnet}
                                label={item.source === "connected"
                                    ? t("{{subnet}} (attached, {{interface}})", { subnet: item.subnet, interface: item.interface })
                                    : t("{{subnet}} (config.toml)", { subnet: item.subnet })} />
                        ))}
                    </FormSelect>
                </FormGroup>
            )}
            {allowed.data && subnets.length > 0 && (
                <FormGroup label={t("Subnet")} isRequired fieldId="ns-subnet">
                    <TextInput id="ns-subnet" value={subnet} placeholder="192.168.1.0/24" onChange={(_e, value) => setSubnet(value)} />
                    <FormHelperText>
                        <HelperText>
                            <HelperTextItem variant={tooLarge ? "error" : "default"}>
                                {tooLarge
                                    ? t("{{size}} addresses, more than {{max}}: scan a part of it, such as a /24.", { size, max: allowed.data.max_addresses })
                                    : t("One of those above or a part of one, of at most {{max}} addresses. Ports tried: {{ports}}.", { max: allowed.data.max_addresses, ports: allowed.data.ports.join(", ") })}
                            </HelperTextItem>
                        </HelperText>
                    </FormHelperText>
                </FormGroup>
            )}
        </FormModal>
    );
}

function ScanList() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const scans = useLoad(() => call<{ runs: Run[] }>("run.list", { kind: "netscan", limit: 100 }));
    const [scanning, setScanning] = useState(false);
    const active = (scans.data?.runs ?? []).some(run => isActiveRun(run.status));
    usePolling(() => scans.reload(), 2000, active);

    return (
        <>
            <SectionHeader title={t("Network scans")}>
                {can("netscan.start") && <Button variant="primary" onClick={() => setScanning(true)}>{t("Scan a subnet")}</Button>}
            </SectionHeader>
            {scanning && <ScanModal onClose={() => { setScanning(false); scans.reload(); }} />}
            <ListState loaded={scans} isEmpty={data => data.runs.length === 0}
                emptyTitle={t("No scans yet")}
                emptyBody={t("A scan finds the hosts that answer on a subnet the controller may reach, with their names, open ports and MAC addresses.")}>
                {data => (
                    <Table aria-label={t("Network scans")} variant="compact">
                        <Thead>
                            <Tr>
                                <Th>{t("Subnet")}</Th>
                                <Th>{t("Status")}</Th>
                                <Th>{t("Hosts found")}</Th>
                                <Th>{t("Launched by")}</Th>
                                <Th>{t("Started")}</Th>
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.runs.map(run => (
                                <Tr key={run.id}>
                                    <Td dataLabel={t("Subnet")}>
                                        <Button variant="link" isInline onClick={() => openScan(run.id)}>{run.options.subnet ?? run.title}</Button>
                                    </Td>
                                    <Td dataLabel={t("Status")}><RunStatusLabel status={run.status} /></Td>
                                    <Td dataLabel={t("Hosts found")}>{run.options.hosts ?? "—"}</Td>
                                    <Td dataLabel={t("Launched by")}>{run.created_by}</Td>
                                    <Td dataLabel={t("Started")}>{formatTime(run.started_at ?? run.created_at, i18n.language)}</Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
        </>
    );
}

function Names({ host }: { host: ScanHost }) {
    const { t } = useTranslation();
    const netbios = host.netbios_name && (host.workgroup ? `${host.netbios_name} (${host.workgroup})` : host.netbios_name);
    if (!host.dns_name && !netbios) return <>—</>;
    return (
        <Flex direction={{ default: "column" }} gap={{ default: "gapNone" }}>
            {host.dns_name && <FlexItem>{host.dns_name}</FlexItem>}
            {netbios && <FlexItem><Tooltip content={t("NetBIOS name")}><span>{netbios}</span></Tooltip></FlexItem>}
        </Flex>
    );
}

function Known({ host }: { host: ScanHost }) {
    const { t } = useTranslation();
    if (host.known.length === 0 && !host.self) return <>—</>;
    return (
        <LabelGroup numLabels={4}>
            {host.self && <Label isCompact color="purple">{t("This controller")}</Label>}
            {host.known.map(known => (
                <Label key={`${known.inventory_id}/${known.host}`} isCompact color={known.by === "address" ? "blue" : "grey"}>
                    {known.by === "address"
                        ? t("{{host}} in {{inventory}}", { host: known.host, inventory: known.inventory })
                        : t("{{host}} in {{inventory}}, by name", { host: known.host, inventory: known.inventory })}
                </Label>
            ))}
        </LabelGroup>
    );
}

function ScanResults({ id }: { id: string }) {
    const { t, i18n } = useTranslation();
    const result = useLoad(() => call<ScanResult>("netscan.result", { id }));
    const [search, setSearch] = useState("");
    usePolling(() => result.reload(), 2000, !result.data?.done);
    const seen: Record<string, string> = { tcp: t("TCP"), icmp: t("ICMP"), arp: t("ARP") };
    const seenHelp: Record<string, string> = {
        tcp: t("A port open, or a connection refused"),
        icmp: t("An answer to an ICMP echo"),
        arp: t("In the controller's neighbour table"),
    };

    const nav = (
        <PageNav links={[
            { label: t("All scans"), onClick: () => cockpit.location.go(["network"]) },
            { label: t("Open the run"), onClick: () => openRun(id) },
        ]} />
    );
    if (result.error) return <>{nav}<Alert variant="danger" isInline title={result.error.message} /></>;
    if (!result.data) return nav;
    const data = result.data;
    const options = data.run.options;
    const hosts = filterScanHosts(data.hosts, search);

    return (
        <>
            {nav}
            <SectionHeader title={t("Scan of {{subnet}}", { subnet: data.subnet })}>
                <RunStatusLabel status={data.run.status} />
            </SectionHeader>
            <Content component="p">
                {t("Launched by {{user}}, {{time}}.", { user: data.run.created_by, time: formatTime(data.run.started_at ?? data.run.created_at, i18n.language) })}
                {" "}
                {data.done ? t("hosts_found_count", { count: data.hosts.length }) : t("Scanning: the hosts appear when the scan ends.")}
            </Content>
            {data.run.error && <Alert variant="danger" isInline title={data.run.error} style={{ marginBottom: "1rem" }} />}
            {data.done && options.complete === false && (
                <Alert variant="warning" isInline title={t("The scan was stopped before the end: these are the hosts it had found.")} style={{ marginBottom: "1rem" }} />
            )}
            {data.done && options.icmp === false && (
                <Alert variant="info" isInline title={t("ICMP echo was not allowed to the runs (net.ipv4.ping_group_range): hosts were found by TCP and the neighbour table only.")} style={{ marginBottom: "1rem" }} />
            )}
            {!data.on_link && (
                <Alert variant="info" isInline title={t("This subnet is beyond a router: MAC addresses come only from NetBIOS, from the hosts that give it.")} style={{ marginBottom: "1rem" }} />
            )}
            {data.done && data.hosts.length > 0 && (
                <>
                    <SearchInput placeholder={t("Filter by address, name, MAC address, port or inventory")} value={search}
                        onChange={(_e, value) => setSearch(value)} onClear={() => setSearch("")} style={{ maxWidth: "30rem", marginBottom: "0.5rem" }} />
                    <Table aria-label={t("Hosts found")} variant="compact">
                        <Thead>
                            <Tr>
                                <Th>{t("Address")}</Th>
                                <Th>{t("Names")}</Th>
                                <Th>{t("MAC address")}</Th>
                                <Th>{t("Open ports")}</Th>
                                <Th>{t("Seen by")}</Th>
                                <Th>{t("In inventories")}</Th>
                            </Tr>
                        </Thead>
                        <Tbody>
                            {hosts.map(host => (
                                <Tr key={host.address}>
                                    <Td dataLabel={t("Address")}><code>{host.address}</code></Td>
                                    <Td dataLabel={t("Names")}><Names host={host} /></Td>
                                    <Td dataLabel={t("MAC address")}>
                                        {host.mac ? <code>{host.mac}</code> : "—"}
                                        {host.mac_source === "netbios" && (
                                            <Tooltip content={t("Given by the host over NetBIOS, not seen on the segment")}>
                                                <Label isCompact color="grey" style={{ marginInlineStart: "0.5rem" }}>{t("NetBIOS")}</Label>
                                            </Tooltip>
                                        )}
                                    </Td>
                                    <Td dataLabel={t("Open ports")}>{host.ports.length > 0 ? host.ports.join(", ") : "—"}</Td>
                                    <Td dataLabel={t("Seen by")}>
                                        <LabelGroup>
                                            {host.seen.map(how => (
                                                <Tooltip key={how} content={seenHelp[how]}><Label isCompact>{seen[how]}</Label></Tooltip>
                                            ))}
                                        </LabelGroup>
                                    </Td>
                                    <Td dataLabel={t("In inventories")}><Known host={host} /></Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                </>
            )}
        </>
    );
}

export function NetworkPage({ id }: { id?: string }) {
    return id ? <ScanResults key={id} id={id} /> : <ScanList />;
}
