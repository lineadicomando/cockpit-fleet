import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Content,
    ExpandableSection,
    Flex,
    FlexItem,
    Label,
    ModalVariant,
    Progress,
    ProgressMeasureLocation,
    ToolbarItem,
    Tooltip,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatBytes } from "../lib/checks.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling, useStoredState } from "../lib/hooks.ts";
import { useCan, useSession } from "../lib/session.tsx";
import {
    ARCHIVE_LINK, SFTP_SERVER, archiveName, archiveUrl, downloadArchive, downloadFromStore, endedDownloads, joinPath, playbookPath, readClosed, sftpCommand, sizeOf,
    uploadToStore, visibleDownloads,
    type PendingUpload, type StoreDownload, type StoreFile, type StoreList, type StoreUnchanged,
} from "../lib/store.ts";
import { shellLine } from "../lib/terminal.ts";
import { AtTerminal, FormModal, SectionHeader, openRun } from "./common.tsx";
import { FileManager, type FileOps, type FileTools } from "./FileManager.tsx";
import { FetchModal, Origin, StoreDetails } from "./StoreDialogs.tsx";
import { Alert, ClipboardCopy } from "./pf.tsx";

const ACTIVE = ["queued", "running"];
// How often the page asks whether the store changed, while it is shown.
const REFRESH = 10000;

type StoreDialog =
    | { kind: "fetch"; again: StoreDownload | null }
    | { kind: "cancel-download"; item: StoreDownload };

// Files on the controller that playbooks copy to nodes: installers, images,
// what runs fetched. Viewers download them; operators also upload and arrange,
// in a list that works as a file manager does (FileManager).
export function StorePage({ directory }: { directory: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<StoreList>("store.list"));
    const pending = useLoad(() => can("store.uploads") ? call<{ uploads: PendingUpload[] }>("store.uploads") : Promise.resolve({ uploads: [] }));
    const downloads = useLoad(() => can("store.downloads") ? call<{ downloads: StoreDownload[] }>("store.downloads") : Promise.resolve({ downloads: [] }));
    const [dialog, setDialog] = useState<StoreDialog | null>(null);
    const [listDialog, setListDialog] = useState(false);
    // What the page found changed when it asked again, until the next reload.
    const [fresh, setFresh] = useState<StoreList | null>(null);
    // Downloads and copies that ended, taken off the page by the user: the
    // lists do not go by themselves, which would move the page under the pointer.
    const [closed, setClosed] = useStoredState("store-downloads-closed", readClosed);
    const tools = useRef<FileTools | null>(null);
    const [sending, setSending] = useState<string | null>(null);

    const canUpload = can("store.put");
    const canRefresh = can("store.refresh");
    const go = (path: string) => cockpit.location.go(["store", ...(path ? path.split("/") : [])]);

    useEffect(() => {
        setFresh(null);
    }, [list.data]);
    const data = fresh ?? list.data;

    // While the page is shown, it asks whether the store changed (a run, a
    // file put there by hand): the answer is short when nothing did.
    const [visible, setVisible] = useState(document.visibilityState === "visible");
    useEffect(() => {
        const look = () => setVisible(document.visibilityState === "visible");
        document.addEventListener("visibilitychange", look);
        return () => document.removeEventListener("visibilitychange", look);
    }, []);
    usePolling(async () => {
        const base = fresh ?? list.data;
        if (!base?.token) return;
        const answer = await call<StoreList | StoreUnchanged>("store.list", { since: base.token });
        if (!("unchanged" in answer)) setFresh(answer);
        else if (answer.free !== base.free) setFresh({ ...base, free: answer.free });
    }, REFRESH, visible && !!list.data && !dialog && !listDialog);

    // While something downloads: its progress, and the list once it lands.
    const busy = (downloads.data?.downloads ?? []).some(item => ACTIVE.includes(item.status));
    const wasBusy = useRef(false);
    usePolling(async () => {
        downloads.reload();
    }, 2000, busy);
    useEffect(() => {
        if (wasBusy.current && !busy) list.reload();
        wasBusy.current = busy;
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [busy]);

    const ops: FileOps = {
        move: async moves => { await call("store.move", { moves }); },
        // Made at once up to 1 GiB, by a job beside the downloads above it.
        copy: async copies => {
            const done = await call<{ copied?: string[]; run_id?: string; size: number }>("store.copy", { copies });
            if (done.run_id) {
                downloads.reload();
                return { notice: t("{{size}} to copy: the copy goes on beside the downloads, above.", { size: formatBytes(done.size) }) };
            }
            return { made: done.copied ?? [] };
        },
        remove: async paths => { await call("store.delete", { paths }); },
        mkdir: async path => { await call("store.mkdir", { path }); },
        // What an earlier upload of the same file left is not sent again.
        upload: async (file, path, replace, onProgress, still) => {
            let offset = 0;
            try {
                const { uploads: earlier } = await call<{ uploads: PendingUpload[] }>("store.uploads");
                offset = earlier.find(upload => upload.path === path && upload.size === file.size)?.received ?? 0;
            } catch {
                offset = 0;
            }
            if (!still()) return null;
            return uploadToStore(file, path, { offset, replace, onProgress });
        },
        download: path => downloadFromStore(path),
        // Folders, or many entries, as one tar made while it downloads.
        downloadMany: async paths => {
            const name = archiveName(paths, directory);
            if (archiveUrl(paths, name).length > ARCHIVE_LINK) throw new Error(t("Too many entries for one archive: download the folder they are in."));
            await downloadArchive(paths, name);
        },
    };

    // Files downloaded from a URL, checked against it now.
    async function checkSources(paths: string[], tools: FileTools) {
        tools.report(null);
        try {
            const done = await call<{ run_ids: string[]; skipped: string[] }>("store.refresh", { paths });
            tools.notify(done.run_ids.length
                ? t("Checking against the URL: the downloads above say how it goes.")
                : t("A download of it is already under way."));
            downloads.reload();
        } catch (ex) {
            tools.report(ex instanceof Error ? ex.message : String(ex));
        }
    }

    async function copyPlaybookPath(path: string, tools: FileTools) {
        try {
            await navigator.clipboard.writeText(playbookPath(path));
            tools.notify(t("Copied: {{text}}", { text: playbookPath(path) }));
        } catch {
            tools.report(t("The browser did not let the page copy. The path is in the details of the file."));
            tools.showDetails(path);
        }
    }

    const shownDownloads = visibleDownloads(downloads.data?.downloads ?? [], closed).slice(0, 10);
    const elsewhere = (pending.data?.uploads ?? []).filter(upload => upload.path !== sending);

    const between = (
        <>
            {shownDownloads.length > 0 && (
                <Table variant="compact" aria-label={t("Downloads and copies into the store")} style={{ marginBottom: "1rem" }}>
                    <Thead>
                        <Tr>
                            <Th>{t("Into the store")}</Th><Th>{t("Origin")}</Th><Th width={30}>{t("Progress")}</Th>
                            {shownDownloads.some(item => !ACTIVE.includes(item.status))
                                ? <Th modifier="fitContent"><Button variant="link" isInline onClick={() => setClosed(endedDownloads(downloads.data?.downloads ?? []))}>{t("Close")}</Button></Th>
                                : <Th screenReaderText={t("Actions")} />}
                        </Tr>
                    </Thead>
                    <Tbody>
                        {shownDownloads.map(item => (
                            <Tr key={item.run_id}>
                                <Td dataLabel={t("Into the store")}>
                                    <Button variant="link" isInline onClick={() => openRun(item.run_id)}>{item.path}</Button>
                                </Td>
                                <Td dataLabel={t("Origin")} modifier="truncate">
                                    {item.kind === "copy"
                                        ? (item.count ?? 1) > 1 ? t("A copy of {{path}} and others", { path: item.source }) : t("A copy of {{path}}", { path: item.source })
                                        : <span title={item.url}>{item.url}</span>}
                                </Td>
                                <Td dataLabel={t("Progress")}>
                                    {item.status === "running" || item.status === "queued"
                                        ? <Progress value={item.total ? (item.received ?? 0) * 100 / item.total : 0} size="sm"
                                            measureLocation={ProgressMeasureLocation.outside}
                                            aria-label={t("Progress of {{name}}", { name: item.path })}
                                            label={item.status === "queued" ? t("Waiting")
                                                : item.total ? `${formatBytes(item.received ?? 0)} / ${formatBytes(item.total)}`
                                                : formatBytes(item.received ?? 0)} />
                                        : item.status === "successful" && item.unchanged
                                            ? <Label isCompact color="grey">{t("Not changed at the URL")}</Label>
                                            : item.status === "successful"
                                            ? <Label isCompact color="green">{t("Done: {{size}}", { size: formatBytes(item.size ?? 0) })}</Label>
                                            : <span>{item.status === "canceled" ? (item.kind === "copy" ? t("Copy canceled") : t("Download canceled"))
                                                : item.error || (item.kind === "copy" ? t("Copy failed") : t("Download failed"))}</span>}
                                </Td>
                                <Td isActionCell>
                                    {ACTIVE.includes(item.status) && can("run.cancel") && (
                                        <Button variant="link" onClick={() => setDialog({ kind: "cancel-download", item })}>{t("Cancel")}</Button>
                                    )}
                                    {!ACTIVE.includes(item.status) && item.status !== "successful" && item.kind !== "copy" && can("store.fetch") && (
                                        <Button variant="link" onClick={() => setDialog({ kind: "fetch", again: item })}>{item.partial ? t("Resume") : t("Try again")}</Button>
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
                            <FlexItem><Button variant="link" isInline onClick={() => tools.current?.act(() => call("store.discard", { path: upload.path }))}>{t("Discard")}</Button></FlexItem>
                        </Flex>
                    ))}
                </Alert>
            )}
        </>
    );

    return (
        <>
            <SectionHeader title={t("Store")}>
                {canUpload && <Button variant="secondary" onClick={() => tools.current?.newFolder()}>{t("New folder")}</Button>}
                {can("store.fetch") && <Button variant="secondary" onClick={() => setDialog({ kind: "fetch", again: null })}>{t("Download from a URL")}</Button>}
                {canUpload && <Button variant="secondary" onClick={() => tools.current?.pick(directory, true)}>{t("Upload a folder")}</Button>}
                {canUpload && <Button variant="primary" onClick={() => tools.current?.pick(directory, false)}>{t("Upload files")}</Button>}
            </SectionHeader>
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
            <FileManager<StoreFile> kind="store" directory={directory} go={go} list={list} data={data} reload={list.reload}
                can={{ upload: canUpload, move: can("store.move"), copy: can("store.copy"), delete: can("store.delete") }}
                ops={ops} tools={tools} between={between} onDialog={setListDialog}
                afterUploads={pending.reload} onSending={setSending}
                words={{
                    root: t("Store"), top: t("the top of the store"), searchAll: t("Search the whole store"), inAll: t("In the whole store"),
                    listLabel: t("Files of the store"), empty: t("The store is empty"),
                    emptyFolder: t("This folder is empty"),
                    emptyBody: canUpload ? t("Upload files, or drop them here. A run can add files too, through fleet_store.") : t("Files are put here by operators, and by runs."),
                    missing: t("There is no folder {{path}} in the store.", { path: directory }),
                    hint: t("Drop files or folders from your computer here or onto a folder; drag entries onto a folder, or onto the path above, to move them, holding Ctrl to copy them. Keys: Ctrl+A, Ctrl+X or Ctrl+C and Ctrl+V, Delete, F2, / to search; the menu key or Shift+F10 for an entry's menu."),
                    incomplete: t("The store holds more than can be listed here: only part of it is shown."),
                }}
                columns={[
                    { title: t("Size"), sort: "size", cell: entry => (entry.file ? formatBytes(entry.file.size) : "") },
                    { title: t("Origin"), cell: entry => entry.file && <Origin file={entry.file} /> },
                    { title: t("Updated"), sort: "updated", cell: entry => (entry.file ? formatTime(entry.file.updated_at, i18n.language) : "") },
                ]}
                badges={file => !file.usable && (
                    <Tooltip content={t("Not given to runs: the file is not the service account's own. Put files here as cockpit-fleet, or change their owner.")}>
                        <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }} tabIndex={0}>{t("Not for runs")}</Label>
                    </Tooltip>
                )}
                openFile={(path, tools) => tools.showDetails(path)}
                fileActions={(entry, tools) => [
                    { title: t("Copy the path for a playbook"), onClick: () => copyPlaybookPath(entry.path, tools) },
                    ...(entry.file?.refresh && canRefresh ? [{ title: t("Check the source for a newer version"), onClick: () => checkSources([entry.path], tools) }] : []),
                ]}
                toolbar={(chosen, tools) => canRefresh && chosen.every(path => data?.files.some(file => file.path === path && file.refresh)) && (
                    <ToolbarItem><Button variant="secondary" size="sm" onClick={() => checkSources(chosen, tools)}>{t("Check the sources")}</Button></ToolbarItem>
                )}
                dragText={paths => paths.map(playbookPath).join("\n")}
                deleteText={(paths, shown) => paths.length > 1
                    ? (paths.some(path => shown?.directories.includes(path))
                        ? t("{{what}} are deleted from the store, {{size}} in all, with everything in the folders among them. Runs under way keep what they were given until they end.",
                            { what: t("files_count", { count: paths.length }), size: formatBytes(shown ? sizeOf(shown, paths).bytes : 0) })
                        : t("{{what}} are deleted from the store, {{size}} in all. Runs under way keep them until they end.",
                            { what: t("files_count", { count: paths.length }), size: formatBytes(shown ? sizeOf(shown, paths).bytes : 0) }))
                    : shown?.files.some(file => file.path === paths[0])
                        ? t("The file is deleted from the store. Runs under way keep it until they end.")
                        : t("The folder is deleted from the store with everything in it. Runs under way keep what they were given until they end.")}
                details={(entry, tools) => data && (
                    <StoreDetails list={data} path={entry.path} onClose={() => tools.showDetails(null)}
                        onTrack={can("store.track") ? every => tools.act(() => call("store.track", { path: entry.path, every })) : undefined}
                        onRefresh={canRefresh ? () => checkSources([entry.path], tools) : undefined}
                        actions={[
                            entry.file && <Button key="download" variant="primary" size="sm" onClick={() => tools.act(() => downloadFromStore(entry.path))}>{t("Download")}</Button>,
                            !entry.file && <Button key="open" variant="primary" size="sm" onClick={() => go(entry.path)}>{t("Open")}</Button>,
                            !entry.file && <Button key="archive" variant="secondary" size="sm" onClick={() => tools.act(() => ops.downloadMany!([entry.path]))}>{t("Download as an archive")}</Button>,
                            can("store.move") && <Button key="rename" variant="secondary" size="sm" onClick={() => tools.rename(entry.path)}>{t("Rename")}</Button>,
                            can("store.move") && <Button key="move" variant="secondary" size="sm" onClick={() => tools.moveTo([entry.path])}>{t("Move to…")}</Button>,
                            can("store.delete") && <Button key="delete" variant="secondary" isDanger size="sm" onClick={() => tools.remove([entry.path])}>{t("Delete")}</Button>,
                        ]} />
                )} />
            {canUpload && (
                <AtTerminal text={shellLine(["fleet", "store", "put", "--file", "FILE", "--path", joinPath(directory, "FILE")])} />
            )}
            {can("store.sftp") && <SftpAccess writes={canUpload} />}

            {dialog?.kind === "fetch" && (
                <FetchModal directory={directory} again={dialog.again} list={data} onClose={() => setDialog(null)} onQueued={downloads.reload} />
            )}
            {dialog?.kind === "cancel-download" && (
                <CancelDownload item={dialog.item} onClose={() => setDialog(null)}
                    onCancel={async () => {
                        await call("run.cancel", { id: dialog.item.run_id });
                        setDialog(null);
                        downloads.reload();
                    }} />
            )}
        </>
    );
}


// How to reach the store from WinSCP, sftp, sshfs: the command each names as
// the server to start over an SSH login to this machine.
function SftpAccess({ writes }: { writes: boolean }) {
    const { t } = useTranslation();
    const session = useSession();
    const [open, setOpen] = useState(false);
    return (
        <ExpandableSection toggleText={t("With an SFTP client")} isExpanded={open} onToggle={(_e, value) => setOpen(value)}>
            <Content component="p">
                {t("WinSCP, sftp, sshfs and other SFTP clients reach the store through your own SSH login to this machine. Name this command as the SFTP server to start (in WinSCP: Advanced, Environment, SFTP, SFTP server):")}
            </Content>
            <ClipboardCopy isReadOnly isCode aria-label={t("SFTP server")}>{SFTP_SERVER}</ClipboardCopy>
            <Content component="p" style={{ marginTop: "1rem" }}>{t("With OpenSSH, from another machine:")}</Content>
            <ClipboardCopy isReadOnly isCode aria-label={t("Command")}>{sftpCommand(session.user, window.location.hostname)}</ClipboardCopy>
            <Content component="p" style={{ marginTop: "1rem" }}>
                {writes
                    ? t("You can read, write, rename and delete there, each change in the audit log. A file written reaches the store, and runs, only when it is complete; an interrupted transfer can be resumed.")
                    : t("You can read and download the files there; writing needs the operator role.")}
            </Content>
        </ExpandableSection>
    );
}

// Asks before stopping a download or a copy into the store.
function CancelDownload({ item, onCancel, onClose }: { item: StoreDownload; onCancel: () => Promise<void>; onClose: () => void }) {
    const { t } = useTranslation();
    const copy = item.kind === "copy";
    return (
        <FormModal
            title={copy ? t("Cancel the copy into {{path}}?", { path: item.path }) : t("Cancel the download into {{path}}?", { path: item.path })}
            submitLabel={copy ? t("Cancel the copy") : t("Cancel the download")}
            cancelLabel={t("Keep running")}
            onSubmit={onCancel}
            onClose={onClose}
            isDanger
            variant={ModalVariant.small}
        >
            <Content component="p">
                {item.status === "queued"
                    ? copy ? t("The copy has not started yet: it will not start.") : t("The download has not started yet: it will not start.")
                    : copy
                        ? t("The copy stops, and nothing of it reaches the store.")
                        : t("The download stops. What has arrived is kept for a week, and \"Resume\" goes on from there when the server allows it.")}
            </Content>
        </FormModal>
    );
}
