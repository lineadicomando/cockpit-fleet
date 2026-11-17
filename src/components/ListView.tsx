// The pieces of a long list that the pages share: the filters in use, the
// choice of grouping, groups that open and close, the view kept in the browser.

import React from "react";
import { useTranslation } from "react-i18next";
import { Button, Content, EmptyState, EmptyStateFooter, Flex, FlexItem, Label, ToggleGroup, ToggleGroupItem } from "@patternfly/react-core";
import { Tbody, Td, Tr } from "@patternfly/react-table";
import { useStoredState } from "../lib/hooks.ts";
import type { HostListView } from "../lib/hosts.ts";
import type { Group } from "../lib/listview.ts";
import { useSession } from "../lib/session.tsx";
import { ExpandCell, SearchInput } from "./pf.tsx";

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

// The filter of a list of hosts, and its grouping by inventory when there is
// more than one to group by.
export function HostListToolbar<V extends HostListView>({ view, onChange, placeholder, canGroup, groups, allOpen, onToggleAll, shown, total }: {
    view: V;
    onChange: (view: V) => void;
    placeholder: string;
    canGroup: boolean;
    groups: Group<unknown>[];
    allOpen: boolean;
    onToggleAll: () => void;
    shown: number;
    total: number;
}) {
    const { t } = useTranslation();
    const search = view.search.trim();
    const chips = search ? [{ key: "search", text: t("Text: {{text}}", { text: search }), onClear: () => onChange({ ...view, search: "" }) }] : [];
    return (
        <div style={{ marginBlockEnd: "1rem" }}>
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                <FlexItem style={{ width: "20rem" }}>
                    <SearchInput aria-label={t("Filter hosts")} placeholder={placeholder} value={view.search}
                        onChange={(_e, value) => onChange({ ...view, search: value })} onClear={() => onChange({ ...view, search: "" })} />
                </FlexItem>
                {canGroup && (
                    <GroupByChoice id="host-list-group-by" value={view.groupBy} onChange={groupBy => onChange({ ...view, groupBy })}
                        options={[["inventory", t("Inventory")], ["none", t("Nothing")]]}
                        groups={groups.length} allOpen={allOpen} onToggleAll={onToggleAll} />
                )}
            </Flex>
            <FilterChips chips={chips} onClearAll={() => onChange({ ...view, search: "" })} shown={shown} total={total} />
        </div>
    );
}

// The bodies of a table whose rows may be in groups: one body per group, led by
// its GroupRow, or a single body. A row of a group starts with an empty cell,
// under the group's toggle.
export function GroupedRows<T>({ groups, grouped, width, isOpen, onToggle, title, count, row }: {
    groups: Group<T>[];
    grouped: boolean;
    // The columns of the table, the toggle's included when grouped.
    width: number;
    isOpen: (group: Group<T>) => boolean;
    onToggle: (group: Group<T>) => void;
    title: (group: Group<T>) => string;
    count: (group: Group<T>) => string;
    row: (item: T, open: boolean) => React.ReactNode;
}) {
    if (!grouped) return <Tbody>{groups.flatMap(group => group.items).map(item => row(item, true))}</Tbody>;
    return (
        <>
            {groups.map(group => {
                const open = isOpen(group);
                return (
                    <Tbody key={group.key} isExpanded={open}>
                        <GroupRow title={title(group)} count={count(group)} open={open} onToggle={() => onToggle(group)} width={width} />
                        {group.items.map(item => row(item, open))}
                    </Tbody>
                );
            })}
        </>
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
