import React from "react";
import { useTranslation } from "react-i18next";
import {
    EmptyState,
    EmptyStateBody,
    Page,
    PageSection,
    Title,
} from "@patternfly/react-core";

export function App() {
    const { t } = useTranslation();

    return (
        <Page sidebar={null}>
            <PageSection>
                <Title headingLevel="h1" size="2xl">
                    Fleet
                </Title>
            </PageSection>
            <PageSection>
                <EmptyState titleText={t("Nothing to show yet")} headingLevel="h2">
                    <EmptyStateBody>
                        {t("cockpit-fleet is installed. Its features will appear here as they are developed.")}
                    </EmptyStateBody>
                </EmptyState>
            </PageSection>
        </Page>
    );
}
