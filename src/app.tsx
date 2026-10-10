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
import { CredentialsPage } from "./components/CredentialsPage.tsx";
import { FilesPage } from "./components/FilesPage.tsx";
import { HostKeysPage } from "./components/HostKeysPage.tsx";
import { InventoriesPage } from "./components/InventoriesPage.tsx";
import { ProjectsPage } from "./components/ProjectsPage.tsx";
import { RunDetailPage, RunsPage } from "./components/RunsPage.tsx";
import { TemplatesPage } from "./components/TemplatesPage.tsx";
import { SessionContext } from "./lib/session.tsx";

const TABS = ["overview", "runs", "templates", "inventories", "projects", "credentials", "hostkeys", "system", "audit"];

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
        const section = TABS.includes(location.path[0]) && (location.path[0] !== "audit" || data.commands.includes("audit.list"))
            ? location.path[0]
            : "overview";
        const go = (tab: string) => cockpit.location.go(tab === "overview" ? [] : [tab]);
        const titles: Record<string, string> = {
            overview: t("Overview"), runs: t("Runs"), templates: t("Job templates"), inventories: t("Inventories"),
            projects: t("Projects"), credentials: t("Credentials"), hostkeys: t("Host keys"), system: t("System"),
            audit: t("Audit log"),
        };

        return (
            <SessionContext.Provider value={data}>
                <PageSection type="tabs" padding={{ default: "noPadding" }}>
                    <Tabs activeKey={section} onSelect={(_event, key) => go(String(key))}>
                        {TABS.filter(tab => tab !== "audit" || data.commands.includes("audit.list")).map(tab => (
                            <Tab key={tab} eventKey={tab} title={<TabTitleText>{titles[tab]}</TabTitleText>} />
                        ))}
                    </Tabs>
                </PageSection>
                <PageSection>
                    {section === "overview" && <OverviewPage session={data} onOpenSystem={() => go("system")} />}
                    {section === "runs" && (location.path[1]
                        ? <RunDetailPage key={location.path[1]} id={location.path[1]} />
                        : <RunsPage />)}
                    {section === "templates" && <TemplatesPage />}
                    {section === "inventories" && <InventoriesPage />}
                    {section === "projects" && (location.path[1]
                        ? <FilesPage key={location.path[1]} id={location.path[1]} />
                        : <ProjectsPage />)}
                    {section === "credentials" && <CredentialsPage />}
                    {section === "hostkeys" && <HostKeysPage />}
                    {section === "system" && <SystemPage />}
                    {section === "audit" && <AuditPage />}
                </PageSection>
            </SessionContext.Provider>
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
