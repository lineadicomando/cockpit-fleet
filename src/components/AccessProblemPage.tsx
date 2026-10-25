import React from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Content,
    EmptyState,
    EmptyStateActions,
    EmptyStateBody,
    EmptyStateFooter,
} from "@patternfly/react-core";
import { LockIcon, ExclamationCircleIcon } from "@patternfly/react-icons";
import type { AccessProblem } from "../lib/fleet.ts";
import { ClipboardCopy } from "./pf.tsx";

interface Props {
    problem: AccessProblem;
    message: string;
    onRetry: () => void;
}

export function AccessProblemPage({ problem, message, onRetry }: Props) {
    const { t } = useTranslation();

    if (problem === "no-access") {
        return (
            <EmptyState titleText={t("You have no role in Fleet")} headingLevel="h2" icon={LockIcon}>
                <EmptyStateBody>
                    <Content component="p">
                        {t("Access is granted through three groups of this server: cockpit-fleet-viewer, cockpit-fleet-operator and cockpit-fleet-admin. Ask an administrator to add your account to one of them, then log in again.")}
                    </Content>
                    <Content component="p">
                        {t("An administrator can do it with:")}
                    </Content>
                    <ClipboardCopy isReadOnly hoverTip={t("Copy")} clickTip={t("Copied")}>
                        sudo usermod -aG cockpit-fleet-viewer USER
                    </ClipboardCopy>
                    <Content component="p">
                        {t("If you administer this server, turn on administrative access at the top of the page.")}
                    </Content>
                </EmptyStateBody>
                <EmptyStateFooter>
                    <EmptyStateActions>
                        <Button variant="primary" onClick={onRetry}>{t("Try again")}</Button>
                    </EmptyStateActions>
                </EmptyStateFooter>
            </EmptyState>
        );
    }

    return (
        <EmptyState
            titleText={problem === "not-installed"
                ? t("The Fleet backend is not installed")
                : t("The Fleet backend cannot be reached")}
            headingLevel="h2"
            icon={ExclamationCircleIcon}
            status="danger"
        >
            <EmptyStateBody>
                {problem === "not-installed"
                    ? t("Install the cockpit-fleet package on this server.")
                    : message}
            </EmptyStateBody>
            <EmptyStateFooter>
                <EmptyStateActions>
                    <Button variant="primary" onClick={onRetry}>{t("Try again")}</Button>
                </EmptyStateActions>
            </EmptyStateFooter>
        </EmptyState>
    );
}
