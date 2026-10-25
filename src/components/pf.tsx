// PatternFly components with the English text they fall back on replaced by
// translated text: labels that only a screen reader says (an alert's kind, a
// close button, a spinner's value), tooltips, and the buttons of an upload.
// Pages import these instead of the originals, so nothing is left in English.
import React, { Children, cloneElement, isValidElement } from "react";
import { useTranslation } from "react-i18next";
import {
    Alert as PfAlert,
    AlertActionCloseButton as PfAlertActionCloseButton,
    Button,
    ClipboardCopy as PfClipboardCopy,
    FileUpload as PfFileUpload,
    MenuToggle,
    Modal as PfModal,
    NumberInput as PfNumberInput,
    SearchInput as PfSearchInput,
    Spinner as PfSpinner,
    Tabs as PfTabs,
} from "@patternfly/react-core";
import type {
    AlertActionCloseButtonProps,
    AlertProps,
    ClipboardCopyProps,
    FileUploadProps,
    ModalProps,
    NumberInputProps,
    SearchInputProps,
    SpinnerProps,
    TabProps,
    TabsProps,
} from "@patternfly/react-core";
import { EllipsisVIcon, TimesIcon } from "@patternfly/react-icons";
import { ActionsColumn as PfActionsColumn, CollapseColumn, Td } from "@patternfly/react-table";
import type { ActionsColumnProps } from "@patternfly/react-table";

function useVariantLabel(variant: AlertProps["variant"]): string {
    const { t } = useTranslation();
    switch (variant) {
        case "danger": return t("Error:");
        case "warning": return t("Warning:");
        case "success": return t("Success:");
        case "info": return t("Information:");
        default: return t("Notice:");
    }
}

export function Alert(props: AlertProps) {
    const { t } = useTranslation();
    const variantLabel = useVariantLabel(props.variant);
    // The title is a paragraph, not a heading: an alert sits at any depth of
    // the page, and a heading of a fixed level would break their order.
    return <PfAlert variantLabel={variantLabel} toggleAriaLabel={t("Details")} component="p" {...props} />;
}

export function AlertActionCloseButton(props: AlertActionCloseButtonProps) {
    const { t } = useTranslation();
    return <PfAlertActionCloseButton aria-label={t("Close")} {...props} />;
}

export function ClipboardCopy(props: Omit<ClipboardCopyProps, "ref">) {
    const { t } = useTranslation();
    const label = props["aria-label"] ?? t("Text to copy");
    return (
        <PfClipboardCopy hoverTip={t("Copy")} clickTip={t("Copied")} textAriaLabel={label}
            toggleAriaLabel={t("Show all")} {...props} />
    );
}

export function FileUpload(props: FileUploadProps) {
    const { t } = useTranslation();
    return (
        <PfFileUpload aria-label={t("Content of the file")} filenameAriaLabel={t("File name")}
            filenamePlaceholder={t("Choose a file")} browseButtonText={t("Browse…")}
            clearButtonText={t("Clear")} {...props} />
    );
}

// The close button is drawn here rather than by PatternFly, which gives it no
// label of ours; Escape still closes.
export function Modal({ onClose, children, ...props }: Omit<ModalProps, "ref">) {
    const { t } = useTranslation();
    return (
        <PfModal {...props} onEscapePress={props.onEscapePress ?? (onClose ? () => onClose(new KeyboardEvent("keydown")) : undefined)}>
            {onClose &&
                <div className="pf-v6-c-modal-box__close">
                    <Button variant="plain" aria-label={t("Close")} icon={<TimesIcon />} onClick={event => onClose(event)} />
                </div>}
            {children}
        </PfModal>
    );
}

// PatternFly puts the id on the box around the field, where the label of a
// FormGroup does not reach the field, and names the field "Input" over that
// label: the id goes to the field, and with it no name of its own.
export function NumberInput({ id, inputProps, ...props }: NumberInputProps) {
    const { t } = useTranslation();
    return (
        <PfNumberInput inputAriaLabel={id ? "" : props["aria-label"] ?? t("Number")} minusBtnAriaLabel={t("Less")}
            plusBtnAriaLabel={t("More")} inputProps={{ ...inputProps, id }} {...props} />
    );
}

export function SearchInput(props: SearchInputProps) {
    const { t } = useTranslation();
    return (
        <PfSearchInput aria-label={t("Search")} resetButtonLabel={t("Clear the search")}
            submitSearchButtonLabel={t("Search")} {...props} />
    );
}

export function Spinner(props: SpinnerProps) {
    const { t } = useTranslation();
    return <PfSpinner aria-valuetext={t("Loading")} {...props} />;
}

// Tabs whose content is drawn apart, in a TabPanel of the same panelId: each
// tab then names that panel, which PatternFly's own panels would otherwise
// be, and they are never drawn here.
export function Tabs({ panelId, children, ...props }: Omit<TabsProps, "ref"> & { panelId: string }) {
    const { t } = useTranslation();
    const tabs = Children.map(children, child => isValidElement<TabProps>(child)
        ? cloneElement(child, { id: panelId, tabContentId: panelId })
        : child) as TabsProps["children"];
    return (
        <PfTabs backScrollAriaLabel={t("Previous tabs")} forwardScrollAriaLabel={t("Next tabs")} {...props}>
            {tabs}
        </PfTabs>
    );
}

export function TabPanel({ panelId, active, children }: {
    panelId: string;
    active: string | number;
    children: React.ReactNode;
}) {
    return <div id={panelId} role="tabpanel" aria-labelledby={`pf-tab-${active}-${panelId}`}>{children}</div>;
}

// The menu of a row of a table.
export function ActionsColumn(props: ActionsColumnProps) {
    const { t } = useTranslation();
    return (
        <PfActionsColumn
            actionsToggle={({ onToggle, isOpen, isDisabled, toggleRef }) => (
                <MenuToggle ref={toggleRef} aria-label={t("Actions")} variant="plain" onClick={onToggle}
                    isExpanded={isOpen} isDisabled={isDisabled} icon={<EllipsisVIcon />} />
            )}
            {...props}
        />
    );
}

// The cell that opens the details of a row; Td's own expand gives it no label of ours.
export function ExpandCell({ isExpanded, onToggle, label }: {
    isExpanded: boolean;
    onToggle: () => void;
    label?: string;
}) {
    const { t } = useTranslation();
    return (
        <Td className="pf-v6-c-table__toggle">
            <CollapseColumn isOpen={isExpanded} onToggle={onToggle} aria-label={label ?? t("Details")} />
        </Td>
    );
}
