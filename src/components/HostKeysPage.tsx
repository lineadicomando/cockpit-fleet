import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Checkbox, Content, FormGroup, FormSelect, FormSelectOption, Label, Radio, Split, SplitItem, TextInput } from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { HostKey, Inventory, ScannedKey } from "../lib/types.ts";
import { FormModal, ListState, SectionHeader, message } from "./common.tsx";

interface Scan {
    keys: ScannedKey[];
    unanswered: string[];
}

// Two steps on purpose: the keys the hosts offer are shown, and nothing is
// trusted until a person has confirmed the fingerprints. A key that differs
// from the one trusted so far needs a separate, explicit consent.
function TrustModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const inventories = useLoad(() => call<{ inventories: Inventory[] }>("inventory.list"));
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
            setScan(await call<Scan>("hostkey.scan", source === "host"
                ? { host: host.trim(), port: Number(port) || 22 }
                : { inventory_id: inventoryId }));
        } catch (ex) {
            setScanError(message(ex));
        }
        setScanning(false);
    }

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

    return (
        <FormModal title={t("Trust hosts")} submitLabel={t("trust_keys_count", { count: wanted.length })}
            onSubmit={trust} onClose={onClose} isDisabled={wanted.length === 0}>
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
            {scanError && <Alert variant="danger" isInline title={scanError} />}
            {scan && scan.unanswered.length > 0 && (
                <Alert variant="warning" isInline title={t("No answer from: {{hosts}}", { hosts: scan.unanswered.join(", ") })} />
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

export function HostKeysPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ keys: HostKey[] }>("hostkey.list"));
    const [adding, setAdding] = useState(false);
    const [error, setError] = useState<string | null>(null);

    async function remove(key: HostKey) {
        setError(null);
        try {
            await call("hostkey.remove", { id: key.id });
            list.reload();
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            <SectionHeader title={t("Trusted host keys")}>
                {can("hostkey.add") && <Button variant="primary" onClick={() => setAdding(true)}>{t("Trust hosts")}</Button>}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.keys.length === 0}
                emptyTitle={t("No trusted host keys")}
                emptyBody={t("Runs only connect over SSH to hosts whose key is listed here. A host with an unknown or changed key is refused.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Trusted host keys")}>
                        <Thead>
                            <Tr><Th>{t("Host")}</Th><Th>{t("Key type")}</Th><Th>{t("Fingerprint")}</Th><Th>{t("Trusted by")}</Th><Th>{t("When")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                        </Thead>
                        <Tbody>
                            {data.keys.map(key => (
                                <Tr key={key.id}>
                                    <Td dataLabel={t("Host")}>{key.host}</Td>
                                    <Td dataLabel={t("Key type")}>{key.key_type}</Td>
                                    <Td dataLabel={t("Fingerprint")}><code>{key.fingerprint}</code></Td>
                                    <Td dataLabel={t("Trusted by")}>{key.added_by}</Td>
                                    <Td dataLabel={t("When")}>{formatTime(key.added_at, i18n.language)}</Td>
                                    <Td isActionCell>
                                        {can("hostkey.remove") && <ActionsColumn items={[{ title: t("Stop trusting"), onClick: () => remove(key) }]} />}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {adding && <TrustModal onClose={() => setAdding(false)} onDone={list.reload} />}
        </>
    );
}
