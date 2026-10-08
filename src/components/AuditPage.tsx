import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, EmptyState, EmptyStateBody, Flex, FlexItem, Spinner, Title } from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call } from "../lib/fleet.ts";
import type { AuditEntry, AuditOutcome } from "../lib/types.ts";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";

const PAGE = 50;
const TONE: Record<AuditOutcome, Tone> = { ok: "good", denied: "warning", error: "bad" };

interface Page {
    entries: AuditEntry[];
    more: boolean;
}

export function AuditPage() {
    const { t, i18n } = useTranslation();
    const [entries, setEntries] = useState<AuditEntry[]>([]);
    const [more, setMore] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    function load(before?: number) {
        setLoading(true);
        call<Page>("audit.list", before === undefined ? { limit: PAGE } : { limit: PAGE, before }).then(
            page => {
                setEntries(current => (before === undefined ? page.entries : [...current, ...page.entries]));
                setMore(page.more);
                setError(null);
                setLoading(false);
            },
            ex => {
                setError(ex instanceof Error ? ex.message : String(ex));
                setLoading(false);
            },
        );
    }

    useEffect(() => load(), []);

    const outcomes: Record<AuditOutcome, string> = {
        ok: t("Done"),
        denied: t("Refused"),
        error: t("Failed"),
    };
    const when = new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "medium" });

    return (
        <>
            <Flex justifyContent={{ default: "justifyContentSpaceBetween" }}>
                <FlexItem>
                    <Title headingLevel="h2" size="lg">{t("Audit log")}</Title>
                </FlexItem>
                <FlexItem>
                    <Button variant="secondary" onClick={() => load()} isDisabled={loading}>
                        {t("Refresh")}
                    </Button>
                </FlexItem>
            </Flex>
            {error && <Alert variant="danger" isInline title={error} />}
            {entries.length === 0 && loading && <Spinner aria-label={t("Loading")} />}
            {entries.length === 0 && !loading && !error && (
                <EmptyState titleText={t("Nothing recorded yet")} headingLevel="h3">
                    <EmptyStateBody>
                        {t("Changes made through Fleet and refused attempts are listed here.")}
                    </EmptyStateBody>
                </EmptyState>
            )}
            {entries.length > 0 && (
                <Table variant="compact" aria-label={t("Audit log")}>
                    <Thead>
                        <Tr>
                            <Th>{t("When")}</Th>
                            <Th>{t("User")}</Th>
                            <Th>{t("Action")}</Th>
                            <Th>{t("Outcome")}</Th>
                            <Th>{t("Details")}</Th>
                        </Tr>
                    </Thead>
                    <Tbody>
                        {entries.map(entry => (
                            <Tr key={entry.id}>
                                <Td dataLabel={t("When")}>{when.format(new Date(entry.ts * 1000))}</Td>
                                <Td dataLabel={t("User")}>{entry.user}</Td>
                                <Td dataLabel={t("Action")}><code>{entry.command}</code></Td>
                                <Td dataLabel={t("Outcome")}>
                                    <StatusLabel tone={TONE[entry.outcome]}>{outcomes[entry.outcome]}</StatusLabel>
                                </Td>
                                <Td dataLabel={t("Details")}>{entry.detail}</Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
            {more && (
                <Button
                    variant="link"
                    onClick={() => load(entries[entries.length - 1].id)}
                    isDisabled={loading}
                >
                    {t("Show older entries")}
                </Button>
            )}
        </>
    );
}
