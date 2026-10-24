import React from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Card,
    CardBody,
    CardTitle,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    Flex,
    Gallery,
    Spinner,
} from "@patternfly/react-core";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import type { Check, Session } from "../lib/types.ts";
import { StatusLabel } from "./StatusLabel.tsx";

export function OverviewPage({ session, onOpenSystem }: { session: Session; onOpenSystem: () => void }) {
    const { t } = useTranslation();
    const checks = useLoad(() => call<{ checks: Check[] }>("selfcheck"));

    const roles = {
        viewer: {
            name: t("Viewer"),
            allows: t("See the state of the fleet, the history of runs and the collected data."),
        },
        operator: {
            name: t("Operator"),
            allows: t("Everything a viewer can do, plus launching the existing job templates and acknowledging alerts."),
        },
        admin: {
            name: t("Administrator"),
            allows: t("Everything an operator can do, plus configuring projects, inventories, credentials, schedules and notifications."),
        },
    };
    // The page is not shown to an approver without a role.
    const role = roles[session.role ?? "viewer"];
    const approves = session.rights.includes("approve");

    const count = (status: Check["status"]) =>
        checks.data?.checks.filter(check => check.status === status).length ?? 0;

    return (
        <Gallery hasGutter minWidths={{ default: "22rem" }}>
            <Card>
                <CardTitle>{t("Your access")}</CardTitle>
                <CardBody>
                    <DescriptionList isHorizontal isCompact>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("User")}</DescriptionListTerm>
                            <DescriptionListDescription>{session.user}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Role")}</DescriptionListTerm>
                            <DescriptionListDescription>{approves ? t("{{role}}, approver", { role: role.name }) : role.name}</DescriptionListDescription>
                        </DescriptionListGroup>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Allows")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {role.allows}
                                {approves && <> {t("Also approving the runs of the job templates that need it, except those you launched.")}</>}
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                    </DescriptionList>
                </CardBody>
            </Card>
            <Card>
                <CardTitle>{t("Installation")}</CardTitle>
                <CardBody>
                    {checks.loading && <Spinner size="md" aria-label={t("Loading")} />}
                    {checks.error && <StatusLabel tone="bad">{checks.error.message}</StatusLabel>}
                    {checks.data && !checks.loading && (
                        <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
                            <Flex gap={{ default: "gapSm" }}>
                                {count("fail") > 0 && (
                                    <StatusLabel tone="bad">{t("checks_failed_count", { count: count("fail") })}</StatusLabel>
                                )}
                                {count("warn") > 0 && (
                                    <StatusLabel tone="warning">{t("checks_warning_count", { count: count("warn") })}</StatusLabel>
                                )}
                                <StatusLabel tone="good">{t("checks_passed_count", { count: count("ok") })}</StatusLabel>
                            </Flex>
                            <div>
                                <Button variant="link" isInline onClick={onOpenSystem}>
                                    {t("Show the checks")}
                                </Button>
                            </div>
                        </Flex>
                    )}
                    <DescriptionList isHorizontal isCompact style={{ marginBlockStart: "1rem" }}>
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Version")}</DescriptionListTerm>
                            <DescriptionListDescription>{session.version}</DescriptionListDescription>
                        </DescriptionListGroup>
                    </DescriptionList>
                </CardBody>
            </Card>
        </Gallery>
    );
}
