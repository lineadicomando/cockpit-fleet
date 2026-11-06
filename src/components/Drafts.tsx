import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Checkbox,
    Content,
    Flex,
    FlexItem,
    FormGroup,
    Label,
    ModalBody,
    ModalFooter,
    ModalHeader,
    ModalVariant,
    TextInput,
} from "@patternfly/react-core";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import type { DraftChange, DraftDiff, DraftInfo } from "../lib/types.ts";
import { FormModal, RunStatusLabel, message } from "./common.tsx";
import { Diff } from "./RunsPage.tsx";
import { Alert, Modal, Spinner } from "./pf.tsx";

// Where the files of a draft are edited: the page of the files, on the draft.
export function draftPath(draft: Pick<DraftInfo, "id" | "draft">): string[] {
    return [draft.id.startsWith("i") ? "inventories" : "projects", draft.id, "drafts", draft.draft];
}

function ChangeLabel({ change }: { change: DraftChange }) {
    const { t } = useTranslation();
    const text = { added: t("Added"), changed: t("Changed"), deleted: t("Deleted") };
    const color = { added: "green", changed: "blue", deleted: "red" } as const;
    return <Label isCompact color={color[change.status]}>{text[change.status]}</Label>;
}

// What a draft would change in main, with the syntax checks of it: taken
// whole or in part, or thrown away. The backend refuses a draft that changed
// since it was shown, and a file main changed too.
export function DraftReview({ id, draft, onDone, onClose }: {
    id: string;
    draft: string;
    onDone: () => void;
    onClose: () => void;
}) {
    const { t, i18n } = useTranslation();
    const diff = useLoad(() => call<DraftDiff>("draft.diff", { id, draft }));
    const [chosen, setChosen] = useState<Set<string>>(new Set());
    const [note, setNote] = useState("");
    const [problem, setProblem] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [discarding, setDiscarding] = useState(false);

    // Everything that can be taken starts chosen; a conflict cannot be.
    useEffect(() => {
        if (diff.data) setChosen(new Set(diff.data.changes.filter(change => !change.conflict).map(change => change.path)));
    }, [diff.data]);

    async function merge(data: DraftDiff) {
        setBusy(true);
        setProblem(null);
        const all = chosen.size === data.changes.length;
        try {
            await call("draft.merge", {
                id, draft, revision: data.revision, paths: all ? [] : [...chosen], ...(note.trim() ? { message: note.trim() } : {}),
            });
            onDone();
            onClose();
        } catch (ex) {
            setProblem(message(ex));
            setBusy(false);
            diff.reload();
        }
    }

    if (discarding) {
        return (
            <FormModal title={t("Discard this draft?")} submitLabel={t("Discard")} isDanger variant={ModalVariant.small}
                onSubmit={async () => { await call("draft.discard", { id, draft }); onDone(); onClose(); }}
                onClose={() => setDiscarding(false)}>
                <Content component="p">{t("What the draft holds and main does not is lost.")}</Content>
            </FormModal>
        );
    }

    const data = diff.data;
    const conflicts = data?.changes.filter(change => change.conflict).length ?? 0;
    return (
        <Modal variant={ModalVariant.large} isOpen onClose={onClose}>
            <ModalHeader title={t("Review a draft")}
                description={data ? t("Written by {{author}}, last on {{time}}. No run uses it until it is merged.", { author: data.author, time: formatTime(data.time, i18n.language) }) : undefined} />
            <ModalBody>
                {problem && <Alert variant="danger" isInline title={problem} style={{ marginBlockEnd: "1rem" }} />}
                {diff.error && <Alert variant="danger" isInline title={diff.error.message} />}
                {!data && !diff.error && <Spinner aria-label={t("Loading")} />}
                {data && (
                    <>
                        {conflicts > 0 && (
                            <Alert variant="warning" isInline style={{ marginBlockEnd: "1rem" }}
                                title={t("Files changed in main too since the draft started cannot be merged: edit the draft or main so that they agree.")} />
                        )}
                        {data.changes.length === 0 && <Content component="p">{t("Main already has everything in this draft.")}</Content>}
                        {data.changes.length > 0 && (
                            <FormGroup label={t("Changes to merge")} role="group" fieldId="draft-changes">
                                {data.changes.map(change => (
                                    <Checkbox key={change.path} id={`draft-change-${change.path}`}
                                        isChecked={chosen.has(change.path)} isDisabled={change.conflict}
                                        onChange={(_event, checked) => setChosen(previous => {
                                            const next = new Set(previous);
                                            if (checked) next.add(change.path);
                                            else next.delete(change.path);
                                            return next;
                                        })}
                                        label={(
                                            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                                                <FlexItem><ChangeLabel change={change} /></FlexItem>
                                                <FlexItem><code>{change.directory ? `${change.path}/` : change.path}</code></FlexItem>
                                                {change.conflict && <FlexItem><Label isCompact color="orange">{t("Changed in main too")}</Label></FlexItem>}
                                            </Flex>
                                        )} />
                                ))}
                            </FormGroup>
                        )}
                        {data.checks.length > 0 && (
                            <>
                                <Content component="h4" style={{ marginBlockStart: "1rem" }}>{t("Syntax checks of the draft")}</Content>
                                <Content component="ul">
                                    {data.checks.map(check => (
                                        <Content component="li" key={check.run_id}>
                                            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                                                <FlexItem><code>{check.path}</code></FlexItem>
                                                <FlexItem><RunStatusLabel status={check.status} /></FlexItem>
                                                {check.lint === "findings" && <FlexItem><Label isCompact color="orange">{t("ansible-lint has remarks")}</Label></FlexItem>}
                                                {!check.current && <FlexItem><Label isCompact color="grey">{t("Of an earlier version")}</Label></FlexItem>}
                                                <FlexItem>
                                                    <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", check.run_id])}>{t("Open the run")}</Button>
                                                </FlexItem>
                                            </Flex>
                                        </Content>
                                    ))}
                                </Content>
                            </>
                        )}
                        {data.text && (
                            <>
                                <Content component="h4" style={{ marginBlockStart: "1rem" }}>{t("What changes")}</Content>
                                <Diff text={data.text} />
                                {data.cut && <Content component="small">{t("This is only the beginning of the change.")}</Content>}
                            </>
                        )}
                        {data.changes.length > 0 && (
                            <FormGroup label={t("What changed")} fieldId="draft-message" style={{ marginBlockStart: "1rem" }}>
                                <TextInput id="draft-message" value={note} onChange={(_event, value) => setNote(value)} maxLength={200}
                                    placeholder={t("Optional: by default it says the draft was merged")} />
                            </FormGroup>
                        )}
                    </>
                )}
            </ModalBody>
            <ModalFooter>
                <Button variant="primary" isLoading={busy} isDisabled={!data || busy || chosen.size === 0} onClick={() => data && merge(data)}>
                    {data && chosen.size < data.changes.length ? t("Merge the chosen files") : t("Merge into main")}
                </Button>
                <Button variant="secondary" isDisabled={busy} onClick={() => cockpit.location.go(draftPath({ id, draft }))}>{t("Edit the draft")}</Button>
                <Button variant="secondary" isDanger isDisabled={busy} onClick={() => setDiscarding(true)}>{t("Discard")}</Button>
                <Button variant="link" onClick={onClose}>{t("Close")}</Button>
            </ModalFooter>
        </Modal>
    );
}

// The drafts waiting for a person: of one project or inventory, or of all.
// Shown only when there are some; asked for again when stamp changes.
export function PendingDrafts({ id, stamp, onChanged }: { id?: string; stamp?: unknown; onChanged?: () => void }) {
    const { t, i18n } = useTranslation();
    const drafts = useLoad(() => call<{ drafts: DraftInfo[] }>("draft.list", id ? { id } : {}), [stamp]);
    const [open, setOpen] = useState<DraftInfo | null>(null);
    const list = drafts.data?.drafts ?? [];
    if (list.length === 0) return null;
    return (
        <>
            <Alert variant="info" isInline style={{ marginBlockEnd: "1rem" }}
                title={t("drafts_waiting", { count: list.length })}>
                <Content component="p">{t("Files the assistant wrote go to a draft, which no run uses until a person merges it.")}</Content>
                <Content component="ul">
                    {list.map(draft => (
                        <Content component="li" key={`${draft.id}-${draft.draft}`}>
                            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                                {!id && <FlexItem><strong>{draft.name}</strong></FlexItem>}
                                <FlexItem>
                                    {t("{{author}}, {{time}}: {{changes}}", {
                                        author: draft.author, time: formatTime(draft.time, i18n.language), changes: t("files_count", { count: draft.changes }),
                                    })}
                                </FlexItem>
                                {draft.conflicts > 0 && <FlexItem><Label isCompact color="orange">{t("conflicts_count", { count: draft.conflicts })}</Label></FlexItem>}
                                <FlexItem><Button variant="link" isInline onClick={() => setOpen(draft)}>{t("Review")}</Button></FlexItem>
                                <FlexItem><Button variant="link" isInline onClick={() => cockpit.location.go(draftPath(draft))}>{t("Edit")}</Button></FlexItem>
                            </Flex>
                        </Content>
                    ))}
                </Content>
            </Alert>
            {open && (
                <DraftReview id={open.id} draft={open.draft} onClose={() => setOpen(null)}
                    onDone={() => { drafts.reload(); onChanged?.(); }} />
            )}
        </>
    );
}
