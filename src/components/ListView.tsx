// The pieces of a long list that the pages share: the filters in use, the
// choice of grouping, groups that open and close, the view kept in the browser.

import React from "react";
import { useTranslation } from "react-i18next";
import { Button, Content, EmptyState, EmptyStateFooter, Flex, FlexItem, Label, ToggleGroup, ToggleGroupItem } from "@patternfly/react-core";
import { Td, Tr } from "@patternfly/react-table";
import { useStoredState } from "../lib/hooks.ts";
import { useSession } from "../lib/session.tsx";
import { ExpandCell } from "./pf.tsx";

// A view of a list kept per user of Cockpit, so that it outlives a change of tab.
export function useStoredView<T>(name: string, read: (text: string | null) => T): [T, (view: T) => void] {
    const session = useSession();
    return useStoredState(`cockpit-fleet.${name}.${session.user}`, read);
}

export interface Chip {
    key: string;
    text: string;
    onClear: () => void;
}

// The filters in use, each removed on its own, and how many items they leave.
export function FilterChips({ chips, onClearAll, shown, total }: {
    chips: Chip[];
    onClearAll: () => void;
    shown: number;
    total: number;
}) {
    const { t } = useTranslation();
    if (chips.length === 0) return null;
    return (
        <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }} style={{ marginBlockStart: "0.5rem" }}>
            {chips.map(chip => (
                <Label key={chip.key} variant="outline" isCompact onClose={chip.onClear}
                    closeBtnAriaLabel={t("Remove the filter {{filter}}", { filter: chip.text })}>{chip.text}</Label>
            ))}
            <Button variant="link" isInline onClick={onClearAll}>{t("Clear all filters")}</Button>
            <Content component="small">{t("Shown: {{shown}} of {{total}}", { shown, total })}</Content>
        </Flex>
    );
}

// How to group the list, and a button that opens or closes every group.
export function GroupByChoice<G extends string>({ id, value, options, onChange, groups, allOpen, onToggleAll }: {
    id: string;
    value: G;
    // The ways of grouping, with "none" among them for no groups.
    options: [G, string][];
    onChange: (value: G) => void;
    // How many groups are shown.
    groups: number;
    allOpen: boolean;
    onToggleAll: () => void;
}) {
    const { t } = useTranslation();
    return (
        <FlexItem align={{ default: "alignRight" }}>
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                <span className="pf-v6-u-font-size-sm" id={id}>{t("Group by")}</span>
                <ToggleGroup aria-labelledby={id} isCompact>
                    {options.map(([option, label]) => (
                        <ToggleGroupItem key={option} text={label} isSelected={value === option} onChange={() => onChange(option)} />
                    ))}
                </ToggleGroup>
                {value !== "none" && groups > 1 && (
                    <Button variant="link" isInline onClick={onToggleAll}>
                        {allOpen ? t("Close all groups") : t("Open all groups")}
                    </Button>
                )}
            </Flex>
        </FlexItem>
    );
}

// The first row of a group in a table: what the group is, how many items it holds.
export function GroupRow({ title, count, open, onToggle, width, children }: {
    title: string;
    count: string;
    open: boolean;
    onToggle: () => void;
    // The columns of the table, this one's toggle included.
    width: number;
    // A summary of the group, after its name.
    children?: React.ReactNode;
}) {
    return (
        <Tr>
            <ExpandCell isExpanded={open} onToggle={onToggle} label={title} />
            <Td colSpan={width - 1}>
                <Flex gap={{ default: "gapMd" }} alignItems={{ default: "alignItemsCenter" }}>
                    <strong>{title}</strong>
                    <span className="pf-v6-u-font-size-sm pf-v6-u-text-color-subtle">{count}</span>
                    {children}
                </Flex>
            </Td>
        </Tr>
    );
}

export function NothingMatches({ title, onClearAll }: { title: string; onClearAll: () => void }) {
    const { t } = useTranslation();
    return (
        <EmptyState titleText={title} headingLevel="h3">
            <EmptyStateFooter>
                <Button variant="link" onClick={onClearAll}>{t("Clear all filters")}</Button>
            </EmptyStateFooter>
        </EmptyState>
    );
}
