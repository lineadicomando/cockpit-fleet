import React from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    Bullseye,
    Page,
    PageSection,
    Tab,
    TabTitleText,
    Title,
} from "@patternfly/react-core";
import { AccessError, FleetError, call, type AccessProblem } from "./lib/fleet.ts";
import { useCockpitLocation, useLoad } from "./lib/hooks.ts";
import type { Session } from "./lib/types.ts";
import { AccessProblemPage } from "./components/AccessProblemPage.tsx";
import { PendingApprovals } from "./components/ApprovalsPage.tsx";
import { AuditPage } from "./components/AuditPage.tsx";
import { ChatPage } from "./components/ChatPage.tsx";
import { VariablesPage } from "./components/VariablesPage.tsx";
import { AssetsPage } from "./components/AssetsPage.tsx";
import { ChecksPage } from "./components/ChecksPage.tsx";
import { HostDetailPage, HostsPage } from "./components/HostsPage.tsx";
import { OverviewPage } from "./components/OverviewPage.tsx";
import { SystemPage } from "./components/SystemPage.tsx";
import { CredentialsPage } from "./components/CredentialsPage.tsx";
import { FilesPage } from "./components/FilesPage.tsx";
import { HostKeysPage } from "./components/HostKeysPage.tsx";
import { InventoriesPage } from "./components/InventoriesPage.tsx";
import { ProjectsPage } from "./components/ProjectsPage.tsx";
import { StorePage } from "./components/StorePage.tsx";
import { RunDetailPage, RunsPage } from "./components/RunsPage.tsx";
import { NotificationsPage } from "./components/NotificationsPage.tsx";
import { SchedulesPage } from "./components/SchedulesPage.tsx";
import { TemplatesPage } from "./components/TemplatesPage.tsx";
import { WorkflowsPage } from "./components/WorkflowsPage.tsx";
import { SessionContext } from "./lib/session.tsx";
import { SECTIONS, groupOf, visibleGroups } from "./lib/sections.ts";
import { Spinner, TabPanel, Tabs } from "./components/pf.tsx";

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
        if (data.role === null) {
            // An approver with no role: what waits for approval, and nothing else.
            return (
                <SessionContext.Provider value={data}>
                    <PageSection><PendingApprovals standalone /></PageSection>
                </SessionContext.Provider>
            );
        }
        // Not every tab is for everyone: the audit trail is for administrators,
        // and the chat is there when config.toml names an agent for it, or for
        // administrators, who find there how to set it up.
        const visible = (tab: string) => (tab !== "audit" || data.commands.includes("audit.list")) && (tab !== "chat" || data.chat || data.role === "admin");
        const groups = visibleGroups(visible);
        const section = SECTIONS.includes(location.path[0]) && visible(location.path[0]) ? location.path[0] : "overview";
        const group = groupOf(groups, section);
        const go = (tab: string) => cockpit.location.go(tab === "overview" ? [] : [tab]);
        const groupTitles: Record<string, string> = {
            overview: t("Overview"), nodes: t("Nodes"), automation: t("Automation"), resources: t("Resources"), administration: t("Administration"), chat: t("Assistant"),
        };
        const titles: Record<string, string> = {
            overview: t("Overview"), hosts: t("Hosts"), assets: t("Assets"), checks: t("Checks"), runs: t("Runs"), templates: t("Job templates"), workflows: t("Workflows"), schedules: t("Schedules"), notifications: t("Notifications"), inventories: t("Inventories"),
            projects: t("Projects"), store: t("Store"), credentials: t("Credentials"), variables: t("Variables"), hostkeys: t("Host keys"), system: t("System"),
            audit: t("Audit log"), chat: t("Assistant"),
        };

        // A group of one section has no second row: its tab is the section's.
        return (
            <SessionContext.Provider value={data}>
                <PageSection type="tabs" padding={{ default: "noPadding" }}>
                    <Tabs panelId={group.sections.length > 1 ? "fleet-group" : "fleet-section"} activeKey={group.key}
                        onSelect={(_event, key) => go(groups.find(item => item.key === key)?.sections[0] ?? "overview")}>
                        {groups.map(item => (
                            <Tab key={item.key} eventKey={item.key} title={<TabTitleText>{groupTitles[item.key]}</TabTitleText>} />
                        ))}
                    </Tabs>
                    {group.sections.length > 1 && (
                        <TabPanel panelId="fleet-group" active={group.key}>
                            <Tabs panelId="fleet-section" activeKey={section} isSubtab aria-label={groupTitles[group.key]}
                                onSelect={(_event, key) => go(String(key))}>
                                {group.sections.map(tab => (
                                    <Tab key={tab} eventKey={tab} title={<TabTitleText>{titles[tab]}</TabTitleText>} />
                                ))}
                            </Tabs>
                        </TabPanel>
                    )}
                </PageSection>
                <PageSection isFilled={filled} className={filled ? "fleet-fill" : undefined}>
                    <TabPanel panelId="fleet-section" active={section}>
                        {section === "overview" && <OverviewPage session={data} onOpenSystem={() => go("system")} />}
                        {section === "hosts" && (location.path[1]
                            ? <HostDetailPage key={location.path[1]} id={location.path[1]} />
                            : <HostsPage />)}
                        {section === "assets" && <AssetsPage />}
                        {section === "checks" && <ChecksPage />}
                        {section === "runs" && (location.path[1]
                            ? <RunDetailPage key={location.path[1]} id={location.path[1]} />
                            : <RunsPage />)}
                        {section === "templates" && <TemplatesPage />}
                        {section === "workflows" && <WorkflowsPage />}
                        {section === "schedules" && <SchedulesPage />}
                        {section === "notifications" && <NotificationsPage />}
                        {section === "inventories" && (location.path[1]
                            ? <FilesPage key={location.path.slice(1).join("/")} id={location.path[1]}
                                draft={location.path[2] === "drafts" ? location.path[3] ?? "" : ""} />
                            : <InventoriesPage />)}
                        {section === "projects" && (location.path[1]
                            ? <FilesPage key={location.path.slice(1).join("/")} id={location.path[1]}
                                draft={location.path[2] === "drafts" ? location.path[3] ?? "" : ""} />
                            : <ProjectsPage />)}
                        {section === "store" && <StorePage directory={location.path.slice(1).join("/")} />}
                        {section === "credentials" && <CredentialsPage />}
                        {section === "variables" && <VariablesPage />}
                        {section === "hostkeys" && <HostKeysPage />}
                        {section === "chat" && <ChatPage />}
                        {section === "system" && <SystemPage />}
                        {section === "audit" && <AuditPage />}
                    </TabPanel>
                </PageSection>
            </SessionContext.Provider>
        );
    }

    // The chat takes what is left of the window, down to its bottom.
    const filled = location.path[0] === "chat";
    return (
        <Page sidebar={null} isContentFilled={filled}>
            <PageSection>
                <Title headingLevel="h1" size="2xl">Fleet</Title>
            </PageSection>
            {body()}
        </Page>
    );
}
