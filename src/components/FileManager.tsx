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
    Progress,
    ProgressMeasureLocation,
    Toolbar,
    ToolbarContent,
    ToolbarGroup,
    ToolbarItem,
} from "@patternfly/react-core";
import { FileIcon, FolderIcon } from "@patternfly/react-icons";
import { Table, Tbody, Td, Th, Thead, Tr, type ThProps } from "@patternfly/react-table";
import { formatBytes } from "../lib/checks.ts";
import { basename, dirname, isInside, relocate, topmost } from "../lib/files.ts";
import { useStoredState, type Loaded } from "../lib/hooks.ts";
import {
    chosenFolder, describe, droppedOf, filterEntries, indexStore, joinPath, moveInList, rangeBetween, readStoreSort, removeFromList, searchStore, settle, sizeOf, sortEntries,
    type Clash, type Dropped, type Entry, type Listed, type Listing, type StoreSortKey, type Upload,
} from "../lib/store.ts";
import { ConfirmDelete, ListState, message } from "./common.tsx";
import { ContextMenu, type MenuEntry } from "./ContextMenu.tsx";
import { RelocateModal } from "./RelocateModal.tsx";
import { ConflictModal, NewFolderModal, RenameModal } from "./StoreDialogs.tsx";
import { ActionsColumn, Alert, SearchInput } from "./pf.tsx";

// A list of files and folders that works as a file manager does: many
// entries selected, dragged onto a folder (copied with Ctrl), cut or copied
// and pasted, uploaded from the desktop, with a menu on each; all of it can
// be done from the keyboard and the menus too. What it lists, and how each
// change is made, is the page's: the store, the documents.

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

export interface Move {
    path: string;
    to: string;
}

// How the page makes each change. Each throws what the backend said.
export interface FileOps {
    move(moves: Move[]): Promise<void>;
    // Copies made at once can be undone; a copy that goes on elsewhere says so.
    copy(copies: Move[]): Promise<{ made?: string[]; notice?: string }>;
    remove(paths: string[]): Promise<void>;
    mkdir(path: string): Promise<void>;
    // An upload begun, or null when STILL says it was cancelled meanwhile.
    upload(file: File, path: string, replace: boolean, onProgress: (sent: number) => void, still: () => boolean): Promise<Upload | null>;
    download(path: string): Promise<unknown>;
    // Folders, or many entries, in one archive.
    downloadMany?: (paths: string[]) => Promise<void>;
}

export interface FileColumn<F extends Listed> {
    title: string;
    sort?: StoreSortKey;
    cell: (entry: Entry<F>) => React.ReactNode;
}

// What the page can do with the list from outside it: its buttons, its panel.
export interface FileTools {
    pick(into: string, folder: boolean): void;
    newFolder(): void;
    rename(path: string): void;
    moveTo(paths: string[]): void;
    copyTo(paths: string[]): void;
    remove(paths: string[]): void;
    showDetails(path: string | null): void;
    report(error: string | null): void;
    notify(text: string): void;
    // An action whose failure is reported, after which the list is read again.
    act(action: () => Promise<unknown>): Promise<void>;
}

export interface FileWords {
    // The top of the tree, in the path: "Store".
    root: string;
    // The top of the tree in a sentence: "the top of the store".
    top: string;
    searchAll: string;
    inAll: string;
    listLabel: string;
    empty: string;
    emptyFolder: string;
    emptyBody: string;
    missing: string;
    hint: string;
    // When the tree holds more than is listed.
    incomplete: string;
    clashTitle?: string;
    clashIntro?: string;
}

interface Props<F extends Listed> {
    // Names the ids of the page's elements and what a drag carries.
    kind: string;
    directory: string;
    go: (path: string) => void;
    list: Loaded<Listing<F>>;
    // What is shown: the listing, or a fresher one the page found.
    data: Listing<F> | null;
    reload: () => void;
    can: { upload: boolean; move: boolean; copy: boolean; delete: boolean };
    ops: FileOps;
    words: FileWords;
    columns: FileColumn<F>[];
    badges?: (file: F) => React.ReactNode;
    // A file opened: a click on its name, a double click on its row.
    openFile: (path: string, tools: FileTools) => void;
    // What the menus offer of one file beside downloading it.
    fileActions?: (entry: Entry<F>, tools: FileTools) => MenuEntry[];
    // What the toolbar offers for what is selected, beside the usual.
    toolbar?: (chosen: string[], tools: FileTools) => React.ReactNode;
    deleteText: (paths: string[], data: Listing<F> | null) => string;
    dragText?: (paths: string[]) => string;
    // The panel beside the list for one entry.
    details?: (entry: Entry<F>, tools: FileTools) => React.ReactNode;
    afterUploads?: () => void;
    // The path being uploaded now, or null: the page may list it elsewhere.
    onSending?: (path: string | null) => void;
    // Whether a dialog of the list is open: the page holds its polling meanwhile.
    onDialog?: (open: boolean) => void;
    // Shown between the uploads and the list: the page's own tables.
    between?: React.ReactNode;
    tools?: React.MutableRefObject<FileTools | null>;
}

type Dialog =
    | { kind: "new-folder" }
    | { kind: "move"; paths: string[] }
    | { kind: "copy"; paths: string[] }
    | { kind: "rename"; path: string }
    | { kind: "delete"; paths: string[] }
    | { kind: "upload-clash"; files: { file: File; path: string }[]; clashing: string[] }
    | { kind: "move-clash"; moves: Move[]; clashing: string[]; copy: boolean };

// Entries found in the whole tree shown at most.
const FOUND = 500;

function isTyping(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    if (target.isContentEditable || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
    return target instanceof HTMLInputElement && !["checkbox", "radio", "button"].includes(target.type);
}

export function FileManager<F extends Listed>(props: Props<F>) {
    const { kind, directory, go, list, reload, can, ops, words, columns } = props;
    const { t } = useTranslation();
    // What a drag of entries carries: their paths, as JSON.
    const DRAGGED = `application/x-cockpit-fleet-${kind}`;
    const [uploads, setUploads] = useState<UploadItem[]>([]);
    const [dialog, setDialog] = useState<Dialog | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ text: string; undo?: () => void } | null>(null);
    // A change shown before the backend has answered, until its next listing.
    const [overlay, setOverlay] = useState<Listing<F> | null>(null);
    const [query, setQuery] = useState("");
    const [everywhere, setEverywhere] = useState(false);
    const [sort, setSort] = useStoredState(`${kind}-sort`, readStoreSort);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [anchor, setAnchor] = useState<string | null>(null);
    // Entries cut or copied, to be pasted elsewhere: a clipboard of the page.
    const [clip, setClip] = useState<{ paths: string[]; copy: boolean } | null>(null);
    const cut = clip && !clip.copy ? clip.paths : [];
    const [details, setDetails] = useState<string | null>(null);
    const [menu, setMenu] = useState<{ x: number; y: number; entry: Entry<F> | null } | null>(null);
    // Where a drag is: a folder's path, or null; "." for the list itself.
    const [target, setTarget] = useState<string | null>(null);
    const chooser = useRef<HTMLInputElement>(null);
    const folderChooser = useRef<HTMLInputElement>(null);
    const uploadInto = useRef("");
    const current = useRef<Upload | null>(null);
    const nextKey = useRef(1);
    const searchId = `${kind}-search`;

    useEffect(() => {
        setOverlay(null);
    }, [props.data]);
    const data = overlay ?? props.data;

    useEffect(() => {
        props.onDialog?.(!!dialog);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [dialog]);

    const index = useMemo(() => (data ? indexStore(data) : new Map<string, Entry<F>[]>()), [data]);
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
            document.getElementById(searchId)?.focus();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [dialog, menu, searchId]);

    function update(key: number, change: Partial<UploadItem>) {
        setUploads(items => items.map(item => item.key === key ? { ...item, ...change } : item));
    }

    // One upload at a time, in the order they were asked for.
    const uploadsLeft = useRef(0);
    // Cancelled while it was being got ready, before it had anything to stop.
    const cancelled = useRef(new Set<number>());
    uploadsLeft.current = uploads.filter(item => item.state === "waiting").length;
    const sending = uploads.some(item => item.state === "sending");
    const next = uploads.find(item => item.state === "waiting");
    useEffect(() => {
        if (sending || !next) return;
        const item = next;
        update(item.key, { state: "sending" });
        props.onSending?.(item.path);
        (async () => {
            try {
                const upload = await ops.upload(item.file, item.path, item.replace, sent => update(item.key, { sent }),
                    () => !cancelled.current.has(item.key));
                if (!upload) return;
                current.current = upload;
                await upload.done;
                // An earlier attempt at the same file is done with too.
                setUploads(items => items.filter(other => other.key === item.key || other.path !== item.path || other.state === "waiting" || other.state === "sending")
                    .map(other => other.key === item.key ? { ...other, state: "done", sent: item.file.size } : other));
            } catch (ex) {
                setUploads(items => items.map(other => other.key !== item.key ? other
                    : { ...other, state: other.state === "cancelled" ? "cancelled" : "failed", error: describe(ex) }));
            } finally {
                current.current = null;
                props.onSending?.(null);
                // The list follows the uploads, but not after each of a hundred small files.
                if (!uploadsLeft.current) reload();
                props.afterUploads?.();
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
        if (!can.upload) return;
        setError(null);
        const files = dropped.files.map(item => ({ file: item.file, path: joinPath(into, item.path) }));
        try {
            for (const path of dropped.directories) await ops.mkdir(joinPath(into, path));
        } catch (ex) {
            setError(message(ex));
            return;
        }
        if (dropped.directories.length && !files.length) reload();
        const clashing = files.filter(item => taken.has(item.path)).map(item => item.path);
        if (clashing.length) setDialog({ kind: "upload-clash", files, clashing });
        else queue(settle(files, taken, "skip"));
    }

    function pick(into: string, folder: boolean) {
        uploadInto.current = into;
        (folder ? folderChooser : chooser).current?.click();
    }

    function cancel(item: UploadItem) {
        cancelled.current.add(item.key);
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
        reload();
        props.afterUploads?.();
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
            await ops.move(moves);
        } catch (ex) {
            setOverlay(null);
            setError(message(ex));
            reload();
            return;
        }
        if (details) {
            const moved = moves.find(move => isInside(details, move.path));
            if (moved) setDetails(moved.to + details.slice(moved.path.length));
        }
        const what = moves.length === 1 ? basename(moves[0].path) : t("files_count", { count: moves.length });
        // Kept beside one with the same name: the name it has now.
        const named = moves.length === 1 ? basename(moves[0].to) : what;
        const folder = dirname(moves[0].to) || words.top;
        const back = moves.map(move => ({ path: move.to, to: move.path })).reverse();
        setNotice({
            text: !undoable ? t("Move undone.")
                : dirname(moves[0].path) === dirname(moves[0].to) && moves.length === 1 ? t("{{what}} renamed to {{name}}.", { what, name: basename(moves[0].to) })
                    : t("{{what}} moved to {{folder}}.", { what: named, folder }),
            undo: undoable ? () => runMoves(back, false) : undefined,
        });
        reload();
    }

    // Copies in one call; those made at once can be undone (deleted).
    async function runCopies(copies: Move[]) {
        if (!copies.length) return;
        setError(null);
        setNotice(null);
        setSelected(new Set());
        setClip(null);
        try {
            const done = await ops.copy(copies);
            if (done.notice) {
                setNotice({ text: done.notice });
            } else {
                const made = done.made ?? [];
                const what = made.length === 1 ? basename(made[0]) : t("files_count", { count: made.length });
                setNotice({
                    text: t("{{what}} copied to {{folder}}.", { what, folder: dirname(copies[0].to) || words.top }),
                    undo: () => remove(made, t("Copy undone.")),
                });
            }
        } catch (ex) {
            setError(message(ex));
        }
        reload();
    }

    function paste(into: string) {
        if (!clip) return;
        if (clip.copy) copyInto(clip.paths, into);
        else moveInto(clip.paths, into);
    }

    // Entries into the folder INTO: by a drag, a paste, or "Move to…".
    function moveInto(paths: string[], into: string) {
        if (!can.move) return;
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
        if (!can.copy) return;
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
            await ops.remove(gone);
            setError(null);
            if (done) setNotice({ text: done });
        } catch (ex) {
            setOverlay(null);
            setError(message(ex));
        }
        reload();
    }

    const tools: FileTools = {
        pick,
        newFolder: () => setDialog({ kind: "new-folder" }),
        rename: path => setDialog({ kind: "rename", path }),
        moveTo: paths => setDialog({ kind: "move", paths }),
        copyTo: paths => setDialog({ kind: "copy", paths }),
        remove: paths => setDialog({ kind: "delete", paths }),
        showDetails: setDetails,
        report: setError,
        notify: text => setNotice({ text }),
        act,
    };
    if (props.tools) props.tools.current = tools;

    const download = (path: string) => act(() => ops.download(path));
    const downloadMany = ops.downloadMany && ((paths: string[]) => act(() => ops.downloadMany!(topmost(paths))));

    function select(entry: Entry<F>, event: React.MouseEvent) {
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
    function entryActions(entry: Entry<F>): MenuEntry[] {
        const many = selected.has(entry.path) && chosen.length > 1 ? chosen : null;
        const paths = many ?? [entry.path];
        const items: MenuEntry[] = [];
        if (many && downloadMany) items.push({ title: t("Download as an archive"), onClick: () => downloadMany(many) });
        if (!many) {
            if (entry.file) {
                items.push({ title: t("Download"), onClick: () => download(entry.path) });
                items.push(...(props.fileActions?.(entry, tools) ?? []));
            } else {
                items.push({ title: t("Open"), onClick: () => go(entry.path) });
                if (downloadMany) items.push({ title: t("Download as an archive"), onClick: () => downloadMany([entry.path]) });
                if (clip && (clip.copy ? can.copy : can.move)) items.push({ title: t("Paste into this folder"), onClick: () => paste(entry.path) });
                if (can.upload) {
                    items.push({ title: t("Upload files here"), onClick: () => pick(entry.path, false) });
                    items.push({ title: t("Upload a folder here"), onClick: () => pick(entry.path, true) });
                }
            }
            if (props.details) items.push({ title: t("Details"), onClick: () => setDetails(entry.path) });
        }
        if (can.move || can.copy) {
            if (items.length) items.push({ isSeparator: true });
            if (can.move && !many) items.push({ title: t("Rename"), onClick: () => setDialog({ kind: "rename", path: entry.path }) });
            if (can.move) items.push({ title: t("Move to…"), onClick: () => setDialog({ kind: "move", paths }) });
            if (can.copy) items.push({ title: t("Copy to…"), onClick: () => setDialog({ kind: "copy", paths }) });
            if (can.move) items.push({ title: t("Cut"), onClick: () => setClip({ paths, copy: false }) });
            if (can.copy) items.push({ title: t("Copy"), onClick: () => setClip({ paths, copy: true }) });
        }
        if (can.delete) {
            items.push({ isSeparator: true });
            items.push({ title: t("Delete"), isDanger: true, onClick: () => setDialog({ kind: "delete", paths }) });
        }
        return items;
    }

    // What the menu of the list itself offers, away from any entry.
    function listActions(): MenuEntry[] {
        const items: MenuEntry[] = [];
        if (clip && (clip.copy ? can.copy : can.move)) items.push({ title: t("Paste here"), onClick: () => paste(directory) });
        if (can.upload) {
            items.push({ title: t("New folder"), onClick: () => setDialog({ kind: "new-folder" }) });
            items.push({ title: t("Upload files"), onClick: () => pick(directory, false) });
            items.push({ title: t("Upload a folder"), onClick: () => pick(directory, true) });
        }
        if (shown.length) items.push({ title: t("Select all"), onClick: () => setSelected(new Set(order)) });
        return items;
    }

    // A menu opened from the keyboard (the menu key, Shift+F10) comes as a
    // contextmenu event too, with no place of the pointer: then below the row.
    function openMenu(event: React.MouseEvent, entry: Entry<F> | null) {
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
        else if (control && key === "x" && can.move && chosen.length) setClip({ paths: chosen, copy: false });
        // Text selected on the page is copied as text, as usual.
        else if (control && key === "c" && can.copy && chosen.length && !window.getSelection()?.toString()) setClip({ paths: chosen, copy: true });
        else if (control && key === "v" && clip && !found) paste(directory);
        else if (event.key === "Delete" && can.delete && chosen.length) setDialog({ kind: "delete", paths: chosen });
        else if (event.key === "F2" && can.move && chosen.length === 1) setDialog({ kind: "rename", path: chosen[0] });
        else if (event.key === "Escape" && (chosen.length || clip)) {
            setSelected(new Set());
            setClip(null);
        } else handled = false;
        if (handled) {
            event.preventDefault();
            event.stopPropagation();
        }
    }

    // Whether Ctrl (Alt on a Mac) is down. Chrome on Linux says so on the
    // keys and on dragstart, but not on dragover and drop: there it is
    // always false, and its dropEffect is move.
    const copyKey = useRef(false);
    useEffect(() => {
        const keys = (event: KeyboardEvent) => { copyKey.current = event.ctrlKey || event.altKey; };
        const blur = () => { copyKey.current = false; };
        window.addEventListener("keydown", keys, true);
        window.addEventListener("keyup", keys, true);
        window.addEventListener("blur", blur);
        return () => {
            window.removeEventListener("keydown", keys, true);
            window.removeEventListener("keyup", keys, true);
            window.removeEventListener("blur", blur);
        };
    }, []);
    const copying = (event: React.DragEvent) => event.ctrlKey || event.altKey || copyKey.current;

    // Where a drag may end: folders take entries of the list and files of
    // the desktop; the list itself, files into the folder shown.
    function dropProps(into: string, mark: string) {
        return {
            onDragOver: (event: React.DragEvent) => {
                const types = event.dataTransfer.types;
                const copy = copying(event);
                const entries = types.includes(DRAGGED) && (copy ? can.copy : can.move) && mark !== ".";
                const files = types.includes("Files") && can.upload;
                if (!entries && !files) return;
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = entries && !copy ? "move" : "copy";
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
                    if (copying(event)) copyInto(paths, into);
                    else moveInto(paths, into);
                } else {
                    // The entries must be taken while the drop is under way.
                    droppedOf(event.dataTransfer).then(dropped => startUploads(dropped, into), ex => setError(message(ex)));
                }
            },
        };
    }

    function dragStart(event: React.DragEvent, entry: Entry<F>) {
        copyKey.current = event.ctrlKey || event.altKey;
        const paths = selected.has(entry.path) ? chosen : [entry.path];
        event.dataTransfer.setData(DRAGGED, JSON.stringify(paths));
        event.dataTransfer.setData("text/plain", props.dragText ? props.dragText(paths) : paths.join("\n"));
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

    const sortable = ["name", ...columns.filter(column => column.sort).map(column => column.sort!)];
    const onSort = (by: StoreSortKey): ThProps["sort"] => found ? undefined : {
        columnIndex: sortable.indexOf(by) + 1,
        sortBy: { index: sortable.indexOf(sort.by) + 1, direction: sort.ascending ? "asc" : "desc" },
        onSort: (_event, _index, direction) => setSort({ by, ascending: direction === "asc" }),
    };

    const parts = directory ? directory.split("/") : [];
    const detailed = details && data && taken.has(details) ? details : null;
    const detailedEntry: Entry<F> | null = detailed ? { name: basename(detailed), path: detailed, file: data!.files.find(file => file.path === detailed) ?? null } : null;
    const allShown = order.length > 0 && chosen.length === order.length;
    const what = (paths: string[]) => paths.length === 1 ? basename(paths[0]) : t("files_count", { count: paths.length });

    const toolbar = (
        <Toolbar className="fleet-store-toolbar" inset={{ default: "insetNone" }}>
            <ToolbarContent>
                <ToolbarItem>
                    <SearchInput searchInputId={searchId} value={query} placeholder={everywhere ? words.searchAll : t("Filter this folder")}
                        onChange={(_e, value) => setQuery(value)} onClear={() => setQuery("")} />
                </ToolbarItem>
                <ToolbarItem alignSelf="center">
                    <Checkbox id={`${kind}-everywhere`} label={words.inAll} isChecked={everywhere} onChange={(_e, value) => setEverywhere(value)} />
                </ToolbarItem>
                {chosen.length > 0 && chosenSize && (
                    <ToolbarGroup>
                        <ToolbarItem alignSelf="center">
                            <span><strong>{t("files_selected", { count: chosen.length })}</strong>{chosenSize.bytes > 0 && `, ${formatBytes(chosenSize.bytes)}`}</span>
                        </ToolbarItem>
                        {chosen.length === 1 && data?.files.some(file => file.path === chosen[0])
                            ? <ToolbarItem><Button variant="secondary" size="sm" onClick={() => download(chosen[0])}>{t("Download")}</Button></ToolbarItem>
                            : downloadMany && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => downloadMany(chosen)}>{t("Download as an archive")}</Button></ToolbarItem>}
                        {props.toolbar?.(chosen, tools)}
                        {can.move && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "move", paths: chosen })}>{t("Move to…")}</Button></ToolbarItem>}
                        {can.copy && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "copy", paths: chosen })}>{t("Copy to…")}</Button></ToolbarItem>}
                        {can.move && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setClip({ paths: chosen, copy: false })}>{t("Cut")}</Button></ToolbarItem>}
                        {can.copy && <ToolbarItem><Button variant="secondary" size="sm" onClick={() => setClip({ paths: chosen, copy: true })}>{t("Copy")}</Button></ToolbarItem>}
                        {can.delete && <ToolbarItem><Button variant="secondary" isDanger size="sm" onClick={() => setDialog({ kind: "delete", paths: chosen })}>{t("Delete")}</Button></ToolbarItem>}
                        <ToolbarItem><Button variant="link" size="sm" onClick={() => setSelected(new Set())}>{t("Clear the selection")}</Button></ToolbarItem>
                    </ToolbarGroup>
                )}
                {clip && (clip.copy ? can.copy : can.move) && (
                    <ToolbarGroup>
                        <ToolbarItem>
                            <Button variant="primary" size="sm" isDisabled={!!found} onClick={() => paste(directory)}>
                                {clip.copy ? t("Paste a copy here: {{what}}", { what: what(clip.paths) }) : t("Paste here: {{what}}", { what: what(clip.paths) })}
                            </Button>
                        </ToolbarItem>
                        <ToolbarItem><Button variant="link" size="sm" onClick={() => setClip(null)}>{t("Cancel")}</Button></ToolbarItem>
                    </ToolbarGroup>
                )}
            </ToolbarContent>
        </Toolbar>
    );

    function row(entry: Entry<F>, rowIndex: number) {
        const file = entry.file;
        const isSelected = selected.has(entry.path);
        const classes = [];
        if (cut.includes(entry.path)) classes.push("is-cut");
        if (target === entry.path && !file) classes.push("is-target");
        const actions = entryActions(entry);
        const open = () => (file ? props.openFile(entry.path, tools) : go(entry.path));
        return (
            <Tr key={entry.path} className={classes.join(" ") || undefined} isRowSelected={isSelected}
                draggable={can.move || can.copy} onDragStart={event => dragStart(event, entry)}
                {...(file ? {} : dropProps(entry.path, entry.path))}
                onClick={event => { if (!(event.target as HTMLElement).closest("button, input, a")) select(entry, event); }}
                onDoubleClick={event => { if (!(event.target as HTMLElement).closest("button, input, a")) open(); }}
                onContextMenu={event => openMenu(event, entry)}>
                <Td>
                    <Checkbox id={`${kind}-select-${rowIndex}`} aria-label={t("Select {{name}}", { name: entry.name })} isChecked={isSelected}
                        onChange={() => toggle(entry.path)} />
                </Td>
                <Td dataLabel={t("Name")}>
                    <Button variant="link" isInline icon={file ? <FileIcon /> : <FolderIcon />}
                        onClick={event => { if (event.shiftKey || event.ctrlKey || event.metaKey) select(entry, event); else open(); }}>
                        {entry.name}
                    </Button>
                    {file && props.badges?.(file)}
                </Td>
                {found && <Td dataLabel={t("Folder")}>
                    <Button variant="link" isInline onClick={() => go(dirname(entry.path))}>{dirname(entry.path) || words.root}</Button>
                </Td>}
                {columns.map(column => <Td key={column.title} dataLabel={column.title}>{column.cell(entry)}</Td>)}
                <Td isActionCell>{actions.length > 0 && <ActionsColumn items={actions.map(item => ("isSeparator" in item ? { isSeparator: true } : item))} />}</Td>
            </Tr>
        );
    }

    const listing = (
        <div ref={listBox} className={`fleet-store-list${target === "." ? " is-target" : ""}`} tabIndex={-1}
            onPaste={event => {
                if (isTyping(event.target) || !can.upload || !event.clipboardData.files.length) return;
                event.preventDefault();
                startUploads({ files: Array.from(event.clipboardData.files).map(file => ({ file, path: file.name })), directories: [] }, directory);
            }}
            onContextMenu={event => openMenu(event, null)}
            {...dropProps(directory, ".")}>
            {missing
                ? <Alert variant="warning" isInline title={words.missing} />
                : (
                    <ListState loaded={{ ...list, data }} isEmpty={() => shown.length === 0 && !query.trim()}
                        emptyTitle={directory ? t("This folder is empty") : words.empty}
                        emptyBody={words.emptyBody}>
                        {() => shown.length === 0
                            ? <Content component="p">{t("Nothing matches {{query}}.", { query: query.trim() })}</Content>
                            : (
                                <Table variant="compact" aria-label={words.listLabel}>
                                    <Thead>
                                        <Tr>
                                            <Th screenReaderText={t("Selected")}>
                                                <Checkbox id={`${kind}-select-all`} aria-label={t("Select all")} isChecked={allShown ? true : chosen.length ? null : false}
                                                    onChange={() => setSelected(allShown ? new Set() : new Set(order))} />
                                            </Th>
                                            <Th sort={onSort("name")}>{t("Name")}</Th>
                                            {found && <Th>{t("Folder")}</Th>}
                                            {columns.map(column => <Th key={column.title} sort={column.sort ? onSort(column.sort) : undefined}>{column.title}</Th>)}
                                            <Th screenReaderText={t("Actions")} />
                                        </Tr>
                                    </Thead>
                                    <Tbody>{shown.map(row)}</Tbody>
                                </Table>
                            )}
                    </ListState>
                )}
            {found && found.more > 0 && <Content component="p">{t("paths_more_count", { count: found.more })}</Content>}
            {(can.upload || can.move) && <p className="fleet-store-hint">{words.hint}</p>}
        </div>
    );

    return (
        <>
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
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem", whiteSpace: "pre-line" }} />}
            {notice && (
                <Alert variant="info" isInline title={notice.text} style={{ marginBottom: "1rem" }} timeout={notice.undo ? 15000 : 5000}
                    onTimeout={() => setNotice(null)}
                    actionLinks={notice.undo && <AlertActionLink onClick={() => { const undo = notice.undo!; setNotice(null); undo(); }}>{t("Undo")}</AlertActionLink>} />
            )}
            {props.list.data && !props.list.data.complete && <Alert variant="warning" isInline title={words.incomplete} style={{ marginBottom: "1rem" }} />}

            {uploads.length > 0 && (
                <Table variant="compact" aria-label={t("Uploads")} style={{ marginBottom: "1rem" }}>
                    <Thead>
                        <Tr>
                            <Th>{t("Uploading")}</Th><Th width={40}>{t("Progress")}</Th>
                            {sending || next
                                ? <Th screenReaderText={t("Actions")} />
                                : <Th modifier="fitContent"><Button variant="link" isInline onClick={() => setUploads([])}>{t("Close")}</Button></Th>}
                        </Tr>
                    </Thead>
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
                                        <>
                                            <Button variant="link" onClick={() => update(item.key, { state: "waiting", error: undefined })}>{t("Resume")}</Button>
                                            <Button variant="link" onClick={() => setUploads(items => items.filter(other => other.key !== item.key))}>{t("Remove from the list")}</Button>
                                        </>
                                    )}
                                </Td>
                            </Tr>
                        ))}
                    </Tbody>
                </Table>
            )}
            {props.between}

            <Drawer isExpanded={!!detailedEntry && !!props.details} isInline>
                <DrawerContent panelContent={detailedEntry && props.details ? props.details(detailedEntry, tools) : undefined}>
                    <DrawerContentBody>
                        {toolbar}
                        <Breadcrumb aria-label={t("Folders")} style={{ marginBottom: "0.5rem" }}>
                            <BreadcrumbItem>
                                <span className={`fleet-store-crumb${target === "" && directory !== "" ? " is-target" : ""}`} {...(directory ? dropProps("", "") : {})}>
                                    {parts.length ? <Button variant="link" isInline onClick={() => go("")}>{words.root}</Button> : words.root}
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

            {menu && (
                <ContextMenu x={menu.x} y={menu.y} label={menu.entry ? t("Actions of {{name}}", { name: menu.entry.name }) : t("Actions of the folder")}
                    items={menu.entry ? entryActions(menu.entry) : listActions()} onClose={() => setMenu(null)} />
            )}
            {dialog?.kind === "new-folder" && (
                <NewFolderModal directory={directory} onClose={() => setDialog(null)}
                    onSubmit={async path => {
                        await ops.mkdir(path);
                        setDialog(null);
                        reload();
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
                <ConfirmDelete name={what(dialog.paths)} what={props.deleteText(dialog.paths, data)}
                    onClose={() => setDialog(null)}
                    onDelete={async () => {
                        setDialog(null);
                        await remove(dialog.paths);
                    }} />
            )}
            {dialog?.kind === "upload-clash" && (
                <ConflictModal paths={dialog.clashing} canReplace title={words.clashTitle} intro={words.clashIntro} onClose={() => setDialog(null)}
                    onChoose={clash => queue(settle(dialog.files, taken, clash))} />
            )}
            {dialog?.kind === "move-clash" && (
                <ConflictModal paths={dialog.clashing} canReplace={false} title={words.clashTitle} intro={words.clashIntro} onClose={() => setDialog(null)}
                    onChoose={clash => settleMoves(dialog.moves, clash, dialog.copy)} />
            )}
        </>
    );
}
