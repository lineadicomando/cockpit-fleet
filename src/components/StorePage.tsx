import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Breadcrumb,
    BreadcrumbItem,
    Button,
    Content,
    Flex,
    FlexItem,
    FormGroup,
    FormHelperText,
    HelperText,
    HelperTextItem,
    Label,
    ModalVariant,
    Progress,
    ProgressMeasureLocation,
    TextInput,
    Tooltip,
} from "@patternfly/react-core";
import { FolderIcon } from "@patternfly/react-icons";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatBytes } from "../lib/checks.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import { describe, downloadFromStore, entriesOf, joinPath, uploadToStore, type PendingUpload, type StoreEntry, type StoreFile, type StoreList, type Upload } from "../lib/store.ts";
import { shellLine } from "../lib/terminal.ts";
import { AtTerminal, ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { ActionsColumn, Alert } from "./pf.tsx";

type UploadState = "waiting" | "sending" | "done" | "failed" | "cancelled";

interface UploadItem {
    key: number;
    file: File;
    path: string;
    replace: boolean;
    sent: number;
    state: UploadState;
    error?: string;
}

function Origin({ file }: { file: StoreFile }) {
    const { t } = useTranslation();
    if (file.origin === "run") {
        return <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", file.source])}>{t("Run {{id}}", { id: file.source })}</Button>;
    }
    if (file.origin === "upload") return <>{t("Uploaded")}</>;
    if (file.origin === "url") return <span title={file.source}>{t("Downloaded")}</span>;
    return <>{t("Put there by hand")}</>;
}

function Digest({ file }: { file: StoreFile }) {
    const { t } = useTranslation();
    if (!file.sha256) return <Label isCompact color="grey">{t("Being computed")}</Label>;
    return (
        <Tooltip content={<span style={{ wordBreak: "break-all" }}>{file.sha256}</span>}>
            <code tabIndex={0}>{file.sha256.slice(0, 12)}</code>
        </Tooltip>
    );
}

function NameModal({ title, label, initial, submitLabel, help, onSubmit, onClose }: {
    title: string;
    label: string;
    initial: string;
    submitLabel: string;
    help: string;
    onSubmit: (value: string) => Promise<void>;
    onClose: () => void;
}) {
    const [value, setValue] = useState(initial);
    return (
        <FormModal title={title} submitLabel={submitLabel} onSubmit={() => onSubmit(value.trim().replace(/^\/+|\/+$/g, ""))}
            onClose={onClose} isDisabled={!value.trim() || value.trim() === initial} variant={ModalVariant.small}>
            <FormGroup label={label} isRequired fieldId="store-name">
                <TextInput id="store-name" value={value} onChange={(_e, text) => setValue(text)} isRequired />
                <FormHelperText><HelperText><HelperTextItem>{help}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

// Files on the controller that playbooks copy to nodes: installers, images,
// what runs fetched. Viewers download them; operators also upload and arrange.
export function StorePage({ directory }: { directory: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<StoreList>("store.list"));
    const pending = useLoad(() => can("store.uploads") ? call<{ uploads: PendingUpload[] }>("store.uploads") : Promise.resolve({ uploads: [] }));
    const [uploads, setUploads] = useState<UploadItem[]>([]);
    const [replacing, setReplacing] = useState<File[] | null>(null);
    const [making, setMaking] = useState(false);
    const [moving, setMoving] = useState<StoreEntry | null>(null);
    const [deleting, setDeleting] = useState<StoreEntry | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [dragging, setDragging] = useState(false);
    const chooser = useRef<HTMLInputElement>(null);
    const current = useRef<Upload | null>(null);
    const nextKey = useRef(1);

    const canUpload = can("store.put");
    const go = (path: string) => cockpit.location.go(["store", ...(path ? path.split("/") : [])]);

    function update(key: number, change: Partial<UploadItem>) {
        setUploads(items => items.map(item => item.key === key ? { ...item, ...change } : item));
    }

    // One upload at a time, in the order they were asked for.
    const sending = uploads.some(item => item.state === "sending");
    const next = uploads.find(item => item.state === "waiting");
    useEffect(() => {
        if (sending || !next) return;
        const item = next;
        update(item.key, { state: "sending" });
        (async () => {
            // What an earlier upload of the same file left is not sent again.
            let offset = 0;
            try {
                const { uploads: earlier } = await call<{ uploads: PendingUpload[] }>("store.uploads");
                const same = earlier.find(upload => upload.path === item.path && upload.size === item.file.size);
                offset = same?.received ?? 0;
            } catch {
                offset = 0;
            }
            const upload = uploadToStore(item.file, item.path, { offset, replace: item.replace, onProgress: sent => update(item.key, { sent }) });
            current.current = upload;
            try {
                await upload.done;
                // An earlier attempt at the same file is done with too.
                setUploads(items => items.filter(other => other.key === item.key || other.path !== item.path || other.state === "waiting" || other.state === "sending")
                    .map(other => other.key === item.key ? { ...other, state: "done", sent: item.file.size } : other));
            } catch (ex) {
                setUploads(items => items.map(other => other.key !== item.key ? other
                    : { ...other, state: other.state === "cancelled" ? "cancelled" : "failed", error: describe(ex) }));
            } finally {
                current.current = null;
                list.reload();
                pending.reload();
            }
        })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sending, next?.key]);

    function queue(files: File[], replace: boolean) {
        const added = files.map(file => ({ key: nextKey.current++, file, path: joinPath(directory, file.name), replace, sent: 0, state: "waiting" as const }));
        setUploads(items => [...items.filter(item => item.state !== "done"), ...added]);
    }

    function choose(files: File[]) {
        if (!files.length) return;
        const present = new Set((list.data?.files ?? []).map(file => file.path));
        const clashing = files.filter(file => present.has(joinPath(directory, file.name)));
        if (clashing.length) {
            setReplacing(files);
            return;
        }
        queue(files, false);
    }

    function cancel(item: UploadItem) {
        if (item.state === "sending") {
            update(item.key, { state: "cancelled" });
            current.current?.cancel();
        } else {
            update(item.key, { state: "cancelled" });
        }
    }

    async function act(action: () => Promise<unknown>) {
        setError(null);
        try {
            await action();
        } catch (ex) {
            setError(message(ex));
        }
        list.reload();
        pending.reload();
    }

    async function download(entry: StoreEntry) {
        await act(() => downloadFromStore(entry.path));
    }

    const parts = directory ? directory.split("/") : [];
    const inside = list.data && directory && !list.data.directories.includes(directory) ? null : list.data;
    const elsewhere = (pending.data?.uploads ?? []).filter(upload => !uploads.some(item => item.path === upload.path && item.state === "sending"));

    return (
        <>
            <SectionHeader title={t("Store")}>
                {canUpload && <Button variant="secondary" onClick={() => setMaking(true)}>{t("New folder")}</Button>}
                {canUpload && <Button variant="primary" onClick={() => chooser.current?.click()}>{t("Upload files")}</Button>}
            </SectionHeader>
            <input ref={chooser} type="file" multiple hidden aria-hidden
                onChange={event => { choose(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
            <Content component="p">
                {t("Files that playbooks copy to nodes. A run finds them in the directory named by the variable fleet_store, and what it writes there is kept here when it ends. Every run can read them: do not keep secrets here.")}
            </Content>
            {list.data && (
                <Content component="p">
                    {list.data.limit
                        ? t("{{used}} used of {{limit}}; {{free}} free on the disk.", { used: formatBytes(list.data.used), limit: formatBytes(list.data.limit), free: formatBytes(list.data.free) })
                        : t("{{used}} used; {{free}} free on the disk.", { used: formatBytes(list.data.used), free: formatBytes(list.data.free) })}
                </Content>
            )}
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {list.data && !list.data.complete && <Alert variant="warning" isInline title={t("The store holds more than can be listed here: only part of it is shown.")} style={{ marginBottom: "1rem" }} />}

            {uploads.length > 0 && (
                <Table variant="compact" aria-label={t("Uploads")} style={{ marginBottom: "1rem" }}>
                    <Thead><Tr><Th>{t("Uploading")}</Th><Th width={40}>{t("Progress")}</Th><Th screenReaderText={t("Actions")} /></Tr></Thead>
                    <Tbody>
                        {uploads.map(item => (
                            <Tr key={item.key}>
                                <Td dataLabel={t("Uploading")}>{item.path}</Td>
                                <Td dataLabel={t("Progress")}>
                                    {item.state === "failed" || item.state === "cancelled"
                                        ? <span>{item.state === "cancelled" ? t("Upload canceled") : item.error}</span>
                                        : <Progress value={item.file.size ? item.sent * 100 / item.file.size : 100} size="sm"
                                            measureLocation={ProgressMeasureLocation.outside}
                                            aria-label={t("Progress of {{name}}", { name: item.path })}
                                            label={item.state === "waiting" ? t("Waiting") : `${formatBytes(item.sent)} / ${formatBytes(item.file.size)}`}
                                            variant={item.state === "done" ? "success" : undefined} />}
                                </Td>
                                <Td isActionCell>
                                    {(item.state === "waiting" || item.state === "sending") && <Button variant="link" onClick={() => cancel(item)}>{t("Cancel")}</Button>}
                                    {(item.state === "failed" || item.state === "cancelled") && (
                                        <Button variant="link" onClick={() => update(item.key, { state: "waiting", error: undefined })}>{t("Resume")}</Button>
                                    )}
                                </Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
            {elsewhere.length > 0 && canUpload && (
                <Alert variant="info" isInline title={t("Uploads that stopped before their end")} style={{ marginBottom: "1rem" }}>
                    <Content component="p">{t("Upload the same file to the same folder again: it goes on from where it stopped.")}</Content>
                    {elsewhere.map(upload => (
                        <Flex key={upload.path} alignItems={{ default: "alignItemsCenter" }}>
                            <FlexItem>{t("{{path}}: {{received}} of {{size}}", { path: upload.path, received: formatBytes(upload.received), size: formatBytes(upload.size) })}</FlexItem>
                            <FlexItem><Button variant="link" isInline onClick={() => act(() => call("store.discard", { path: upload.path }))}>{t("Discard")}</Button></FlexItem>
                        </Flex>
                    ))}
                </Alert>
            )}

            <Breadcrumb aria-label={t("Folders")} style={{ marginBottom: "0.5rem" }}>
                <BreadcrumbItem>
                    {parts.length ? <Button variant="link" isInline onClick={() => go("")}>{t("Store")}</Button> : t("Store")}
                </BreadcrumbItem>
                {parts.map((part, index) => (
                    <BreadcrumbItem key={index} isActive={index === parts.length - 1}>
                        {index === parts.length - 1 ? part
                            : <Button variant="link" isInline onClick={() => go(parts.slice(0, index + 1).join("/"))}>{part}</Button>}
                    </BreadcrumbItem>
                ))}
            </Breadcrumb>
            <div className={dragging ? "fleet-drop-target" : undefined}
                style={dragging ? { outline: "2px dashed var(--pf-t--global--border--color--clicked)" } : undefined}
                onDragOver={event => { if (canUpload && event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }}
                onDragLeave={() => setDragging(false)}
                onDrop={event => { if (!canUpload) return; event.preventDefault(); setDragging(false); choose(Array.from(event.dataTransfer.files)); }}>
                {list.data && directory && !inside
                    ? <Alert variant="warning" isInline title={t("There is no folder {{path}} in the store.", { path: directory })} />
                    : (
                        <ListState loaded={list} isEmpty={data => entriesOf(data, directory).length === 0}
                            emptyTitle={directory ? t("This folder is empty") : t("The store is empty")}
                            emptyBody={canUpload ? t("Upload files, or drop them here. A run can add files too, through fleet_store.") : t("Files are put here by operators, and by runs.")}>
                            {data => (
                                <Table variant="compact" aria-label={t("Files of the store")}>
                                    <Thead>
                                        <Tr><Th>{t("Name")}</Th><Th>{t("Size")}</Th><Th>{t("SHA-256")}</Th><Th>{t("Origin")}</Th><Th>{t("By")}</Th><Th>{t("Updated")}</Th><Th screenReaderText={t("Actions")} /></Tr>
                                    </Thead>
                                    <Tbody>
                                        {entriesOf(data, directory).map(entry => {
                                            const items = [];
                                            if (entry.file) items.push({ title: t("Download"), onClick: () => download(entry) });
                                            if (can("store.move")) items.push({ title: t("Rename or move"), onClick: () => setMoving(entry) });
                                            if (can("store.delete")) items.push({ title: t("Delete"), onClick: () => setDeleting(entry) });
                                            const file = entry.file;
                                            return (
                                                <Tr key={entry.path}>
                                                    <Td dataLabel={t("Name")}>
                                                        {file
                                                            ? <Button variant="link" isInline onClick={() => download(entry)}>{entry.name}</Button>
                                                            : <Button variant="link" isInline icon={<FolderIcon />} onClick={() => go(entry.path)}>{entry.name}</Button>}
                                                        {file && !file.usable && (
                                                            <Tooltip content={t("Not given to runs: the file is not the service account's own. Put files here as cockpit-fleet, or change their owner.")}>
                                                                <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }} tabIndex={0}>{t("Not for runs")}</Label>
                                                            </Tooltip>
                                                        )}
                                                    </Td>
                                                    <Td dataLabel={t("Size")}>{file ? formatBytes(file.size) : ""}</Td>
                                                    <Td dataLabel={t("SHA-256")}>{file && <Digest file={file} />}</Td>
                                                    <Td dataLabel={t("Origin")}>{file && <Origin file={file} />}</Td>
                                                    <Td dataLabel={t("By")}>{file?.updated_by ?? ""}</Td>
                                                    <Td dataLabel={t("Updated")}>{file ? formatTime(file.updated_at, i18n.language) : ""}</Td>
                                                    <Td isActionCell>{items.length > 0 && <ActionsColumn items={items} />}</Td>
                                                </Tr>
                                            );
                                        })}
                                    </Tbody>
                                </Table>
                            )}
                        </ListState>
                    )}
            </div>
            {canUpload && (
                <AtTerminal text={shellLine(["fleet", "store", "put", "--file", "FILE", "--path", joinPath(directory, "FILE")])} />
            )}

            {replacing && (
                <FormModal title={t("Replace files?")} submitLabel={t("Replace")} isDanger variant={ModalVariant.small}
                    onClose={() => setReplacing(null)}
                    onSubmit={async () => { queue(replacing, true); setReplacing(null); }}>
                    <Content component="p">{t("These files are already in this folder, and would be replaced:")}</Content>
                    <Content component="ul">
                        {replacing.filter(file => (list.data?.files ?? []).some(other => other.path === joinPath(directory, file.name)))
                            .map(file => <Content component="li" key={file.name}>{file.name}</Content>)}
                    </Content>
                </FormModal>
            )}
            {making && (
                <NameModal title={t("New folder")} label={t("Name")} initial="" submitLabel={t("Create")}
                    help={directory ? t("In {{path}}. A / makes folders within folders.", { path: directory }) : t("A / makes folders within folders.")}
                    onClose={() => setMaking(false)}
                    onSubmit={async name => {
                        await call("store.mkdir", { path: joinPath(directory, name) });
                        setMaking(false);
                        list.reload();
                    }} />
            )}
            {moving && (
                <NameModal title={t("Rename or move {{name}}", { name: moving.name })} label={t("New path in the store")} initial={moving.path}
                    submitLabel={t("Move")} help={t("The whole path from the top of the store: a/b/name.")}
                    onClose={() => setMoving(null)}
                    onSubmit={async to => {
                        await call("store.move", { path: moving.path, to });
                        setMoving(null);
                        list.reload();
                    }} />
            )}
            {deleting && (
                <ConfirmDelete name={deleting.name}
                    what={deleting.file
                        ? t("The file is deleted from the store. Runs under way keep it until they end.")
                        : t("The folder is deleted from the store with everything in it. Runs under way keep what they were given until they end.")}
                    onClose={() => setDeleting(null)}
                    onDelete={async () => {
                        await call("store.delete", { path: deleting.path });
                        setDeleting(null);
                        list.reload();
                    }} />
            )}
        </>
    );
}
