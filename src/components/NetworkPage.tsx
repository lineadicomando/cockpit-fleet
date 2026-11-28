import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    Checkbox, Content, Flex, ModalBody, ModalHeader, ModalVariant, FlexItem, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, Label, LabelGroup, Radio, TextInput, Tooltip,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { filterScanHosts, inventoriesFound, inventoryHosts, subnetSize } from "../lib/netscan.ts";
import { useCan } from "../lib/session.tsx";
import type { Inventory, MacOutcome, MacsAnswer, ProbeAnswer, ProbedHost, ProbeFinding, Run, ScanHost, ScanResult, ScanSubnets } from "../lib/types.ts";
import { FormModal, ListState, PageNav, RunStatusLabel, SectionHeader, isActiveRun, openRun } from "./common.tsx";
import { Alert, Button, Modal, SearchInput, Spinner } from "./pf.tsx";

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

// A new inventory written here, of the hosts chosen: named as asked, with
// their addresses and the MAC addresses the scan saw.
function InventoryModal({ id, hosts, onClose }: { id: string; hosts: ScanHost[]; onClose: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState("");
    const [naming, setNaming] = useState<"short" | "dns" | "address">("short");
    const [group, setGroup] = useState("");

    async function create() {
        const made = await call<{ inventory: Inventory }>("netscan.inventory", {
            id, name: name.trim(), naming, group: group.trim(), addresses: hosts.map(host => host.address),
        });
        onClose();
        cockpit.location.go(["inventories", made.inventory.id]);
    }

    return (
        <FormModal title={t("Create an inventory")} submitLabel={t("Create")} onSubmit={create} onClose={onClose} isDisabled={!name.trim()}>
            <Content component="p">
                {t("new_inventory_hosts_count", { count: hosts.length })}
            </Content>
            <FormGroup label={t("Name")} isRequired fieldId="ns-inv-name">
                <TextInput id="ns-inv-name" value={name} onChange={(_e, value) => setName(value)} />
            </FormGroup>
            <FormGroup role="radiogroup" label={t("Names of the hosts")} fieldId="ns-inv-naming">
                <Radio id="ns-inv-short" name="ns-inv-naming" isChecked={naming === "short"} onChange={() => setNaming("short")}
                    label={t("Short: the NetBIOS name, or the first part of the DNS name")} />
                <Radio id="ns-inv-dns" name="ns-inv-naming" isChecked={naming === "dns"} onChange={() => setNaming("dns")}
                    label={t("The whole DNS name")} />
                <Radio id="ns-inv-address" name="ns-inv-naming" isChecked={naming === "address"} onChange={() => setNaming("address")}
                    label={t("The address")} />
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem>{t("A host with no such name, or whose name another host took, is named by its address.")}</HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
            <FormGroup label={t("Group")} fieldId="ns-inv-group">
                <TextInput id="ns-inv-group" value={group} onChange={(_e, value) => setGroup(value)} />
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem>{t("Optional: a group to put every host in.")}</HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

function OutcomeLabel({ outcome }: { outcome: MacOutcome }) {
    const { t } = useTranslation();
    if (outcome.result === "written") return <Label isCompact color="blue">{outcome.moved ? t("Written, its file moved") : t("Written")}</Label>;
    if (outcome.result === "unchanged") return <Label isCompact color="green">{t("Already there")}</Label>;
    return <Tooltip content={outcome.reason ?? ""}><Label isCompact color="orange">{t("Left alone")}</Label></Tooltip>;
}

// The MAC addresses the scan saw, written as mac_address into an inventory
// written here, for its hosts that the scan found: what would be done is
// shown before anything is written.
function MacsModal({ id, hosts, onClose, onDone }: {
    id: string; hosts: ScanHost[]; onClose: () => void; onDone: (text: string) => void;
}) {
    const { t } = useTranslation();
    const inventories = useLoad(() => call<{ inventories: Inventory[] }>("inventory.list"));
    const found = inventoriesFound(hosts);
    const managed = (inventories.data?.inventories ?? []).filter(inventory => inventory.kind === "managed" && found.has(inventory.id));
    const [inventoryId, setInventoryId] = useState("");
    const [byName, setByName] = useState(false);
    const [preview, setPreview] = useState<MacsAnswer | null>(null);
    const [error, setError] = useState<string | null>(null);
    const names = inventoryId ? inventoryHosts(hosts, inventoryId, byName) : [];

    useEffect(() => {
        setPreview(null);
        setError(null);
        if (!inventoryId || names.length === 0) return;
        let current = true;
        call<MacsAnswer>("netscan.macs", { id, inventory_id: inventoryId, by_name: byName, hosts: names, preview: true })
            .then(answer => { if (current) setPreview(answer); }, ex => { if (current) setError(ex instanceof Error ? ex.message : String(ex)); });
        return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [inventoryId, byName, names.join(" ")]);

    const writing = (preview?.hosts ?? []).filter(outcome => outcome.result === "written").length;

    async function write() {
        const answer = await call<MacsAnswer>("netscan.macs", { id, inventory_id: inventoryId, by_name: byName, hosts: names });
        const inventory = managed.find(item => item.id === inventoryId)?.name ?? "";
        onDone(t("mac_written_count", { count: answer.hosts.filter(outcome => outcome.result === "written").length, inventory }));
        onClose();
    }

    return (
        <FormModal title={t("Write the MAC addresses into an inventory")} submitLabel={t("Write")} onSubmit={write} onClose={onClose}
            isDisabled={writing === 0}>
            <Content component="p">
                {t("Each host of the inventory that the scan found gets mac_address in host_vars/HOST/fleet_mac.yaml, as one change of the inventory. A file of its variables already in host_vars is moved into that directory, unchanged: Ansible would no longer read it. A host whose variables already set mac_address is left alone.")}
            </Content>
            {inventories.error && <Alert variant="danger" isInline title={inventories.error.message} />}
            {inventories.data && managed.length === 0 && (
                <Alert variant="info" isInline title={t("None of these hosts is in an inventory written in Fleet.")} />
            )}
            {managed.length > 0 && (
                <FormGroup label={t("Inventory")} isRequired fieldId="ns-macs-inventory">
                    <FormSelect id="ns-macs-inventory" value={inventoryId} onChange={(_e, value) => setInventoryId(value)}>
                        <FormSelectOption value="" label={t("Choose an inventory")} isPlaceholder />
                        {managed.map(inventory => (
                            <FormSelectOption key={inventory.id} value={inventory.id}
                                label={t("inventory_found_count", { name: inventory.name, count: found.get(inventory.id) ?? 0 })} />
                        ))}
                    </FormSelect>
                </FormGroup>
            )}
            {inventoryId && (
                <Checkbox id="ns-macs-by-name" isChecked={byName} onChange={(_e, value) => setByName(value)}
                    label={t("Also the hosts found by name only, not by address")} />
            )}
            {inventoryId && names.length === 0 && (
                <Content component="p">{t("No host of this inventory was found with a MAC address.")}</Content>
            )}
            {error && <Alert variant="danger" isInline title={error} />}
            {preview && preview.hosts.length > 0 && (
                <Table aria-label={t("What would be written")} variant="compact">
                    <Thead>
                        <Tr>
                            <Th>{t("Host")}</Th>
                            <Th>{t("MAC address")}</Th>
                            <Th>{t("Result")}</Th>
                        </Tr>
                    </Thead>
                    <Tbody>
                        {preview.hosts.map(outcome => (
                            <Tr key={outcome.host}>
                                <Td dataLabel={t("Host")}>{outcome.host}</Td>
                                <Td dataLabel={t("MAC address")}>{outcome.mac ? <code>{outcome.mac}</code> : "—"}</Td>
                                <Td dataLabel={t("Result")}>
                                    <OutcomeLabel outcome={outcome} />
                                    {outcome.moved && <div><small>{t("{{from}} moved to {{to}}", outcome.moved)}</small></div>}
                                    {outcome.reason && <div><small>{outcome.reason}</small></div>}
                                </Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
        </FormModal>
    );
}

function ScanResults({ id }: { id: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const result = useLoad(() => call<ScanResult>("netscan.result", { id }));
    const [search, setSearch] = useState("");
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [dialog, setDialog] = useState<"" | "inventory" | "macs">("");
    const [done, setDone] = useState("");
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
    const chosen = data.hosts.filter(host => selected.has(host.address));
    // What the actions take: the hosts chosen, or, when none is, every one for
    // the MAC addresses, and those new to the fleet for an inventory (not this
    // controller, nor a host some inventory has already).
    const taken = chosen.length > 0 ? chosen : data.hosts;
    const forInventory = chosen.length > 0 ? chosen : data.hosts.filter(host => !host.self && host.known.length === 0);
    const allShown = hosts.length > 0 && hosts.every(host => selected.has(host.address));
    const select = (addresses: string[], on: boolean) => setSelected(current => {
        const next = new Set(current);
        for (const address of addresses) {
            if (on) next.add(address); else next.delete(address);
        }
        return next;
    });

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
            {done && <Alert variant="success" isInline title={done} style={{ marginBottom: "1rem" }} />}
            {dialog === "inventory" && <InventoryModal id={id} hosts={forInventory} onClose={() => setDialog("")} />}
            {dialog === "macs" && <MacsModal id={id} hosts={taken} onClose={() => setDialog("")} onDone={setDone} />}
            {data.done && data.hosts.length > 0 && (
                <>
                    <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }} style={{ marginBottom: "0.5rem" }}>
                        <FlexItem grow={{ default: "grow" }}>
                            <SearchInput placeholder={t("Filter by address, name, MAC address, port or inventory")} value={search}
                                onChange={(_e, value) => setSearch(value)} onClear={() => setSearch("")} style={{ maxWidth: "30rem" }} />
                        </FlexItem>
                        <FlexItem>
                            {chosen.length > 0 ? t("chosen_hosts_count", { count: chosen.length }) : t("No host chosen: an inventory takes those in none yet, the MAC addresses all of them.")}
                        </FlexItem>
                        {can("netscan.inventory") && (
                            <Button variant="secondary" isDisabled={forInventory.length === 0} onClick={() => { setDone(""); setDialog("inventory"); }}>
                                {t("create_inventory_hosts_count", { count: forInventory.length })}
                            </Button>
                        )}
                        {can("netscan.macs") && (
                            <Button variant="secondary" onClick={() => { setDone(""); setDialog("macs"); }}>{t("Write the MAC addresses into an inventory")}</Button>
                        )}
                    </Flex>
                    <Table aria-label={t("Hosts found")} variant="compact">
                        <Thead>
                            <Tr>
                                <Th select={{ onSelect: (_e, on) => select(hosts.map(host => host.address), on), isSelected: allShown }} aria-label={t("Choose the hosts shown")} />
                                <Th>{t("Address")}</Th>
                                <Th>{t("Names")}</Th>
                                <Th>{t("MAC address")}</Th>
                                <Th>{t("Open ports")}</Th>
                                <Th>{t("Seen by")}</Th>
                                <Th>{t("In inventories")}</Th>
                            </Tr>
                        </Thead>
                        <Tbody>
                            {hosts.map((host, index) => (
                                <Tr key={host.address}>
                                    <Td select={{ rowIndex: index, onSelect: (_e, on) => select([host.address], on), isSelected: selected.has(host.address) }} />
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

function useFindings() {
    const { t } = useTranslation();
    const text: Record<ProbeFinding, string> = {
        unresolved: t("Its name has no address"),
        ipv6: t("IPv6 only, not probed"),
        silent: t("Nothing answers"),
        segment_only: t("On the segment, answers nothing"),
        port_closed: t("Its connection's port is closed"),
        mac_differs: t("Another MAC address than the CMDB's"),
        address_moved: t("The CMDB saw it at another address"),
        netbios_differs: t("Gives another name"),
    };
    return (finding: ProbeFinding) => text[finding] ?? finding;
}

const SERIOUS: ProbeFinding[] = ["unresolved", "silent", "mac_differs"];

function Probed({ host }: { host: ProbedHost }) {
    const { t } = useTranslation();
    const finding = useFindings();
    if (host.findings.length === 0) return <Label isCompact color="green">{t("Answers")}</Label>;
    return (
        <LabelGroup numLabels={4}>
            {host.findings.map(item => (
                <Label key={item} isCompact color={SERIOUS.includes(item) ? "red" : "orange"}>{finding(item)}</Label>
            ))}
        </LabelGroup>
    );
}

// The hosts of an inventory probed from the controller, one by one: what
// each says, to tell a host that is off from one that moved or is closed.
export function ProbeModal({ inventory, onClose }: { inventory: Inventory; onClose: () => void }) {
    const { t } = useTranslation();
    const probe = useLoad(() => call<ProbeAnswer>("netscan.probe", { inventory_id: inventory.id }));
    const data = probe.data;
    return (
        <Modal variant={ModalVariant.large} isOpen onClose={onClose}>
            <ModalHeader title={t("Probe of {{name}}", { name: inventory.name })}
                description={t("From the controller: whether each name resolves, whether the host answers an ICMP echo or a TCP connection, on its connection's port too, and whether the MAC address seen is the one the CMDB knows. Nothing is changed on the hosts.")} />
            <ModalBody>
                {probe.error && <Alert variant="danger" isInline title={probe.error.message} />}
                {!data && !probe.error && <Spinner aria-label={t("Probing")} />}
                {data && !data.icmp && (
                    <Alert variant="info" isInline style={{ marginBottom: "1rem" }}
                        title={t("ICMP echo is not allowed to the service account (net.ipv4.ping_group_range): hosts were tried by TCP and the neighbour table only.")} />
                )}
                {data && data.hosts.length === 0 && <Content component="p">{t("This inventory has no host reached over the network, as last read.")}</Content>}
                {data && data.hosts.length > 0 && (
                    <Table aria-label={t("Hosts probed")} variant="compact">
                        <Thead>
                            <Tr>
                                <Th>{t("Host")}</Th>
                                <Th>{t("Address")}</Th>
                                <Th>{t("Result")}</Th>
                                <Th>{t("Open ports")}</Th>
                                <Th>{t("MAC address")}</Th>
                                <Th>{t("Names")}</Th>
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.hosts.map(host => (
                                <Tr key={host.name}>
                                    <Td dataLabel={t("Host")}>{host.name}</Td>
                                    <Td dataLabel={t("Address")}>{host.address ? <code>{host.address}</code> : "—"}</Td>
                                    <Td dataLabel={t("Result")}><Probed host={host} /></Td>
                                    <Td dataLabel={t("Open ports")}>
                                        {host.ports.length > 0 ? host.ports.join(", ") : "—"}
                                        {host.findings.includes("port_closed") && <div><small>{t("Not {{port}}", { port: host.connection_port })}</small></div>}
                                    </Td>
                                    <Td dataLabel={t("MAC address")}>
                                        {host.mac ? <code>{host.mac}</code> : "—"}
                                        {host.findings.includes("mac_differs") && host.cmdb && (
                                            <div><small>{t("CMDB: {{macs}}", { macs: host.cmdb.macs.join(", ") })}</small></div>
                                        )}
                                        {host.mac_of.length > 0 && <div><small>{t("Also that of: {{hosts}}", { hosts: host.mac_of.join(", ") })}</small></div>}
                                    </Td>
                                    <Td dataLabel={t("Names")}>
                                        {[host.dns_name, host.netbios_name].filter(Boolean).join(", ") || "—"}
                                        {host.findings.includes("address_moved") && host.cmdb && (
                                            <div><small>{t("CMDB addresses: {{addresses}}", { addresses: host.cmdb.addresses.join(", ") })}</small></div>
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ModalBody>
        </Modal>
    );
}

