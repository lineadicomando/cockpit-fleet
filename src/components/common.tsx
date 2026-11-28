import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import cockpit from "cockpit";
import {
    AlertActionLink,
    AlertGroup,
    Content,
    EmptyState,
    EmptyStateBody,
    ExpandableSection,
    Flex,
    FlexItem,
    Form,
    FormGroup,
    ModalBody,
    ModalFooter,
    ModalHeader,
    ModalVariant,
    TextInput,
    Title,
} from "@patternfly/react-core";
import { AngleLeftIcon, ExternalLinkAltIcon } from "@patternfly/react-icons";
import type { Loaded } from "../lib/hooks.ts";
import { detailLocation, runLocation } from "../lib/sections.ts";
import { shellPage } from "../lib/shell.ts";
import type { RunStatus } from "../lib/types.ts";
import { StatusLabel, type Tone } from "./StatusLabel.tsx";
import { Label } from "@patternfly/react-core";
import { Alert, AlertActionCloseButton, Button, ClipboardCopy, Modal, Spinner } from "./pf.tsx";

export function message(ex: unknown): string {
    return ex instanceof Error ? ex.message : String(ex);
}

// A run not over yet: what shows it is polled until it is.
export function isActiveRun(status: RunStatus | ""): boolean {
    return status === "pending_approval" || status === "queued" || status === "running";
}

// Opens a run's page, which then offers to go back to the page it came from.
export function openRun(id: string) {
    const { path, options } = runLocation(id, cockpit.location.path);
    cockpit.location.go(path, options);
}

// Opens a host's page, which then offers to go back to the page it came from.
export function openHost(id: string) {
    const { path, options } = detailLocation("hosts", id, cockpit.location.path);
    cockpit.location.go(path, options);
}

export interface NavLink { label: string; onClick: () => void }

// The ways out of a detail page, as links above its title: apart from the
// page's actions, so that a click meant to leave cannot act on what it shows.
// The first one goes back, and has the arrow.
export function PageNav({ links }: { links: (NavLink | null | false | undefined)[] }) {
    const { t } = useTranslation();
    const shown = links.filter((link): link is NavLink => !!link);
    if (shown.length === 0) return null;
    return (
        <Flex component="nav" aria-label={t("Page navigation")} gap={{ default: "gapMd" }} style={{ marginBlockEnd: "0.5rem" }}>
            {shown.map((link, index) => (
                <Button key={link.label} variant="link" isInline icon={index === 0 ? <AngleLeftIcon /> : undefined} onClick={link.onClick}>
                    {link.label}
                </Button>
            ))}
        </Flex>
    );
}

// A run that was just started is announced, not opened: the page stays where
// it is, and the notice links to the run.
// One waiting for an approval is said to wait: it has not started.
interface StartedRun { key: number; id: string; title: string; waiting: boolean }
let started: StartedRun[] = [];
let nextKey = 0;
const listeners = new Set<(runs: StartedRun[]) => void>();

function setStarted(runs: StartedRun[]) {
    started = runs;
    for (const listener of listeners) listener(runs);
}

export function announceRun(id: string, title = "", status?: RunStatus) {
    setStarted([...started, { key: nextKey++, id, title, waiting: status === "pending_approval" }]);
}

export function StartedRuns() {
    const { t } = useTranslation();
    const [runs, setRuns] = useState(started);
    useEffect(() => {
        listeners.add(setRuns);
        return () => { listeners.delete(setRuns); };
    }, []);
    const dismiss = (key: number) => setStarted(started.filter(run => run.key !== key));

    return (
        <AlertGroup isToast isLiveRegion>
            {runs.map(run => (
                <Alert key={run.key} variant="info" timeout={8000} onTimeout={() => dismiss(run.key)}
                    title={run.waiting
                        ? (run.title ? t("Waiting for an approval: {{title}}", { title: run.title }) : t("The run waits for an approval"))
                        : (run.title ? t("Started: {{title}}", { title: run.title }) : t("The run was started"))}
                    actionClose={<AlertActionCloseButton onClose={() => dismiss(run.key)} />}
                    actionLinks={<AlertActionLink onClick={() => { dismiss(run.key); openRun(run.id); }}>{t("Open the run")}</AlertActionLink>} />
            ))}
        </AlertGroup>
    );
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
    const retry = <AlertActionLink onClick={loaded.reload}>{t("Try again")}</AlertActionLink>;
    if (loaded.error && !loaded.data) return <Alert variant="danger" isInline title={loaded.error.message} actionLinks={retry} />;
    if (!loaded.data) return <Spinner aria-label={t("Loading")} />;
    // A refresh that failed leaves what was loaded in sight, said to be maybe out of date.
    const stale = loaded.error && (
        <Alert variant="warning" isInline title={t("Could not refresh: what is shown may be out of date.")} actionLinks={retry}
            style={{ marginBottom: "1rem" }}>
            {loaded.error.message}
        </Alert>
    );
    if (isEmpty(loaded.data)) {
        return (
            <>
                {stale}
                <EmptyState titleText={emptyTitle} headingLevel="h3">
                    <EmptyStateBody>{emptyBody}</EmptyStateBody>
                </EmptyState>
            </>
        );
    }
    return <>{stale}{children(loaded.data)}</>;
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
    // In place of "Cancel", when the action itself is a cancellation.
    cancelLabel?: string;
    // A long form: once something was typed in it, closing it without
    // saving asks first (Escape, the close button, Cancel).
    guardClose?: boolean;
}

// A dialog that runs one action: keeps the button busy, shows the error and
// stays open when the action fails.
export function FormModal({ title, submitLabel, onSubmit, onClose, children, isDanger, isDisabled, variant, cancelLabel, guardClose }: FormModalProps) {
    const { t } = useTranslation();
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);
    // Typed into: an input event reaches the form from any field in it,
    // the code editor's included (menus drawn outside it change nothing typed).
    const [touched, setTouched] = useState(false);
    const [closing, setClosing] = useState(false);
    const errorRef = useRef<HTMLDivElement>(null);
    const close = () => (guardClose && touched && !submitting ? setClosing(true) : onClose());

    // The error is shown at the top: a form scrolled down to its end is brought back to it.
    useEffect(() => { if (error) errorRef.current?.scrollIntoView?.({ block: "nearest" }); }, [error]);

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
        <Modal variant={variant ?? ModalVariant.medium} isOpen onClose={close}>
            <ModalHeader title={title} />
            <ModalBody>
                {error && <div ref={errorRef} role="alert"><Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} /></div>}
                <Form onSubmit={event => { event.preventDefault(); if (!isDisabled && !submitting) submit(); }}
                    onInput={guardClose ? () => setTouched(true) : undefined}>
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
                <Button variant="link" onClick={close}>{cancelLabel ?? t("Cancel")}</Button>
            </ModalFooter>
            {closing && (
                <Modal variant={ModalVariant.small} isOpen onClose={() => setClosing(false)}>
                    <ModalHeader title={t("Discard the changes?")} />
                    <ModalBody>{t("What was written in this form is lost.")}</ModalBody>
                    <ModalFooter>
                        <Button variant="danger" onClick={() => { setClosing(false); onClose(); }}>{t("Discard")}</Button>
                        <Button variant="link" onClick={() => setClosing(false)}>{t("Keep editing")}</Button>
                    </ModalFooter>
                </Modal>
            )}
        </Modal>
    );
}

export function ConfirmDelete({ what, name, onDelete, onClose, isDisabled }: {
    what: string;
    name: string;
    onDelete: () => Promise<void>;
    onClose: () => void;
    isDisabled?: boolean;
}) {
    const { t } = useTranslation();
    return (
        <FormModal
            title={t("Delete {{name}}?", { name })}
            submitLabel={t("Delete")}
            onSubmit={onDelete}
            onClose={onClose}
            isDanger
            isDisabled={isDisabled}
            variant={ModalVariant.small}
        >
            <Content component="p">{what}</Content>
        </FormModal>
    );
}

// Asks the name of a copy of a project or an inventory written here, and makes it.
export function DuplicateModal({ name, what, onDuplicate, onClose }: {
    name: string;
    what: string;
    onDuplicate: (name: string) => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [copy, setCopy] = useState(t("{{name}} (copy)", { name }));
    return (
        <FormModal title={t("Duplicate {{name}}", { name })} submitLabel={t("Duplicate")}
            onSubmit={() => onDuplicate(copy.trim())} onClose={onClose} isDisabled={!copy.trim()} variant={ModalVariant.small}>
            <Content component="p">{what}</Content>
            <FormGroup label={t("Name")} isRequired fieldId="duplicate-name">
                <TextInput id="duplicate-name" value={copy} onChange={(_e, value) => setCopy(value)} />
            </FormGroup>
        </FormModal>
    );
}

const RUN_TONE: Record<RunStatus, Tone | null> = {
    pending_approval: "warning", queued: null, running: null, successful: "good", failed: "bad", canceled: "warning", error: "bad",
    rejected: "warning", expired: "warning",
};

// Shown beside the name of a project or an inventory kept as a starter.
export function StarterLabel() {
    const { t } = useTranslation();
    return <Label color="purple" isCompact style={{ marginInlineStart: "0.5rem" }}>{t("Starter")}</Label>;
}

// Keeps a project or an inventory as a starter, or makes it usable again.
export function StarterModal({ name, starter, what, onConfirm, onClose }: {
    name: string;
    // What it becomes.
    starter: boolean;
    what: string;
    onConfirm: () => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    return (
        <FormModal title={starter ? t("Use {{name}} as a starter?", { name }) : t("Use {{name}} in runs again?", { name })}
            submitLabel={starter ? t("Use as starter") : t("Use in runs again")}
            onSubmit={onConfirm} onClose={onClose} variant={ModalVariant.small}>
            <Content component="p">{what}</Content>
        </FormModal>
    );
}

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
// agent the chat has started; in this one (`here`) where nothing would be
// lost, as while the chat is being set up, so that no tab is left behind.
export function TerminalCommand({ text, here = false }: { text: string; here?: boolean }) {
    const { t } = useTranslation();
    const url = here ? "" : terminalUrl();
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
