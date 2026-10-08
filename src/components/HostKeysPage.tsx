import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Content, FormGroup, Split, SplitItem, TextInput } from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { HostKey, ScannedKey } from "../lib/types.ts";
import { FormModal, ListState, SectionHeader, message } from "./common.tsx";

// Two steps on purpose: the keys a host offers are shown, and nothing is
// trusted until a person has confirmed the fingerprints.
function TrustModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [host, setHost] = useState("");
    const [port, setPort] = useState("22");
    const [keys, setKeys] = useState<ScannedKey[] | null>(null);
    const [scanning, setScanning] = useState(false);
    const [scanError, setScanError] = useState<string | null>(null);

    async function scan() {
        setScanning(true);
        setScanError(null);
        setKeys(null);
        try {
            const result = await call<{ keys: ScannedKey[] }>("hostkey.scan", { host: host.trim(), port: Number(port) || 22 });
            setKeys(result.keys);
        } catch (ex) {
            setScanError(message(ex));
        }
        setScanning(false);
    }

    async function trust() {
        for (const key of keys ?? []) {
            await call("hostkey.add", { host: key.host, key_type: key.key_type, key: key.key });
        }
        onDone();
        onClose();
    }

    return (
        <FormModal title={t("Trust a host")} submitLabel={t("Trust these keys")} onSubmit={trust} onClose={onClose} isDisabled={!keys}>
            <Split hasGutter>
                <SplitItem isFilled>
                    <FormGroup label={t("Host name or address")} isRequired fieldId="hk-host">
                        <TextInput id="hk-host" value={host} onChange={(_e, value) => { setHost(value); setKeys(null); }} />
                    </FormGroup>
                </SplitItem>
                <SplitItem>
                    <FormGroup label={t("Port")} fieldId="hk-port">
                        <TextInput id="hk-port" type="number" value={port} onChange={(_e, value) => { setPort(value); setKeys(null); }} style={{ width: "6rem" }} />
                    </FormGroup>
                </SplitItem>
            </Split>
            <div>
                <Button variant="secondary" onClick={scan} isLoading={scanning} isDisabled={!host.trim() || scanning}>
                    {t("Read the host's keys")}
                </Button>
            </div>
            {scanError && <Alert variant="danger" isInline title={scanError} />}
            {keys && (
                <>
                    <Content component="p">
                        {t("Compare these fingerprints with the ones shown on the host itself (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub). Trust them only if they match.")}
                    </Content>
                    <Table variant="compact" aria-label={t("Keys offered by the host")}>
                        <Tbody>
                            {keys.map(key => (
                                <Tr key={key.key_type}>
                                    <Td>{key.key_type}</Td>
                                    <Td><code>{key.fingerprint}</code></Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                </>
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
                {can("hostkey.add") && <Button variant="primary" onClick={() => setAdding(true)}>{t("Trust a host")}</Button>}
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
