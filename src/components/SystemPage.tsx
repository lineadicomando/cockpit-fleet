import React from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Flex, FlexItem, Spinner, Title } from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import type { Check, CheckStatus } from "../lib/types.ts";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";

const TONE: Record<CheckStatus, Tone> = { ok: "good", warn: "warning", fail: "bad" };
const ORDER: Record<CheckStatus, number> = { fail: 0, warn: 1, ok: 2 };

export function SystemPage() {
    const { t } = useTranslation();
    const { data, error, loading, reload } = useLoad(() => call<{ checks: Check[] }>("selfcheck"));

    const labels: Record<string, string> = {
        ansible: t("Ansible"),
        systemd: t("systemd"),
        database: t("Database"),
        config: t("Configuration file"),
        state_dir: t("State directory"),
        secrets_dir: t("Secret store"),
        run_unit: t("Run unit"),
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
        </>
    );
}
