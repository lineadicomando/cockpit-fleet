import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button, Content, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, Label, ModalVariant, TextInput,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling } from "../lib/hooks.ts";
import { useCan, useSession } from "../lib/session.tsx";
import type { ApprovalEntry, ApprovalMode } from "../lib/types.ts";
import { FormModal, ListState, SectionHeader } from "./common.tsx";
import { Answered } from "./SurveyEditor.tsx";

type Decision = "approve" | "reject";

const MODES: ApprovalMode[] = ["none", "confirm", "other"];

export const strongestMode = (...modes: ApprovalMode[]): ApprovalMode =>
    modes.reduce((most, mode) => MODES.indexOf(mode) > MODES.indexOf(most) ? mode : most, "none");

// How a template or a step of a workflow waits for an approval. A step waits
// at least as its template does: floor is the template's.
export function ApprovalModeField({ id, value, onChange, floor = "none" }: {
    id: string;
    value: ApprovalMode;
    onChange: (mode: ApprovalMode) => void;
    floor?: ApprovalMode;
}) {
    const { t } = useTranslation();
    const labels: Record<ApprovalMode, string> = {
        none: t("No approval"),
        confirm: t("A confirmation"),
        other: t("Another person's approval"),
    };
    const shown = strongestMode(value, floor);
    const help: Record<ApprovalMode, string> = {
        none: t("Runs start as soon as they are launched."),
        confirm: t("A run waits until who launched it confirms it, or a member of cockpit-fleet-approver or an administrator approves it. A run launched by a schedule waits for another person. An agent never confirms."),
        other: t("A run waits until a member of cockpit-fleet-approver, or an administrator, other than who launched it approves it. An administrator may approve their own unless config.toml says otherwise."),
    };
    return (
        <FormGroup label={t("Before it starts")} fieldId={id}>
            <FormSelect id={id} value={shown} onChange={(_e, mode) => onChange(mode as ApprovalMode)}>
                {MODES.map(mode => <FormSelectOption key={mode} value={mode} label={labels[mode]}
                    isDisabled={MODES.indexOf(mode) < MODES.indexOf(floor)} />)}
            </FormSelect>
            <FormHelperText><HelperText><HelperTextItem>
                {help[shown]}
                {floor !== "none" && " " + t("Its job template asks for this already.")}
            </HelperTextItem></HelperText></FormHelperText>
        </FormGroup>
    );
}

// A label for a template or a step that waits for an approval.
export function ApprovalModeLabel({ mode, color = "purple" }: { mode: ApprovalMode; color?: "purple" | "orange" }) {
    const { t } = useTranslation();
    if (mode === "none") return null;
    return <Label isCompact color={color}>{mode === "confirm" ? t("Confirmation") : t("Approval")}</Label>;
}

// Approve or reject one run, with a comment kept with the decision.
export function DecideModal({ runId, title, decision, onDone, onClose }: {
    runId: string;
    title: string;
    decision: Decision;
    onDone: () => void;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [comment, setComment] = useState("");
    return (
        <FormModal
            title={decision === "approve" ? t("Approve {{name}}?", { name: title }) : t("Reject {{name}}?", { name: title })}
            submitLabel={decision === "approve" ? t("Approve") : t("Reject")}
            isDanger={decision === "reject"}
            variant={ModalVariant.small}
            onSubmit={async () => {
                await call(decision === "approve" ? "approval.approve" : "approval.reject", { id: runId, comment: comment.trim() });
                onDone();
                onClose();
            }}
            onClose={onClose}
        >
            <Content component="p">
                {decision === "approve"
                    ? t("The run starts as it was launched: with the project as it was then, on the hosts and with the answers shown.")
                    : t("The run ends without starting.")}
            </Content>
            <FormGroup label={t("Comment")} fieldId="decide-comment">
                <TextInput id="decide-comment" value={comment} onChange={(_e, value) => setComment(value)} maxLength={500} />
            </FormGroup>
        </FormModal>
    );
}

export function DecideButtons({ runId, title, onDone }: { runId: string; title: string; onDone: () => void }) {
    const { t } = useTranslation();
    const [deciding, setDeciding] = useState<Decision | null>(null);
    return (
        <>
            <Button variant="primary" size="sm" onClick={() => setDeciding("approve")}>{t("Approve")}</Button>{" "}
            <Button variant="secondary" isDanger size="sm" onClick={() => setDeciding("reject")}>{t("Reject")}</Button>
            {deciding && <DecideModal runId={runId} title={title} decision={deciding} onDone={onDone} onClose={() => setDeciding(null)} />}
        </>
    );
}

// The runs that wait for an approval. Above the list of runs it shows only
// when there are some; for an approver without a role it is the whole page.
export function PendingApprovals({ standalone = false }: { standalone?: boolean }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const session = useSession();
    const list = useLoad(() => call<{ approvals: ApprovalEntry[] }>("approval.list", { state: "pending" }));
    usePolling(list.reload, 5000, true);
    const entries = list.data?.approvals ?? [];
    if (!standalone && entries.length === 0) return null;
    const mayOpen = can("run.get");

    const table = (rows: ApprovalEntry[]) => (
        <Table variant="compact" aria-label={t("Runs waiting for approval")}>
            <Thead>
                <Tr><Th>{t("Run")}</Th><Th>{t("Hosts")}</Th><Th>{t("Answers")}</Th><Th>{t("Launched by")}</Th><Th>{t("Expires")}</Th><Th screenReaderText={t("Actions")} /></Tr>
            </Thead>
            <Tbody>
                {rows.map(entry => (
                    <Tr key={entry.id}>
                        <Td dataLabel={t("Run")}>
                            {mayOpen
                                ? <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", entry.id])}>{entry.title}</Button>
                                : entry.title}
                            {entry.options.check && <Label isCompact color="blue" style={{ marginInlineStart: "0.5rem" }}>{t("Dry run")}</Label>}
                            {entry.approval.mode === "confirm" && <Label isCompact color="purple" style={{ marginInlineStart: "0.5rem" }}>{t("Confirmation")}</Label>}
                            {entry.project && <Content component="small" style={{ display: "block" }}>
                                {entry.revision ? t("Project {{name}}, revision {{revision}}", { name: entry.project, revision: entry.revision.slice(0, 10) }) : t("Project {{name}}", { name: entry.project })}
                            </Content>}
                        </Td>
                        <Td dataLabel={t("Hosts")}>
                            {entry.inventory}
                            {entry.options.limit && <> <code>{entry.options.limit}</code></>}
                        </Td>
                        <Td dataLabel={t("Answers")}>
                            {entry.kind === "adhoc"
                                ? <>
                                    <code>{entry.options.module}</code>
                                    {entry.options.become && <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }}>{t("become")}</Label>}
                                    {entry.options.free_form && <code style={{ display: "block" }}>{entry.options.free_form}</code>}
                                    {Object.keys(entry.options.args ?? {}).length > 0 && <code style={{ display: "block" }}>{JSON.stringify(entry.options.args)}</code>}
                                </>
                                : <Answered answers={entry.options.answers ?? {}} secrets={entry.options.secret_answers} />}
                        </Td>
                        <Td dataLabel={t("Launched by")}>
                            {entry.created_by}
                            <Content component="small" style={{ display: "block" }}>{formatTime(entry.approval.requested_at, i18n.language)}</Content>
                        </Td>
                        <Td dataLabel={t("Expires")}>{formatTime(entry.approval.expires_at, i18n.language)}</Td>
                        <Td dataLabel={t("Actions")} isActionCell style={{ whiteSpace: "nowrap" }}>
                            {entry.can_decide
                                ? <DecideButtons runId={entry.id} title={entry.title} onDone={list.reload} />
                                : <Content component="small">{entry.created_by === session.user ? t("Another user must approve it") : t("You cannot approve it")}</Content>}
                        </Td>
                    </Tr>
                ))}
            </Tbody>
        </Table>
    );

    return (
        <div style={{ marginBlockEnd: "2rem" }}>
            <SectionHeader title={t("Waiting for approval")}>
                {standalone && <Button variant="secondary" onClick={list.reload}>{t("Refresh")}</Button>}
            </SectionHeader>
            <ListState loaded={list} isEmpty={data => data.approvals.length === 0}
                emptyTitle={t("Nothing to approve")}
                emptyBody={t("The runs of job templates that need approval wait here until someone approves them.")}>
                {data => table(data.approvals)}
            </ListState>
        </div>
    );
}
