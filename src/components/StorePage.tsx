import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    AlertActionLink,
    Breadcrumb,
    BreadcrumbItem,
    Button,
    Checkbox,
    Content,
    Drawer,
    DrawerContent,
    DrawerContentBody,
    Flex,
    FlexItem,
    Label,
    Progress,
    ProgressMeasureLocation,
    Toolbar,
    ToolbarContent,
    ToolbarGroup,
    ToolbarItem,
    Tooltip,
} from "@patternfly/react-core";
import { FileIcon, FolderIcon } from "@patternfly/react-icons";
import { Table, Tbody, Td, Th, Thead, Tr, type ThProps } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatBytes } from "../lib/checks.ts";
import { basename, dirname, isInside, relocate, topmost } from "../lib/files.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad, usePolling, useStoredState } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import {
    ARCHIVE_LINK, archiveName, archiveUrl, chosenFolder, describe, downloadArchive, downloadFromStore, droppedOf, filterEntries, indexStore, joinPath, moveInList, playbookPath, rangeBetween,
    readStoreSort, removeFromList, searchStore, settle, sizeOf, sortEntries, uploadToStore,
    type Clash, type Dropped, type PendingUpload, type StoreDownload, type StoreEntry, type StoreList, type StoreSortKey, type StoreUnchanged, type Upload,
} from "../lib/store.ts";
import { shellLine } from "../lib/terminal.ts";
import { AtTerminal, ConfirmDelete, ListState, SectionHeader, message } from "./common.tsx";
import { ContextMenu, type MenuEntry } from "./ContextMenu.tsx";
import { RelocateModal } from "./RelocateModal.tsx";
import { ConflictModal, FetchModal, NewFolderModal, Origin, RenameModal, StoreDetails } from "./StoreDialogs.tsx";
import { ActionsColumn, Alert, SearchInput } from "./pf.tsx";

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

interface Move {
    path: string;
    to: string;
}

type Dialog =
    | { kind: "new-folder" }
    | { kind: "fetch"; again: StoreDownload | null }
    | { kind: "move"; paths: string[] }
    | { kind: "copy"; paths: string[] }
    | { kind: "rename"; path: string }
    | { kind: "delete"; paths: string[] }
    | { kind: "upload-clash"; files: { file: File; path: string }[]; clashing: string[] }
    | { kind: "move-clash"; moves: Move[]; clashing: string[]; copy: boolean };

// What a drag of entries of the store carries: their paths, as JSON.
const DRAGGED = "application/x-cockpit-fleet-store";
const ACTIVE = ["queued", "running"];
// Entries found in the whole store shown at most.
const FOUND = 500;
// How often the page asks whether the store changed, while it is shown.
const REFRESH = 10000;

function isTyping(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    if (target.isContentEditable || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
    return target instanceof HTMLInputElement && !["checkbox", "radio", "button"].includes(target.type);
}

// Files on the controller that playbooks copy to nodes: installers, images,
// what runs fetched. Viewers download them; operators also upload and arrange.
// The list works as a file manager does: many entries selected, dragged onto
// a folder (copied with Ctrl), cut or copied and pasted, with a menu on
// each; all of it can be done from the keyboard and the menus too.
export function StorePage({ directory }: { directory: string }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<StoreList>("store.list"));
    const pending = useLoad(() => can("store.uploads") ? call<{ uploads: PendingUpload[] }>("store.uploads") : Promise.resolve({ uploads: [] }));
    const downloads = useLoad(() => can("store.downloads") ? call<{ downloads: StoreDownload[] }>("store.downloads") : Promise.resolve({ downloads: [] }));
    const [uploads, setUploads] = useState<UploadItem[]>([]);
    const [dialog, setDialog] = useState<Dialog | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ text: string; undo?: () => void } | null>(null);
    // A change shown before the backend has answered, until its next listing.
    const [overlay, setOverlay] = useState<StoreList | null>(null);
    // What the page found changed when it asked again, until the next reload.
    const [fresh, setFresh] = useState<StoreList | null>(null);
    const [query, setQuery] = useState("");
    const [everywhere, setEverywhere] = useState(false);
    const [sort, setSort] = useStoredState("store-sort", readStoreSort);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [anchor, setAnchor] = useState<string | null>(null);
    // Entries cut or copied, to be pasted elsewhere: a clipboard of the page.
    const [clip, setClip] = useState<{ paths: string[]; copy: boolean } | null>(null);
    const cut = clip && !clip.copy ? clip.paths : [];
    const [details, setDetails] = useState<string | null>(null);
    const [menu, setMenu] = useState<{ x: number; y: number; entry: StoreEntry | null } | null>(null);
    // Where a drag is: a folder's path, or null; "." for the list itself.
    const [target, setTarget] = useState<string | null>(null);
    const chooser = useRef<HTMLInputElement>(null);
    const folderChooser = useRef<HTMLInputElement>(null);
    const uploadInto = useRef("");
    const current = useRef<Upload | null>(null);
    const nextKey = useRef(1);

    const canUpload = can("store.put");
    const canMove = can("store.move");
    const canDelete = can("store.delete");
    const canCopy = can("store.copy");
    const canRefresh = can("store.refresh");
    const go = (path: string) => cockpit.location.go(["store", ...(path ? path.split("/") : [])]);

    useEffect(() => {
        setOverlay(null);
        setFresh(null);
    }, [list.data]);
    const data = overlay ?? fresh ?? list.data;

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
        if (!base?.token || overlay) return;
        const answer = await call<StoreList | StoreUnchanged>("store.list", { since: base.token });
        if (!("unchanged" in answer)) setFresh(answer);
        else if (answer.free !== base.free) setFresh({ ...base, free: answer.free });
    }, REFRESH, visible && !!list.data && !dialog);
    const index = useMemo(() => (data ? indexStore(data) : new Map<string, StoreEntry[]>()), [data]);
    const taken = useMemo(() => new Set([...(data?.files ?? []).map(file => file.path), ...(data?.directories ?? [])]), [data]);
    const searching = everywhere && query.trim() !== "";
    const found = useMemo(() => (data && searching ? searchStore(data, query, FOUND) : null), [data, searching, query]);
    const shown = useMemo(() => found ? found.entries : sortEntries(filterEntries(index.get(directory) ?? [], query), sort),
        [found, index, directory, query, sort]);
    const order = useMemo(() => shown.map(entry => entry.path), [shown]);
    // Only what is shown counts as selected: a filter hides the rest.
    const chosen = useMemo(() => order.filter(path => selected.has(path)), [order, selected]);
    const chosenSize = useMemo(() => (data && chosen.length ? sizeOf(data, chosen) : null), [data, chosen]);
    const missing = !!data && directory !== "" && !data.directories.includes(directory);

    useEffect(() => {
        setSelected(new Set());
        setAnchor(null);
        setQuery("");
    }, [directory]);

    useEffect(() => {
        folderChooser.current?.setAttribute("webkitdirectory", "");
    }, []);

    // "/" goes to the search, as in many lists of files.
    useEffect(() => {
        const onKey = (event: KeyboardEvent) => {
            if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target) || dialog || menu) return;
            event.preventDefault();
            document.getElementById("store-search")?.focus();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [dialog, menu]);

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

    function update(key: number, change: Partial<UploadItem>) {
        setUploads(items => items.map(item => item.key === key ? { ...item, ...change } : item));
    }

    // One upload at a time, in the order they were asked for.
    const uploadsLeft = useRef(0);
    uploadsLeft.current = uploads.filter(item => item.state === "waiting").length;
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
                // The list follows the uploads, but not after each of a hundred small files.
                if (!uploadsLeft.current) list.reload();
                pending.reload();
            }
        })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sending, next?.key]);

    function queue(files: { file: File; path: string; replace: boolean }[]) {
        const added = files.map(item => ({ key: nextKey.current++, ...item, sent: 0, state: "waiting" as const }));
        setUploads(items => [...items.filter(item => item.state !== "done"), ...added]);
    }

    // Files from the desktop into the folder INTO, with the folders they were
    // dropped in; names already there are asked about once for all.
    async function startUploads(dropped: Dropped, into: string) {
        if (!canUpload) return;
        setError(null);
        const files = dropped.files.map(item => ({ file: item.file, path: joinPath(into, item.path) }));
        try {
            for (const path of dropped.directories) await call("store.mkdir", { path: joinPath(into, path) });
        } catch (ex) {
            setError(message(ex));
            return;
        }
        if (dropped.directories.length && !files.length) list.reload();
        const clashing = files.filter(item => taken.has(item.path)).map(item => item.path);
        if (clashing.length) setDialog({ kind: "upload-clash", files, clashing });
        else queue(settle(files, taken, "skip"));
    }

    function pick(into: string, folder: boolean) {
        uploadInto.current = into;
        (folder ? folderChooser : chooser).current?.click();
    }

    function cancel(item: UploadItem) {
        update(item.key, { state: "cancelled" });
        if (item.state === "sending") current.current?.cancel();
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

    // Moves in one call, all or nothing, shown before the answer; what was
    // done can be undone.
    async function runMoves(moves: Move[], undoable: boolean) {
        if (!data || !moves.length) return;
        setError(null);
        setNotice(null);
        setOverlay(moveInList(data, moves));
        setSelected(new Set());
        setClip(null);
        try {
            await call("store.move", { moves });
        } catch (ex) {
            setOverlay(null);
            setError(message(ex));
            list.reload();
            return;
        }
        if (details) {
            const moved = moves.find(move => isInside(details, move.path));
            if (moved) setDetails(moved.to + details.slice(moved.path.length));
        }
        const what = moves.length === 1 ? basename(moves[0].path) : t("files_count", { count: moves.length });
        // Kept beside one with the same name: the name it has now.
        const named = moves.length === 1 ? basename(moves[0].to) : what;
        const folder = dirname(moves[0].to) || t("the top of the store");
        const back = moves.map(move => ({ path: move.to, to: move.path })).reverse();
        setNotice({
            text: !undoable ? t("Move undone.")
                : dirname(moves[0].path) === dirname(moves[0].to) && moves.length === 1 ? t("{{what}} renamed to {{name}}.", { what, name: basename(moves[0].to) })
                    : t("{{what}} moved to {{folder}}.", { what: named, folder }),
            undo: undoable ? () => runMoves(back, false) : undefined,
        });
        list.reload();
    }

    // Copies in one call: made at once up to 1 GiB, by a job beside the
    // downloads above it. Those made at once can be undone (deleted).
    async function runCopies(copies: Move[]) {
        if (!copies.length) return;
        setError(null);
        setNotice(null);
        setSelected(new Set());
        setClip(null);
        try {
            const done = await call<{ copied?: string[]; run_id?: string; size: number }>("store.copy", { copies });
            if (done.run_id) {
                setNotice({ text: t("{{size}} to copy: the copy goes on beside the downloads, above.", { size: formatBytes(done.size) }) });
                downloads.reload();
            } else {
                const made = done.copied ?? [];
                const what = made.length === 1 ? basename(made[0]) : t("files_count", { count: made.length });
                setNotice({
                    text: t("{{what}} copied to {{folder}}.", { what, folder: dirname(copies[0].to) || t("the top of the store") }),
                    undo: () => remove(made, t("Copy undone.")),
                });
            }
        } catch (ex) {
            setError(message(ex));
        }
        list.reload();
    }

    function paste(into: string) {
        if (!clip) return;
        if (clip.copy) copyInto(clip.paths, into);
        else moveInto(clip.paths, into);
    }

    // Entries into the folder INTO: by a drag, a paste, or "Move to…".
    function moveInto(paths: string[], into: string) {
        if (!canMove) return;
        const moves = relocate(paths, into);
        if (moves === null) {
            setError(t("A folder cannot go into itself."));
            return;
        }
        const clashing = moves.filter(move => taken.has(move.to)).map(move => move.to);
        if (clashing.length) setDialog({ kind: "move-clash", moves, clashing, copy: false });
        else runMoves(moves, true);
    }

    // Copies into the folder INTO; one already there is never replaced.
    function copyInto(paths: string[], into: string) {
        if (!canCopy) return;
        const copies = topmost(paths).map(path => ({ path, to: joinPath(into, basename(path)) }));
        if (copies.some(copy => into !== "" && isInside(into, copy.path))) {
            setError(t("A folder cannot go into itself."));
            return;
        }
        const clashing = copies.filter(copy => taken.has(copy.to));
        // Pasted into the folder they are in: kept beside under another name, unasked.
        if (clashing.length && clashing.every(copy => copy.to === copy.path)) settleMoves(copies, "keep", true);
        else if (clashing.length) setDialog({ kind: "move-clash", moves: copies, clashing: clashing.map(copy => copy.to), copy: true });
        else runCopies(copies);
    }

    function settleMoves(moves: Move[], clash: Clash, copy: boolean) {
        // A move frees the paths it leaves; a copy does not.
        const others = copy ? taken : new Set([...taken].filter(path => !moves.some(move => isInside(path, move.path))));
        const settled = settle(moves.map(move => ({ path: move.to, from: move.path })), others, clash)
            .map(item => ({ path: item.from, to: item.path }));
        if (copy) runCopies(settled);
        else runMoves(settled, true);
    }

    async function remove(paths: string[], done?: string) {
        if (!data) return;
        const gone = topmost(paths);
        setOverlay(removeFromList(data, gone));
        setSelected(new Set());
        setNotice(null);
        if (details && gone.some(path => isInside(details, path))) setDetails(null);
        try {
            await call("store.delete", { paths: gone });
            setError(null);
            if (done) setNotice({ text: done });
        } catch (ex) {
            setOverlay(null);
            setError(message(ex));
        }
        list.reload();
    }

    async function download(path: string) {
        await act(() => downloadFromStore(path));
    }

    // Files downloaded from a URL, checked against it now.
    async function checkSources(paths: string[]) {
        setError(null);
        setNotice(null);
        try {
            const done = await call<{ run_ids: string[]; skipped: string[] }>("store.refresh", { paths });
            setNotice({
                text: done.run_ids.length
                    ? t("Checking against the URL: the downloads above say how it goes.")
                    : t("A download of it is already under way."),
            });
            downloads.reload();
        } catch (ex) {
            setError(message(ex));
        }
    }

    // Folders, or many entries, as one tar made while it downloads.
    async function downloadMany(paths: string[]) {
        const gone = topmost(paths);
        const name = archiveName(gone, directory);
        if (archiveUrl(gone, name).length > ARCHIVE_LINK) {
            setError(t("Too many entries for one archive: download the folder they are in."));
            return;
        }
        await act(() => downloadArchive(gone, name));
    }

    async function copyPlaybookPath(path: string) {
        try {
            await navigator.clipboard.writeText(playbookPath(path));
            setNotice({ text: t("Copied: {{text}}", { text: playbookPath(path) }) });
        } catch {
            setError(t("The browser did not let the page copy. The path is in the details of the file."));
            setDetails(path);
        }
    }

    function select(entry: StoreEntry, event: React.MouseEvent) {
        if (event.shiftKey && anchor) {
            const range = rangeBetween(order, anchor, entry.path);
            setSelected(current => new Set(event.ctrlKey || event.metaKey ? [...current, ...range] : range));
            return;
        }
        if (event.ctrlKey || event.metaKey) {
            toggle(entry.path);
        } else {
            setSelected(new Set([entry.path]));
        }
        setAnchor(entry.path);
    }

    function toggle(path: string) {
        setSelected(current => {
            const next = new Set(current);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
        setAnchor(path);
    }

    // What the menus offer for ENTRY, or for all that is selected when it is one of them.
    function entryActions(entry: StoreEntry): MenuEntry[] {
        const many = selected.has(entry.path) && chosen.length > 1 ? chosen : null;
        const paths = many ?? [entry.path];
        const items: MenuEntry[] = [];
        if (many) items.push({ title: t("Download as an archive"), onClick: () => downloadMany(many) });
        if (!many) {
            if (entry.file) {
                items.push({ title: t("Download"), onClick: () => download(entry.path) });
                items.push({ title: t("Copy the path for a playbook"), onClick: () => copyPlaybookPath(entry.path) });
                if (entry.file.refresh && canRefresh) items.push({ title: t("Check the source for a newer version"), onClick: () => checkSources([entry.path]) });
            } else {
                items.push({ title: t("Open"), onClick: () => go(entry.path) });
                items.push({ title: t("Download as an archive"), onClick: () => downloadMany([entry.path]) });
                if (clip && (clip.copy ? canCopy : canMove)) items.push({ title: t("Paste into this folder"), onClick: () => paste(entry.path) });
                if (canUpload) {
                    items.push({ title: t("Upload files here"), onClick: () => pick(entry.path, false) });
                    items.push({ title: t("Upload a folder here"), onClick: () => pick(entry.path, true) });
                }
            }
            items.push({ title: t("Details"), onClick: () => setDetails(entry.path) });
        }
        if (canMove || canCopy) {
            if (items.length) items.push({ isSeparator: true });
            if (canMove && !many) items.push({ title: t("Rename"), onClick: () => setDialog({ kind: "rename", path: entry.path }) });
            if (canMove) items.push({ title: t("Move to…"), onClick: () => setDialog({ kind: "move", paths }) });
            if (canCopy) items.push({ title: t("Copy to…"), onClick: () => setDialog({ kind: "copy", paths }) });
            if (canMove) items.push({ title: t("Cut"), onClick: () => setClip({ paths, copy: false }) });
            if (canCopy) items.push({ title: t("Copy"), onClick: () => setClip({ paths, copy: true }) });
        }
        if (canDelete) {
            items.push({ isSeparator: true });
            items.push({ title: t("Delete"), isDanger: true, onClick: () => setDialog({ kind: "delete", paths }) });
        }
        return items;
    }

    // What the menu of the list itself offers, away from any entry.
    function listActions(): MenuEntry[] {
        const items: MenuEntry[] = [];
        if (clip && (clip.copy ? canCopy : canMove)) items.push({ title: t("Paste here"), onClick: () => paste(directory) });
        if (canUpload) {
            items.push({ title: t("New folder"), onClick: () => setDialog({ kind: "new-folder" }) });
            items.push({ title: t("Upload files"), onClick: () => pick(directory, false) });
            items.push({ title: t("Upload a folder"), onClick: () => pick(directory, true) });
        }
        if (shown.length) items.push({ title: t("Select all"), onClick: () => setSelected(new Set(order)) });
        return items;
    }

    // A menu opened from the keyboard (the menu key, Shift+F10) comes as a
    // contextmenu event too, with no place of the pointer: then below the row.
    function openMenu(event: React.MouseEvent, entry: StoreEntry | null) {
        event.preventDefault();
        event.stopPropagation();
        if (entry && !selected.has(entry.path)) {
            setSelected(new Set([entry.path]));
            setAnchor(entry.path);
        }
        let { clientX: x, clientY: y } = event;
        if (x === 0 && y === 0) {
            const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
            x = box.left + 24;
            y = box.bottom;
        }
        if (entry ? entryActions(entry).length : listActions().length) setMenu({ x, y, entry });
    }

    // The shortcuts of the list, while the focus is in it or nowhere in
    // particular (after a click on a folder, say).
    const keys = useRef<(event: KeyboardEvent) => void>(() => {});
    keys.current = onKeyDown;
    useEffect(() => {
        const listen = (event: KeyboardEvent) => keys.current(event);
        window.addEventListener("keydown", listen);
        return () => window.removeEventListener("keydown", listen);
    }, []);
    const listBox = useRef<HTMLDivElement>(null);

    function onKeyDown(event: KeyboardEvent) {
        const where = event.target as Node | null;
        const inList = where === document.body || (where !== null && !!listBox.current?.contains(where));
        if (!inList || event.defaultPrevented || isTyping(event.target) || dialog || menu) return;
        const control = event.ctrlKey || event.metaKey;
        const key = event.key.toLowerCase();
        let handled = true;
        if (control && key === "a") setSelected(new Set(order));
        else if (control && key === "x" && canMove && chosen.length) setClip({ paths: chosen, copy: false });
        // Text selected on the page is copied as text, as usual.
        else if (control && key === "c" && canCopy && chosen.length && !window.getSelection()?.toString()) setClip({ paths: chosen, copy: true });
        else if (control && key === "v" && clip && !found) paste(directory);
        else if (event.key === "Delete" && canDelete && chosen.length) setDialog({ kind: "delete", paths: chosen });
        else if (event.key === "F2" && canMove && chosen.length === 1) setDialog({ kind: "rename", path: chosen[0] });
        else if (event.key === "Escape" && (chosen.length || clip)) {
            setSelected(new Set());
            setClip(null);
        } else handled = false;
        if (handled) {
            event.preventDefault();
            event.stopPropagation();
        }
    }

    // Where a drag may end: folders take entries of the store and files of
    // the desktop; the list itself, files into the folder shown.
    function dropProps(into: string, mark: string) {
        return {
            onDragOver: (event: React.DragEvent) => {
                const types = event.dataTransfer.types;
                const copying = event.ctrlKey || event.altKey;
                const entries = types.includes(DRAGGED) && (copying ? canCopy : canMove) && mark !== ".";
                const files = types.includes("Files") && canUpload;
                if (!entries && !files) return;
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = entries && !copying ? "move" : "copy";
                setTarget(mark);
            },
            onDragLeave: (event: React.DragEvent) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setTarget(null);
            },
            onDrop: (event: React.DragEvent) => {
                const dragged = event.dataTransfer.getData(DRAGGED);
                if (!dragged && !event.dataTransfer.types.includes("Files")) return;
                event.preventDefault();
                event.stopPropagation();
                setTarget(null);
                if (dragged) {
                    const paths = JSON.parse(dragged) as string[];
                    if (paths.includes(into)) return;
                    // With Ctrl (Alt on a Mac) a drag copies, as in file managers.
                    if (event.ctrlKey || event.altKey) copyInto(paths, into);
                    else moveInto(paths, into);
                } else {
                    // The entries must be taken while the drop is under way.
                    droppedOf(event.dataTransfer).then(dropped => startUploads(dropped, into), ex => setError(message(ex)));
                }
            },
        };
    }

    function dragStart(event: React.DragEvent, entry: StoreEntry) {
        const paths = selected.has(entry.path) ? chosen : [entry.path];
        event.dataTransfer.setData(DRAGGED, JSON.stringify(paths));
        event.dataTransfer.setData("text/plain", paths.map(playbookPath).join("\n"));
        event.dataTransfer.effectAllowed = "copyMove";
    }

    // A drag that ends anywhere clears the place it was over.
    useEffect(() => {
        const clear = () => setTarget(null);
        window.addEventListener("dragend", clear);
        window.addEventListener("drop", clear);
        return () => {
            window.removeEventListener("dragend", clear);
            window.removeEventListener("drop", clear);
        };
    }, []);

    const onSort = (by: StoreSortKey, column: number): ThProps["sort"] => found ? undefined : {
        columnIndex: column,
        sortBy: { index: ["name", "size", "updated"].indexOf(sort.by) + 1, direction: sort.ascending ? "asc" : "desc" },
        onSort: (_event, _index, direction) => setSort({ by, ascending: direction === "asc" }),
    };

    const parts = directory ? directory.split("/") : [];
    const elsewhere = (pending.data?.uploads ?? []).filter(upload => !uploads.some(item => item.path === upload.path && item.state === "sending"));
    const detailed = details && data && (taken.has(details)) ? details : null;
    const detailedEntry: StoreEntry | null = detailed ? { name: basename(detailed), path: detailed, file: data!.files.find(file => file.path === detailed) ?? null } : null;
    const allShown = order.length > 0 && chosen.length === order.length;

    const toolbar = (
        <Toolbar className="fleet-store-toolbar" inset={{ default: "insetNone" }}>
            <ToolbarContent>
                <ToolbarItem>
                    <SearchInput searchInputId="store-search" value={query} placeholder={everywhere ? t("Search the whole store") : t("Filter this folder")}
                        onChange={(_e, value) => setQuery(value)} onClear={() => setQuery("")} />
                </ToolbarItem>
                <ToolbarItem alignSelf="center">
                    <Checkbox id="store-everywhere" label={t("In the whole store")} isChecked={everywhere} onChange={(_e, value) => setEverywhere(value)} />
                </ToolbarItem>
                {chosen.length > 0 && chosenSize && (
                    <ToolbarGroup>
                        <ToolbarItem alignSelf="center">
                            <span><strong>{t("files_selected", { count: chosen.length })}</strong>{chosenSize.bytes > 0 && `, ${formatBytes(chosenSize.bytes)}`}</span>
                        </ToolbarItem>
                        {chosen.length === 1 && data?.files.some(file => file.path === chosen[0])
                            ? <ToolbarItem><Button variant="secondary" size="sm" onClick={() => download(chosen[0])}>{t("Download")}</Button></ToolbarItem>
                            : <ToolbarItem><Button variant="secondary" size="sm" onClick={() => downloadMany(chosen)}>{t("Download as an archive")}</Button></ToolbarItem>}
                        {canRefresh && chosen.every(path => data?.files.some(file => file.path === path && file.refresh)) && (
                            <ToolbarItem><Button variant="secondary" size="sm" onClick={() => checkSources(chosen)}>{t("Check the sources")}</Button></ToolbarItem>
                        )}
                        {canMove && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "move", paths: chosen })}>{t("Move to…")}</Button></ToolbarItem>}
                        {canCopy && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "copy", paths: chosen })}>{t("Copy to…")}</Button></ToolbarItem>}
                        {canMove && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setClip({ paths: chosen, copy: false })}>{t("Cut")}</Button></ToolbarItem>}
                        {canCopy && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setClip({ paths: chosen, copy: true })}>{t("Copy")}</Button></ToolbarItem>}
                        {canDelete && <ToolbarItem><Button variant="secondary" isDanger size="sm" onClick={() => setDialog({ kind: "delete", paths: chosen })}>{t("Delete")}</Button></ToolbarItem>}
                        <ToolbarItem><Button variant="link" size="sm" onClick={() => setSelected(new Set())}>{t("Clear the selection")}</Button></ToolbarItem>
                    </ToolbarGroup>
                )}
                {clip && (clip.copy ? canCopy : canMove) && (
                    <ToolbarGroup>
                        <ToolbarItem>
                            <Button variant="primary" size="sm" isDisabled={!!found} onClick={() => paste(directory)}>
                                {(clip.copy ? t("Paste a copy here: {{what}}", { what: clip.paths.length === 1 ? basename(clip.paths[0]) : t("files_count", { count: clip.paths.length }) })
                                    : t("Paste here: {{what}}", { what: clip.paths.length === 1 ? basename(clip.paths[0]) : t("files_count", { count: clip.paths.length }) }))}
                            </Button>
                        </ToolbarItem>
                        <ToolbarItem><Button variant="link" size="sm" onClick={() => setClip(null)}>{t("Cancel")}</Button></ToolbarItem>
                    </ToolbarGroup>
                )}
            </ToolbarContent>
        </Toolbar>
    );

    function row(entry: StoreEntry, rowIndex: number) {
        const file = entry.file;
        const isSelected = selected.has(entry.path);
        const classes = [];
        if (cut.includes(entry.path)) classes.push("is-cut");
        if (target === entry.path && !file) classes.push("is-target");
        const actions = entryActions(entry);
        return (
            <Tr key={entry.path} className={classes.join(" ") || undefined} isRowSelected={isSelected}
                draggable={canMove || canCopy} onDragStart={event => dragStart(event, entry)}
                {...(file ? {} : dropProps(entry.path, entry.path))}
                onClick={event => { if (!(event.target as HTMLElement).closest("button, input, a")) select(entry, event); }}
                onDoubleClick={event => { if (!(event.target as HTMLElement).closest("button, input, a")) { if (file) setDetails(entry.path); else go(entry.path); } }}
                onContextMenu={event => openMenu(event, entry)}>
                <Td>
                    <Checkbox id={`store-select-${rowIndex}`} aria-label={t("Select {{name}}", { name: entry.name })} isChecked={isSelected}
                        onChange={() => toggle(entry.path)} />
                </Td>
                <Td dataLabel={t("Name")}>
                    <Button variant="link" isInline icon={file ? <FileIcon /> : <FolderIcon />}
                        onClick={event => { if (event.shiftKey || event.ctrlKey || event.metaKey) select(entry, event); else if (file) setDetails(entry.path); else go(entry.path); }}>
                        {entry.name}
                    </Button>
                    {file && !file.usable && (
                        <Tooltip content={t("Not given to runs: the file is not the service account's own. Put files here as cockpit-fleet, or change their owner.")}>
                            <Label isCompact color="orange" style={{ marginInlineStart: "0.5rem" }} tabIndex={0}>{t("Not for runs")}</Label>
                        </Tooltip>
                    )}
                </Td>
                {found && <Td dataLabel={t("Folder")}>
                    <Button variant="link" isInline onClick={() => go(dirname(entry.path))}>{dirname(entry.path) || t("Store")}</Button>
                </Td>}
                <Td dataLabel={t("Size")}>{file ? formatBytes(file.size) : ""}</Td>
                <Td dataLabel={t("Origin")}>{file && <Origin file={file} />}</Td>
                <Td dataLabel={t("Updated")}>{file ? formatTime(file.updated_at, i18n.language) : ""}</Td>
                <Td isActionCell>{actions.length > 0 && <ActionsColumn items={actions.map(item => ("isSeparator" in item ? { isSeparator: true } : item))} />}</Td>
            </Tr>
        );
    }

    const listing = (
        <div ref={listBox} className={`fleet-store-list${target === "." ? " is-target" : ""}`} tabIndex={-1}
            onPaste={event => {
                if (isTyping(event.target) || !canUpload || !event.clipboardData.files.length) return;
                event.preventDefault();
                startUploads({ files: Array.from(event.clipboardData.files).map(file => ({ file, path: file.name })), directories: [] }, directory);
            }}
            onContextMenu={event => openMenu(event, null)}
            {...dropProps(directory, ".")}>
            {missing
                ? <Alert variant="warning" isInline title={t("There is no folder {{path}} in the store.", { path: directory })} />
                : (
                    <ListState loaded={{ ...list, data }} isEmpty={() => shown.length === 0 && !query.trim()}
                        emptyTitle={directory ? t("This folder is empty") : t("The store is empty")}
                        emptyBody={canUpload ? t("Upload files, or drop them here. A run can add files too, through fleet_store.") : t("Files are put here by operators, and by runs.")}>
                        {() => shown.length === 0
                            ? <Content component="p">{t("Nothing matches {{query}}.", { query: query.trim() })}</Content>
                            : (
                                <Table variant="compact" aria-label={t("Files of the store")}>
                                    <Thead>
                                        <Tr>
                                            <Th screenReaderText={t("Selected")}>
                                                <Checkbox id="store-select-all" aria-label={t("Select all")} isChecked={allShown ? true : chosen.length ? null : false}
                                                    onChange={() => setSelected(allShown ? new Set() : new Set(order))} />
                                            </Th>
                                            <Th sort={onSort("name", 1)}>{t("Name")}</Th>
                                            {found && <Th>{t("Folder")}</Th>}
                                            <Th sort={onSort("size", 2)}>{t("Size")}</Th>
                                            <Th>{t("Origin")}</Th>
                                            <Th sort={onSort("updated", 3)}>{t("Updated")}</Th>
                                            <Th screenReaderText={t("Actions")} />
                                        </Tr>
                                    </Thead>
                                    <Tbody>{shown.map(row)}</Tbody>
                                </Table>
                            )}
                    </ListState>
                )}
            {found && found.more > 0 && <Content component="p">{t("paths_more_count", { count: found.more })}</Content>}
            {(canUpload || canMove) && (
                <p className="fleet-store-hint">
                    {t("Drop files or folders from your computer here or onto a folder; drag entries onto a folder, or onto the path above, to move them, holding Ctrl to copy them. Keys: Ctrl+A, Ctrl+X or Ctrl+C and Ctrl+V, Delete, F2, / to search; the menu key or Shift+F10 for an entry's menu.")}
                </p>
            )}
        </div>
    );

    return (
        <>
            <SectionHeader title={t("Store")}>
                {canUpload && <Button variant="secondary" onClick={() => setDialog({ kind: "new-folder" })}>{t("New folder")}</Button>}
                {can("store.fetch") && <Button variant="secondary" onClick={() => setDialog({ kind: "fetch", again: null })}>{t("Download from a URL")}</Button>}
                {canUpload && <Button variant="secondary" onClick={() => pick(directory, true)}>{t("Upload a folder")}</Button>}
                {canUpload && <Button variant="primary" onClick={() => pick(directory, false)}>{t("Upload files")}</Button>}
            </SectionHeader>
            <input ref={chooser} type="file" multiple hidden aria-hidden
                onChange={event => {
                    const files = Array.from(event.target.files ?? []);
                    event.target.value = "";
                    if (files.length) startUploads({ files: files.map(file => ({ file, path: file.name })), directories: [] }, uploadInto.current);
                }} />
            <input ref={folderChooser} type="file" multiple hidden aria-hidden
                onChange={event => {
                    const files = Array.from(event.target.files ?? []);
                    event.target.value = "";
                    if (files.length) startUploads(chosenFolder(files), uploadInto.current);
                }} />
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
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem", whiteSpace: "pre-line" }} />}
            {notice && (
                <Alert variant="info" isInline title={notice.text} style={{ marginBottom: "1rem" }} timeout={notice.undo ? 15000 : 5000}
                    onTimeout={() => setNotice(null)}
                    actionLinks={notice.undo && <AlertActionLink onClick={() => { const undo = notice.undo!; setNotice(null); undo(); }}>{t("Undo")}</AlertActionLink>} />
            )}
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
            {(downloads.data?.downloads ?? []).length > 0 && (
                <Table variant="compact" aria-label={t("Downloads and copies into the store")} style={{ marginBottom: "1rem" }}>
                    <Thead><Tr><Th>{t("Into the store")}</Th><Th>{t("Origin")}</Th><Th width={30}>{t("Progress")}</Th><Th screenReaderText={t("Actions")} /></Tr></Thead>
                    <Tbody>
                        {(downloads.data?.downloads ?? []).slice(0, 10).map(item => (
                            <Tr key={item.run_id}>
                                <Td dataLabel={t("Into the store")}>
                                    <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", item.run_id])}>{item.path}</Button>
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
                                        <Button variant="link" onClick={() => act(() => call("run.cancel", { id: item.run_id }).then(downloads.reload))}>{t("Cancel")}</Button>
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
                            <FlexItem><Button variant="link" isInline onClick={() => act(() => call("store.discard", { path: upload.path }))}>{t("Discard")}</Button></FlexItem>
                        </Flex>
                    ))}
                </Alert>
            )}

            <Drawer isExpanded={!!detailedEntry} isInline>
                <DrawerContent panelContent={detailedEntry && data
                    ? (
                        <StoreDetails list={data} path={detailedEntry.path} onClose={() => setDetails(null)}
                            onTrack={can("store.track") ? every => act(() => call("store.track", { path: detailedEntry.path, every })) : undefined}
                            onRefresh={canRefresh ? () => checkSources([detailedEntry.path]) : undefined}
                            actions={[
                                detailedEntry.file && <Button key="download" variant="primary" size="sm" onClick={() => download(detailedEntry.path)}>{t("Download")}</Button>,
                                !detailedEntry.file && <Button key="open" variant="primary" size="sm" onClick={() => go(detailedEntry.path)}>{t("Open")}</Button>,
                                !detailedEntry.file && <Button key="archive" variant="secondary" size="sm" onClick={() => downloadMany([detailedEntry.path])}>{t("Download as an archive")}</Button>,
                                canMove && <Button key="rename" variant="secondary" size="sm" onClick={() => setDialog({ kind: "rename", path: detailedEntry.path })}>{t("Rename")}</Button>,
                                canMove && <Button key="move" variant="secondary" size="sm" onClick={() => setDialog({ kind: "move", paths: [detailedEntry.path] })}>{t("Move to…")}</Button>,
                                canDelete && <Button key="delete" variant="secondary" isDanger size="sm" onClick={() => setDialog({ kind: "delete", paths: [detailedEntry.path] })}>{t("Delete")}</Button>,
                            ]} />
                    )
                    : undefined}>
                    <DrawerContentBody>
                        {toolbar}
                        <Breadcrumb aria-label={t("Folders")} style={{ marginBottom: "0.5rem" }}>
                            <BreadcrumbItem>
                                <span className={`fleet-store-crumb${target === "" && directory !== "" ? " is-target" : ""}`} {...(directory ? dropProps("", "") : {})}>
                                    {parts.length ? <Button variant="link" isInline onClick={() => go("")}>{t("Store")}</Button> : t("Store")}
                                </span>
                            </BreadcrumbItem>
                            {parts.map((part, at) => {
                                const path = parts.slice(0, at + 1).join("/");
                                const last = at === parts.length - 1;
                                return (
                                    <BreadcrumbItem key={path} isActive={last}>
                                        <span className={`fleet-store-crumb${target === path && !last ? " is-target" : ""}`} {...(last ? {} : dropProps(path, path))}>
                                            {last ? part : <Button variant="link" isInline onClick={() => go(path)}>{part}</Button>}
                                        </span>
                                    </BreadcrumbItem>
                                );
                            })}
                        </Breadcrumb>
                        {listing}
                    </DrawerContentBody>
                </DrawerContent>
            </Drawer>
            {canUpload && (
                <AtTerminal text={shellLine(["fleet", "store", "put", "--file", "FILE", "--path", joinPath(directory, "FILE")])} />
            )}

            {menu && (
                <ContextMenu x={menu.x} y={menu.y} label={menu.entry ? t("Actions of {{name}}", { name: menu.entry.name }) : t("Actions of the folder")}
                    items={menu.entry ? entryActions(menu.entry) : listActions()} onClose={() => setMenu(null)} />
            )}
            {dialog?.kind === "fetch" && (
                <FetchModal directory={directory} again={dialog.again} list={data} onClose={() => setDialog(null)} onQueued={downloads.reload} />
            )}
            {dialog?.kind === "new-folder" && (
                <NewFolderModal directory={directory} onClose={() => setDialog(null)}
                    onSubmit={async path => {
                        await call("store.mkdir", { path });
                        setDialog(null);
                        list.reload();
                    }} />
            )}
            {dialog?.kind === "move" && (
                <RelocateModal paths={dialog.paths} copy={false} directories={data?.directories ?? []} onClose={() => setDialog(null)}
                    onSubmit={async into => {
                        setDialog(null);
                        moveInto(dialog.paths, into);
                    }} />
            )}
            {dialog?.kind === "copy" && (
                <RelocateModal paths={dialog.paths} copy directories={data?.directories ?? []} onClose={() => setDialog(null)}
                    onSubmit={async into => {
                        setDialog(null);
                        copyInto(dialog.paths, into);
                    }} />
            )}
            {dialog?.kind === "rename" && (
                <RenameModal path={dialog.path} onClose={() => setDialog(null)}
                    taken={new Set((index.get(dirname(dialog.path)) ?? []).map(entry => entry.name))}
                    onSubmit={async to => {
                        setDialog(null);
                        await runMoves([{ path: dialog.path, to }], true);
                    }} />
            )}
            {dialog?.kind === "delete" && (
                <ConfirmDelete name={dialog.paths.length === 1 ? basename(dialog.paths[0]) : t("files_count", { count: dialog.paths.length })}
                    what={dialog.paths.length > 1
                        ? (dialog.paths.some(path => data?.directories.includes(path))
                            ? t("{{what}} are deleted from the store, {{size}} in all, with everything in the folders among them. Runs under way keep what they were given until they end.",
                                { what: t("files_count", { count: dialog.paths.length }), size: formatBytes(data ? sizeOf(data, dialog.paths).bytes : 0) })
                            : t("{{what}} are deleted from the store, {{size}} in all. Runs under way keep them until they end.",
                                { what: t("files_count", { count: dialog.paths.length }), size: formatBytes(data ? sizeOf(data, dialog.paths).bytes : 0) }))
                        : data?.files.some(file => file.path === dialog.paths[0])
                            ? t("The file is deleted from the store. Runs under way keep it until they end.")
                            : t("The folder is deleted from the store with everything in it. Runs under way keep what they were given until they end.")}
                    onClose={() => setDialog(null)}
                    onDelete={async () => {
                        setDialog(null);
                        await remove(dialog.paths);
                    }} />
            )}
            {dialog?.kind === "upload-clash" && (
                <ConflictModal paths={dialog.clashing} canReplace onClose={() => setDialog(null)}
                    onChoose={clash => queue(settle(dialog.files, taken, clash))} />
            )}
            {dialog?.kind === "move-clash" && (
                <ConflictModal paths={dialog.clashing} canReplace={false} onClose={() => setDialog(null)}
                    onChoose={clash => settleMoves(dialog.moves, clash, dialog.copy)} />
            )}
        </>
    );
}

