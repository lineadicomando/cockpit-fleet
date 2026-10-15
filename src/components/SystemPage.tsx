import React, { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Content, Flex, FlexItem, Spinner, Title } from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Check, CheckStatus, ImportReport } from "../lib/types.ts";
import { message } from "./common.tsx";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";

const TONE: Record<CheckStatus, Tone> = { ok: "good", warn: "warning", fail: "bad" };
const ORDER: Record<CheckStatus, number> = { fail: 0, warn: 1, ok: 2 };

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
            <Flex justifyContent={{ default: "justifyContentSpaceBetween" }}>
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
