import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button, Checkbox, Content, Flex, FlexItem, FormGroup, FormSelect, FormSelectOption, Label, Radio, Split, SplitItem, TextInput, Tooltip,
} from "@patternfly/react-core";
import { ExpandableRowContent, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { download } from "../lib/cmdb.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import {
    chosen, filterKeyHosts, groupKeyHosts, inventoriesOf, isHostKeyGroupOpen, keyHosts, NO_INVENTORY, readHostKeysView,
    withHostKeyGroupsOpen, withoutHostKeyFilters, type HostKeysView, type KeyHost, type TrustFilter,
} from "../lib/hostkeys.ts";
import type { Group } from "../lib/listview.ts";
import type { HostKey, Inventory, ScannedKey, UntrustedHost } from "../lib/types.ts";
import { FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { FilterChips, GroupByChoice, NothingMatches, useStoredView } from "./ListView.tsx";
import { ActionsColumn, Alert, ExpandCell, SearchInput } from "./pf.tsx";

interface Scan {
    keys: ScannedKey[];
    unanswered: string[];
}

// Two steps on purpose: the keys the hosts offer are shown, and nothing is
// trusted until a person has confirmed the fingerprints. A key that differs
// from the one trusted so far needs a separate, explicit consent.
// With HOSTS, the keys of those hosts are read at once, to check them again.
function TrustModal({ hosts, onClose, onDone }: { hosts?: string[]; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const inventories = useLoad(() => hosts ? Promise.resolve({ inventories: [] }) : call<{ inventories: Inventory[] }>("inventory.list"));
    const [source, setSource] = useState<"host" | "inventory">("host");
    const [host, setHost] = useState("");
    const [port, setPort] = useState("22");
    const [inventoryId, setInventoryId] = useState("");
    const [scan, setScan] = useState<Scan | null>(null);
    const [scanning, setScanning] = useState(false);
    const [scanError, setScanError] = useState<string | null>(null);
    const [replace, setReplace] = useState(false);

    const reset = () => { setScan(null); setReplace(false); };

    async function read() {
        setScanning(true);
        setScanError(null);
        reset();
        try {
            setScan(await call<Scan>("hostkey.scan", hosts
                ? { hosts }
                : source === "host"
                    ? { host: host.trim(), port: Number(port) || 22 }
                    : { inventory_id: inventoryId }));
        } catch (ex) {
            setScanError(message(ex));
        }
        setScanning(false);
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { if (hosts) read(); }, []);

    const keys = scan?.keys ?? [];
    const changed = keys.filter(key => key.state === "changed");
    const wanted = keys.filter(key => key.state === "new" || (key.state === "changed" && replace));

    async function trust() {
        for (const key of wanted) {
            await call("hostkey.add", { host: key.host, key_type: key.key_type, key: key.key, replace: key.state === "changed" });
        }
        onDone();
        onClose();
    }

    const stateLabel = (key: ScannedKey) => {
        if (key.state === "trusted") return <Label isCompact color="green">{t("Already trusted")}</Label>;
        if (key.state === "changed") return <Label isCompact color="red">{t("Changed")}</Label>;
        return <Label isCompact color="blue">{t("New")}</Label>;
    };

    const chooseSource = (
        <>
            <FormGroup role="radiogroup" isInline fieldId="hk-source" label={t("Hosts")}>
                <Radio id="hk-source-host" name="hk-source" label={t("One host")} isChecked={source === "host"} onChange={() => { setSource("host"); reset(); }} />
                <Radio id="hk-source-inventory" name="hk-source" label={t("All hosts of an inventory")} isChecked={source === "inventory"} onChange={() => { setSource("inventory"); reset(); }} />
            </FormGroup>
            {source === "host" ? (
                <Split hasGutter>
                    <SplitItem isFilled>
                        <FormGroup label={t("Host name or address")} isRequired fieldId="hk-host">
                            <TextInput id="hk-host" value={host} onChange={(_e, value) => { setHost(value); reset(); }} />
                        </FormGroup>
                    </SplitItem>
                    <SplitItem>
                        <FormGroup label={t("Port")} fieldId="hk-port">
                            <TextInput id="hk-port" type="number" value={port} onChange={(_e, value) => { setPort(value); reset(); }} style={{ width: "6rem" }} />
                        </FormGroup>
                    </SplitItem>
                </Split>
            ) : (
                <FormGroup label={t("Inventory")} isRequired fieldId="hk-inventory">
                    <FormSelect id="hk-inventory" value={inventoryId} onChange={(_e, value) => { setInventoryId(value); reset(); }}>
                        <FormSelectOption value="" label={t("Choose an inventory")} isPlaceholder />
                        {(inventories.data?.inventories ?? []).map(inventory => (
                            <FormSelectOption key={inventory.id} value={inventory.id} label={`${inventory.name} (${inventory.host_count})`} />
                        ))}
                    </FormSelect>
                </FormGroup>
            )}
            <div>
                <Button variant="secondary" onClick={read} isLoading={scanning}
                    isDisabled={scanning || (source === "host" ? !host.trim() : !inventoryId)}>
                    {t("Read the keys")}
                </Button>
            </div>
        </>
    );

    return (
        <FormModal title={hosts ? t("Check the keys again") : t("Trust hosts")} submitLabel={t("trust_keys_count", { count: wanted.length })}
            onSubmit={trust} onClose={onClose} isDisabled={wanted.length === 0}>
            {hosts ? (
                <Content component="p">
                    {t("The keys that these hosts offer now: {{hosts}}", { hosts: hosts.length > 10 ? `${hosts.slice(0, 10).join(", ")}…` : hosts.join(", ") })}
                </Content>
            ) : chooseSource}
            {hosts && scanning && <Content component="p">{t("Reading the keys…")}</Content>}
            {scanError && <Alert variant="danger" isInline title={scanError} />}
            {scan && scan.unanswered.length > 0 && (
                <Alert variant="warning" isInline title={t("No answer from: {{hosts}}", { hosts: scan.unanswered.join(", ") })} />
            )}
            {scan && hosts && keys.length > 0 && keys.every(key => key.state === "trusted") && (
                <Alert variant="success" isInline title={t("Every key offered is the one trusted")} />
            )}
            {scan && keys.length > 0 && (
                <>
                    <Content component="p">
                        {t("Compare these fingerprints with the ones shown on the host itself (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub). Trust them only if they match.")}
                    </Content>
                    <div style={{ maxBlockSize: "22rem", overflow: "auto" }}>
                        <Table variant="compact" aria-label={t("Keys offered by the hosts")}>
                            <Thead><Tr><Th>{t("Host")}</Th><Th>{t("Key type")}</Th><Th>{t("Fingerprint")}</Th><Th>{t("Status")}</Th></Tr></Thead>
                            <Tbody>
                                {keys.map(key => (
                                    <Tr key={`${key.host} ${key.key_type}`}>
                                        <Td>{key.host}</Td>
                                        <Td>{key.key_type}</Td>
                                        <Td><code>{key.fingerprint}</code></Td>
                                        <Td>{stateLabel(key)}</Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                    </div>
                </>
            )}
            {changed.length > 0 && (
                <Alert variant="danger" isInline title={t("The key of a host already trusted has changed")}>
                    <Content component="p">
                        {t("This happens when a host is reinstalled, and also when something is impersonating it. Replace the trusted key only if you know why it changed.")}
                    </Content>
                    <Checkbox id="hk-replace" label={t("Replace the trusted keys of: {{hosts}}", { hosts: [...new Set(changed.map(key => key.host))].join(", ") })}
                        isChecked={replace} onChange={(_e, value) => setReplace(value)} />
                </Alert>
            )}
        </FormModal>
    );
}

// ecdsa-sha2-nistp256 is an ecdsa key, ssh-ed25519 an ed25519 one.
function shortType(keyType: string): string {
    if (keyType.startsWith("ecdsa-")) return "ecdsa";
    return keyType.replace(/^ssh-/, "");
}

function KeyTypes({ host }: { host: KeyHost }) {
    const { t } = useTranslation();
    if (!host.trusted) return <Label isCompact color="orange">{t("Not trusted")}</Label>;
    return (
        <Flex gap={{ default: "gapXs" }}>
            {host.keys.map(key => (
                <Tooltip key={key.id} content={<code>{key.fingerprint}</code>}>
                    <Label isCompact color="green">{shortType(key.key_type)}</Label>
                </Tooltip>
            ))}
        </Flex>
    );
}

function inventoryNames(host: KeyHost): string {
    return host.inventories.map(item => item.host === host.address ? item.name : `${item.name} (${item.host})`).join(", ");
}

// A list of hosts in a sentence, the first few only.
function someHosts(hosts: string[]): string {
    return hosts.length > 20 ? `${hosts.slice(0, 20).join(", ")}…` : hosts.join(", ");
}

function RemoveHostsModal({ hosts, onClose, onDone }: { hosts: KeyHost[]; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const names = hosts.map(host => host.host);
    async function remove() {
        await call("hostkey.remove", { hosts: names });
        onDone();
        onClose();
    }
    return (
        <FormModal title={t("stop_trusting_hosts_count", { count: hosts.length })} submitLabel={t("Stop trusting")}
            onSubmit={remove} onClose={onClose} isDanger>
            <Content component="p">
                {t("Every key trusted for these hosts goes, and runs refuse to connect to them until their keys are trusted again: {{hosts}}", { hosts: someHosts(names) })}
            </Content>
        </FormModal>
    );
}

function HostKeysToolbar({ view, onChange, inventories, groups, shown, total }: {
    view: HostKeysView;
    onChange: (view: HostKeysView) => void;
    inventories: { id: string; name: string }[];
    groups: Group<KeyHost>[];
    shown: number;
    total: number;
}) {
    const { t } = useTranslation();
    const set = (change: Partial<HostKeysView>) => onChange({ ...view, ...change });
    const trustNames: Record<Exclude<TrustFilter, "">, string> = { trusted: t("Trusted"), untrusted: t("Not trusted") };
    const inventoryName = view.inventory === NO_INVENTORY ? t("In no inventory") : inventories.find(item => item.id === view.inventory)?.name ?? "";
    const allOpen = groups.every(group => isHostKeyGroupOpen(view, group));
    const chip = (key: string, text: string, clear: Partial<HostKeysView>) => ({ key, text, onClear: () => set(clear) });
    const chips = [
        ...(view.search.trim() ? [chip("search", t("Text: {{text}}", { text: view.search.trim() }), { search: "" })] : []),
        ...(view.inventory ? [chip("inventory", t("Inventory: {{value}}", { value: inventoryName }), { inventory: "" })] : []),
        ...(view.trust ? [chip("trust", trustNames[view.trust], { trust: "" })] : []),
    ];

    return (
        <div style={{ marginBlockEnd: "1rem" }}>
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                <FlexItem style={{ width: "18rem" }}>
                    <SearchInput aria-label={t("Filter hosts")} placeholder={t("Host, name in an inventory, fingerprint…")} value={view.search}
                        onChange={(_e, value) => set({ search: value })} onClear={() => set({ search: "" })} />
                </FlexItem>
                <FlexItem style={{ width: "12rem" }}>
                    <FormSelect aria-label={t("Inventory")} value={view.inventory} onChange={(_e, value) => set({ inventory: value })}>
                        <FormSelectOption value="" label={t("Every inventory")} />
                        {inventories.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        <FormSelectOption value={NO_INVENTORY} label={t("In no inventory")} />
                    </FormSelect>
                </FlexItem>
                <FlexItem style={{ width: "12rem" }}>
                    <FormSelect aria-label={t("Filter by trust")} value={view.trust} onChange={(_e, value) => set({ trust: value as TrustFilter })}>
                        <FormSelectOption value="" label={t("Trusted or not")} />
                        {Object.entries(trustNames).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                    </FormSelect>
                </FlexItem>
                <GroupByChoice id="hk-group-by" value={view.groupBy} onChange={groupBy => set({ groupBy })}
                    options={[["host", t("Host")], ["inventory", t("Inventory")], ["none", t("Nothing")]]}
                    groups={groups.length} allOpen={allOpen} onToggleAll={() => onChange(withHostKeyGroupsOpen(view, groups, !allOpen))} />
            </Flex>
            <FilterChips chips={chips} onClearAll={() => onChange(withoutHostKeyFilters(view))} shown={shown} total={total} />
        </div>
    );
}

export function HostKeysPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ keys: HostKey[]; untrusted: UntrustedHost[] }>("hostkey.list"));
    const [stored, setView] = useStoredView("hostkeys", readHostKeysView);
    const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
    const [adding, setAdding] = useState(false);
    const [checking, setChecking] = useState<string[] | null>(null);
    const [removing, setRemoving] = useState<KeyHost[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    async function removeKey(key: HostKey) {
        setError(null);
        try {
            await call("hostkey.remove", { id: key.id });
            list.reload();
        } catch (ex) {
            setError(message(ex));
        }
    }

    async function exportKeys(hosts: KeyHost[]) {
        setError(null);
        try {
            const result = await call<{ text: string }>("hostkey.export", { hosts: hosts.map(host => host.host) });
            download(result.text, "known_hosts", "text/plain");
        } catch (ex) {
            setError(message(ex));
        }
    }

    const choose = (hosts: KeyHost[], value: boolean) => {
        const next = new Set(selected);
        for (const host of hosts) {
            if (value) next.add(host.host);
            else next.delete(host.host);
        }
        setSelected(next);
    };

    const hostActions = (host: KeyHost) => [
        ...(can("hostkey.scan") ? [{ title: host.trusted ? t("Check the keys again") : t("Read the keys"), onClick: () => setChecking([host.host]) }] : []),
        ...(host.trusted && can("hostkey.export") ? [{ title: t("Export"), onClick: () => exportKeys([host]) }] : []),
        ...(host.trusted && can("hostkey.remove") ? [{ title: t("Stop trusting"), onClick: () => setRemoving([host]) }] : []),
    ];

    return (
        <>
            <SectionHeader title={t("Trusted host keys")}>
                {can("hostkey.add") && <Button variant="primary" onClick={() => setAdding(true)}>{t("Trust hosts")}</Button>}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.keys.length === 0 && data.untrusted.length === 0}
                emptyTitle={t("No trusted host keys")}
                emptyBody={t("Runs only connect over SSH to hosts whose key is listed here. A host with an unknown or changed key is refused.")}>
                {data => {
                    const hosts = keyHosts(data.keys, data.untrusted);
                    const inventories = inventoriesOf(hosts);
                    // An inventory kept in the view may have gone since.
                    const view = stored.inventory && stored.inventory !== NO_INVENTORY && !inventories.some(item => item.id === stored.inventory)
                        ? { ...stored, inventory: "" } : stored;
                    const shown = filterKeyHosts(hosts, view);
                    const groups = groupKeyHosts(shown, view.groupBy, view.inventory);
                    const picked = chosen(shown, selected);
                    const pickedTrusted = picked.filter(host => host.trusted);
                    const allShown = shown.length > 0 && picked.length === shown.length;
                    const byHost = view.groupBy === "host";
                    const byInventory = view.groupBy === "inventory";
                    const width = (byHost || byInventory ? 1 : 0) + 8;

                    const select = (host: KeyHost, id: string) => (
                        <Td>
                            <Checkbox id={id} aria-label={t("Select {{name}}", { name: host.host })} isChecked={selected.has(host.host)}
                                onChange={(_e, value) => choose([host], value)} />
                        </Td>
                    );

                    const hostCells = (host: KeyHost) => (
                        <>
                            <Td dataLabel={t("Host")}>{host.address}</Td>
                            <Td dataLabel={t("Port")}>{host.port}</Td>
                            <Td dataLabel={t("Keys")}><KeyTypes host={host} /></Td>
                            <Td dataLabel={t("Inventories")}>{inventoryNames(host)}</Td>
                            <Td dataLabel={t("Trusted by")}>{host.added_by}</Td>
                            <Td dataLabel={t("When")}>{host.trusted ? formatTime(host.added_at, i18n.language) : ""}</Td>
                            <Td isActionCell>
                                {hostActions(host).length > 0 && <ActionsColumn items={hostActions(host)} />}
                            </Td>
                        </>
                    );

                    const keyTable = (host: KeyHost) => (
                        <Table variant="compact" borders={false} aria-label={t("Keys of {{host}}", { host: host.host })}>
                            <Thead><Tr><Th>{t("Key type")}</Th><Th>{t("Fingerprint")}</Th><Th>{t("Trusted by")}</Th><Th>{t("When")}</Th><Th screenReaderText={t("Actions")} /></Tr></Thead>
                            <Tbody>
                                {host.keys.map(key => (
                                    <Tr key={key.id}>
                                        <Td dataLabel={t("Key type")}>{key.key_type}</Td>
                                        <Td dataLabel={t("Fingerprint")}><code>{key.fingerprint}</code></Td>
                                        <Td dataLabel={t("Trusted by")}>{key.added_by}</Td>
                                        <Td dataLabel={t("When")}>{formatTime(key.added_at, i18n.language)}</Td>
                                        <Td isActionCell>
                                            {can("hostkey.remove") && <ActionsColumn items={[{ title: t("Stop trusting this key"), onClick: () => removeKey(key) }]} />}
                                        </Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                    );

                    const body = () => {
                        if (byHost) {
                            return groups.map((group, index) => {
                                const host = group.items[0];
                                const open = host.trusted && isHostKeyGroupOpen(view, group);
                                return (
                                    <Tbody key={group.key} isExpanded={open}>
                                        <Tr>
                                            {host.trusted
                                                ? <ExpandCell isExpanded={open} label={host.host} onToggle={() => setView(withHostKeyGroupsOpen(view, [group], !open))} />
                                                : <Td />}
                                            {select(host, `hk-select-${index}`)}
                                            {hostCells(host)}
                                        </Tr>
                                        {host.trusted && (
                                            <Tr isExpanded={open}>
                                                <Td />
                                                <Td colSpan={width - 1}>
                                                    <ExpandableRowContent>{keyTable(host)}</ExpandableRowContent>
                                                </Td>
                                            </Tr>
                                        )}
                                    </Tbody>
                                );
                            });
                        }
                        if (byInventory) {
                            return groups.map((group, groupIndex) => {
                                const open = isHostKeyGroupOpen(view, group);
                                const all = group.items.every(host => selected.has(host.host));
                                const some = group.items.some(host => selected.has(host.host));
                                const title = group.key === NO_INVENTORY ? t("In no inventory") : group.name;
                                return (
                                    <Tbody key={group.key} isExpanded={open}>
                                        <Tr>
                                            <ExpandCell isExpanded={open} label={title} onToggle={() => setView(withHostKeyGroupsOpen(view, [group], !open))} />
                                            <Td>
                                                <Checkbox id={`hk-group-${groupIndex}`} aria-label={t("Select {{name}}", { name: title })}
                                                    isChecked={all ? true : some ? null : false} onChange={(_e, value) => choose(group.items, value)} />
                                            </Td>
                                            <Td colSpan={width - 2}>
                                                <Flex gap={{ default: "gapMd" }} alignItems={{ default: "alignItemsCenter" }}>
                                                    <strong>{title}</strong>
                                                    <span className="pf-v6-u-font-size-sm pf-v6-u-text-color-subtle">{t("hosts_count", { count: group.items.length })}</span>
                                                </Flex>
                                            </Td>
                                        </Tr>
                                        {group.items.map((host, index) => (
                                            <Tr key={host.host} isExpanded={open}>
                                                <Td />
                                                {select(host, `hk-select-${groupIndex}-${index}`)}
                                                {hostCells(host)}
                                            </Tr>
                                        ))}
                                    </Tbody>
                                );
                            });
                        }
                        return (
                            <Tbody>
                                {shown.map((host, index) => <Tr key={host.host}>{select(host, `hk-select-${index}`)}{hostCells(host)}</Tr>)}
                            </Tbody>
                        );
                    };

                    return (
                        <>
                            <HostKeysToolbar view={view} onChange={setView} inventories={inventories} groups={groups} shown={shown.length} total={hosts.length} />
                            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }} style={{ marginBlockEnd: "0.5rem" }}>
                                <Content component="small">{t("hosts_selected_count", { count: picked.length })}</Content>
                                {can("hostkey.scan") && (
                                    <Button variant="secondary" size="sm" isDisabled={picked.length === 0} onClick={() => setChecking(picked.map(host => host.host))}>
                                        {t("Check the keys again")}
                                    </Button>
                                )}
                                {can("hostkey.export") && (
                                    <Button variant="secondary" size="sm" isDisabled={pickedTrusted.length === 0} onClick={() => exportKeys(pickedTrusted)}>
                                        {t("Export")}
                                    </Button>
                                )}
                                {can("hostkey.remove") && (
                                    <Button variant="danger" size="sm" isDisabled={pickedTrusted.length === 0} onClick={() => setRemoving(pickedTrusted)}>
                                        {t("Stop trusting")}
                                    </Button>
                                )}
                                {picked.length > 0 && <Button variant="link" isInline onClick={() => setSelected(new Set())}>{t("Clear the selection")}</Button>}
                            </Flex>
                            {shown.length === 0 ? (
                                <NothingMatches title={t("No host matches the filters")} onClearAll={() => setView(withoutHostKeyFilters(view))} />
                            ) : (
                                <Table variant="compact" aria-label={t("Trusted host keys")}>
                                    <Thead>
                                        <Tr>
                                            {(byHost || byInventory) && <Th screenReaderText={t("Group")} />}
                                            <Th>
                                                <Checkbox id="hk-select-all" aria-label={t("Select all")} isChecked={allShown ? true : picked.length ? null : false}
                                                    onChange={(_e, value) => choose(shown, value)} />
                                            </Th>
                                            <Th>{t("Host")}</Th><Th>{t("Port")}</Th><Th>{t("Keys")}</Th><Th>{t("Inventories")}</Th>
                                            <Th>{t("Trusted by")}</Th><Th>{t("When")}</Th><Th screenReaderText={t("Actions")} />
                                        </Tr>
                                    </Thead>
                                    {body()}
                                </Table>
                            )}
                        </>
                    );
                }}
            </ListState>
            {adding && <TrustModal onClose={() => setAdding(false)} onDone={list.reload} />}
            {checking && <TrustModal hosts={checking} onClose={() => setChecking(null)} onDone={list.reload} />}
            {removing && (
                <RemoveHostsModal hosts={removing} onClose={() => setRemoving(null)}
                    onDone={() => { choose(removing, false); list.reload(); }} />
            )}
        </>
    );
}
