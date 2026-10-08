import React from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    Bullseye,
    Page,
    PageSection,
    Spinner,
    Tab,
    Tabs,
    TabTitleText,
    Title,
} from "@patternfly/react-core";
import { AccessError, FleetError, call, type AccessProblem } from "./lib/fleet.ts";
import { useCockpitLocation, useLoad } from "./lib/hooks.ts";
import type { Session } from "./lib/types.ts";
import { AccessProblemPage } from "./components/AccessProblemPage.tsx";
import { AuditPage } from "./components/AuditPage.tsx";
import { OverviewPage } from "./components/OverviewPage.tsx";
import { SystemPage } from "./components/SystemPage.tsx";

// A refusal by the backend itself means the same as one by sudo.
function accessProblem(error: Error): AccessProblem {
    if (error instanceof AccessError) return error.problem;
    return error instanceof FleetError && error.code === "forbidden" ? "no-access" : "failed";
}

export function App() {
    const { t } = useTranslation();
    const session = useLoad(() => call<Session>("whoami"));
    const location = useCockpitLocation();

    function body() {
        if (session.error) {
            const error = session.error;
            return (
                <PageSection>
                    <AccessProblemPage
                        problem={accessProblem(error)}
                        message={error.message}
                        onRetry={session.reload}
                    />
                </PageSection>
            );
        }
        if (!session.data) {
            return (
                <PageSection>
                    <Bullseye><Spinner aria-label={t("Loading")} /></Bullseye>
                </PageSection>
            );
        }

        const data = session.data;
        // The backend decides; the tab is only hidden so as not to offer a dead end.
        const tabs = ["overview", "system", ...(data.commands.includes("audit.list") ? ["audit"] : [])];
        const active = tabs.includes(location.path[0]) ? location.path[0] : "overview";
        const go = (tab: string) => cockpit.location.go(tab === "overview" ? [] : [tab]);

        return (
            <>
                <PageSection type="tabs" padding={{ default: "noPadding" }}>
                    <Tabs activeKey={active} onSelect={(_event, key) => go(String(key))}>
                        <Tab eventKey="overview" title={<TabTitleText>{t("Overview")}</TabTitleText>} />
                        <Tab eventKey="system" title={<TabTitleText>{t("System")}</TabTitleText>} />
                        {tabs.includes("audit") && (
                            <Tab eventKey="audit" title={<TabTitleText>{t("Audit log")}</TabTitleText>} />
                        )}
                    </Tabs>
                </PageSection>
                <PageSection>
                    {active === "overview" && <OverviewPage session={data} onOpenSystem={() => go("system")} />}
                    {active === "system" && <SystemPage />}
                    {active === "audit" && <AuditPage />}
                </PageSection>
            </>
        );
    }

    return (
        <Page sidebar={null}>
            <PageSection>
                <Title headingLevel="h1" size="2xl">Fleet</Title>
            </PageSection>
            {body()}
        </Page>
    );
}
