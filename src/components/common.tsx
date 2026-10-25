import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    Button,
    Content,
    EmptyState,
    EmptyStateBody,
    ExpandableSection,
    Flex,
    FlexItem,
    Form,
    ModalBody,
    ModalFooter,
    ModalHeader,
    ModalVariant,
    Title,
} from "@patternfly/react-core";
import { ExternalLinkAltIcon } from "@patternfly/react-icons";
import type { Loaded } from "../lib/hooks.ts";
import { shellPage } from "../lib/shell.ts";
import type { RunStatus } from "../lib/types.ts";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";
import { Label } from "@patternfly/react-core";
import { Alert, ClipboardCopy, Modal, Spinner } from "./pf.tsx";

export function message(ex: unknown): string {
    return ex instanceof Error ? ex.message : String(ex);
}

export function SectionHeader({ title, children }: { title: string; children?: React.ReactNode }) {
    return (
        <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} style={{ marginBlockEnd: "1rem" }}>
            <FlexItem>
                <Title headingLevel="h2" size="lg">{title}</Title>
            </FlexItem>
            <Flex gap={{ default: "gapSm" }}>{children}</Flex>
        </Flex>
    );
}

// Spinner, error or empty state for a list; renders children once there is data.
export function ListState<T>({ loaded, isEmpty, emptyTitle, emptyBody, children }: {
    loaded: Loaded<T>;
    isEmpty: (data: T) => boolean;
    emptyTitle: string;
    emptyBody: string;
    children: (data: T) => React.ReactNode;
}) {
    const { t } = useTranslation();
    if (loaded.error) return <Alert variant="danger" isInline title={loaded.error.message} />;
    if (!loaded.data) return <Spinner aria-label={t("Loading")} />;
    if (isEmpty(loaded.data)) {
        return (
            <EmptyState titleText={emptyTitle} headingLevel="h3">
                <EmptyStateBody>{emptyBody}</EmptyStateBody>
            </EmptyState>
        );
    }
    return <>{children(loaded.data)}</>;
}

interface FormModalProps {
    title: string;
    submitLabel: string;
    onSubmit: () => Promise<void>;
    onClose: () => void;
    children: React.ReactNode;
    isDanger?: boolean;
    isDisabled?: boolean;
    variant?: ModalVariant;
}

// A dialog that runs one action: keeps the button busy, shows the error and
// stays open when the action fails.
export function FormModal({ title, submitLabel, onSubmit, onClose, children, isDanger, isDisabled, variant }: FormModalProps) {
    const { t } = useTranslation();
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);

    async function submit() {
        setSubmitting(true);
        setError(null);
        try {
            await onSubmit();
        } catch (ex) {
            setError(message(ex));
            setSubmitting(false);
        }
    }

    return (
        <Modal variant={variant ?? ModalVariant.medium} isOpen onClose={onClose}>
            <ModalHeader title={title} />
            <ModalBody>
                {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
                <Form onSubmit={event => { event.preventDefault(); if (!isDisabled && !submitting) submit(); }}>
                    {children}
                </Form>
            </ModalBody>
            <ModalFooter>
                <Button
                    variant={isDanger ? "danger" : "primary"}
                    isLoading={submitting}
                    isDisabled={isDisabled || submitting}
                    onClick={submit}
                >
                    {submitLabel}
                </Button>
                <Button variant="link" onClick={onClose}>{t("Cancel")}</Button>
            </ModalFooter>
        </Modal>
    );
}

export function ConfirmDelete({ what, name, onDelete, onClose }: {
    what: string;
    name: string;
    onDelete: () => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    return (
        <FormModal
            title={t("Delete {{name}}?", { name })}
            submitLabel={t("Delete")}
            onSubmit={onDelete}
            onClose={onClose}
            isDanger
            variant={ModalVariant.small}
        >
            <Content component="p">{what}</Content>
        </FormModal>
    );
}

const RUN_TONE: Record<RunStatus, Tone | null> = {
    pending_approval: "warning", queued: null, running: null, successful: "good", failed: "bad", canceled: "warning", error: "bad",
    rejected: "warning", expired: "warning",
};

export function RunStatusLabel({ status }: { status: RunStatus }) {
    const { t } = useTranslation();
    const text: Record<RunStatus, string> = {
        pending_approval: t("Waiting for approval"),
        queued: t("Queued"),
        running: t("Running"),
        successful: t("Successful"),
        failed: t("Failed"),
        canceled: t("Canceled"),
        error: t("Error"),
        rejected: t("Rejected"),
        expired: t("Expired"),
    };
    const tone = RUN_TONE[status];
    if (tone) return <StatusLabel tone={tone}>{text[status]}</StatusLabel>;
    return (
        <Label color={status === "running" ? "blue" : "grey"} isCompact
            icon={status === "running" ? <Spinner size="sm" aria-hidden /> : undefined}>
            {text[status]}
        </Label>
    );
}

function terminalUrl(): string {
    try {
        return window.top && window.top !== window ? shellPage(window.top.location.pathname, window.location.pathname, "/system/terminal") : "";
    } catch {
        return "";
    }
}

// A command to run in a terminal: it can be copied, and Cockpit's own
// terminal, which runs as the user of this page, is one click away. In a new
// tab: leaving the page would lose what was being done there, such as the
// agent the chat has started.
export function TerminalCommand({ text }: { text: string }) {
    const { t } = useTranslation();
    const url = terminalUrl();
    return (
        <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }} flexWrap={{ default: "nowrap" }}>
            <FlexItem grow={{ default: "grow" }}>
                <ClipboardCopy isReadOnly isCode hoverTip={t("Copy")} clickTip={t("Copied")} aria-label={t("Command")}>{text}</ClipboardCopy>
            </FlexItem>
            {url
                ? <Button variant="secondary" component="a" href={url} target="_blank" rel="noopener noreferrer"
                    icon={<ExternalLinkAltIcon />} iconPosition="end">{t("Open the terminal")}</Button>
                : <Button variant="secondary" onClick={() => cockpit.jump("/system/terminal")}>{t("Open the terminal")}</Button>}
        </Flex>
    );
}

// What the page does, as the fleet command that does it at a terminal.
export function AtTerminal({ text }: { text: string }) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    return (
        <ExpandableSection toggleText={t("At a terminal")} isExpanded={open} onToggle={(_e, value) => setOpen(value)}>
            <TerminalCommand text={text} />
        </ExpandableSection>
    );
}
