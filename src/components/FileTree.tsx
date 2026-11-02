import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { IAction } from "@patternfly/react-table";
import { AngleDownIcon, AngleRightIcon, FileIcon, FolderIcon, FolderOpenIcon } from "@patternfly/react-icons";
import { buildTree, dirname, visibleNodes, type FileNode } from "../lib/files.ts";
import { ActionsColumn } from "./pf.tsx";

// What a drag of nodes of the tree carries: their paths, as JSON.
const DRAGGED = "application/x-cockpit-fleet-paths";

// The files of a project or an inventory as a tree. Not PatternFly's
// TreeView: it draws each node as a button, and Firefox starts no drag from
// inside a button. This one follows the tree pattern of WAI-ARIA: one node
// in the tab order, the arrow keys to move, Space to select, Enter to open.
export function FileTree({
    label, files, directories, open, onToggle, current, selected, onSelect, editable,
    actions, onOpen, onMove, onDropFiles, onRename, onDelete,
}: {
    label: string;
    files: string[];
    // Shown even with no file in them.
    directories: string[];
    open: Set<string>;
    onToggle: (path: string) => void;
    // The file shown beside the tree.
    current: string | null;
    selected: Set<string>;
    onSelect: (selected: Set<string>) => void;
    editable: boolean;
    actions: (node: FileNode) => IAction[];
    onOpen: (path: string) => void;
    // Drag and drop: nodes onto a directory ("" is the top), files from the desktop too.
    onMove: (paths: string[], into: string) => void;
    onDropFiles: (files: File[], into: string) => void;
    onRename: (path: string) => void;
    onDelete: (paths: string[]) => void;
}) {
    const { t } = useTranslation();
    const tree = useMemo(() => buildTree(files, directories), [files, directories]);
    const shown = useMemo(() => visibleNodes(tree, open), [tree, open]);
    const [focused, setFocused] = useState<string | null>(null);
    const [target, setTarget] = useState<string | null>(null);
    const items = useRef(new Map<string, HTMLLIElement>());

    // The node in the tab order: the one last focused, if still shown.
    const tabbable = shown.some(entry => entry.node.path === focused) ? focused : (current ?? shown[0]?.node.path ?? null);

    function focus(path: string | undefined) {
        if (path === undefined) return;
        setFocused(path);
        items.current.get(path)?.focus();
    }

    function toggleSelected(path: string) {
        const next = new Set(selected);
        if (next.has(path)) next.delete(path);
        else next.add(path);
        onSelect(next);
    }

    function activate(node: FileNode) {
        if (node.children) onToggle(node.path);
        else onOpen(node.path);
    }

    function keyDown(event: React.KeyboardEvent, node: FileNode) {
        const index = shown.findIndex(entry => entry.node.path === node.path);
        const isOpen = open.has(node.path);
        let handled = true;
        switch (event.key) {
        case "ArrowDown": focus(shown[index + 1]?.node.path); break;
        case "ArrowUp": focus(shown[index - 1]?.node.path); break;
        case "Home": focus(shown[0]?.node.path); break;
        case "End": focus(shown[shown.length - 1]?.node.path); break;
        case "ArrowRight":
            if (node.children && !isOpen) onToggle(node.path);
            else if (node.children?.length) focus(node.children[0].path);
            break;
        case "ArrowLeft":
            if (node.children && isOpen) onToggle(node.path);
            else if (dirname(node.path)) focus(dirname(node.path));
            break;
        case "Enter": activate(node); break;
        case " ": if (editable) toggleSelected(node.path); break;
        case "F2": if (editable) onRename(node.path); break;
        case "Delete": if (editable) onDelete(selected.has(node.path) ? [...selected] : [node.path]); break;
        default: handled = false;
        }
        if (handled) {
            event.preventDefault();
            event.stopPropagation();
        }
    }

    // Where a drag may end: a directory, or the top for "". Only nodes of
    // this tree, or files from outside it.
    function dropProps(into: string) {
        if (!editable) return {};
        return {
            onDragOver: (event: React.DragEvent) => {
                const types = event.dataTransfer.types;
                if (!types.includes(DRAGGED) && !types.includes("Files")) return;
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = types.includes(DRAGGED) ? "move" : "copy";
                setTarget(into);
            },
            onDragLeave: (event: React.DragEvent) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setTarget(null);
            },
            onDrop: (event: React.DragEvent) => {
                event.preventDefault();
                event.stopPropagation();
                setTarget(null);
                const dragged = event.dataTransfer.getData(DRAGGED);
                if (dragged) onMove(JSON.parse(dragged) as string[], into);
                else if (event.dataTransfer.files.length) onDropFiles([...event.dataTransfer.files], into);
            },
        };
    }

    function dragStart(event: React.DragEvent, node: FileNode) {
        const paths = selected.has(node.path) ? [...selected] : [node.path];
        event.dataTransfer.setData(DRAGGED, JSON.stringify(paths));
        event.dataTransfer.setData("text/plain", paths.join("\n"));
        event.dataTransfer.effectAllowed = "move";
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

    function render(nodes: FileNode[], level: number): React.ReactNode {
        return nodes.map(node => {
            const isDirectory = node.children !== undefined;
            const isOpen = isDirectory && open.has(node.path);
            const classes = ["fleet-tree-row"];
            if (node.path === current) classes.push("is-current");
            if (selected.has(node.path)) classes.push("is-selected");
            if (target === node.path) classes.push("is-target");
            return (
                <li key={node.path} role="treeitem" aria-level={level} aria-label={node.name} aria-expanded={isDirectory ? isOpen : undefined}
                    aria-selected={editable ? selected.has(node.path) : undefined}
                    aria-current={node.path === current ? "true" : undefined}
                    tabIndex={node.path === tabbable ? 0 : -1}
                    ref={element => { if (element) items.current.set(node.path, element); else items.current.delete(node.path); }}
                    onFocus={event => { if (event.target === event.currentTarget) setFocused(node.path); }}
                    onKeyDown={event => keyDown(event, node)}>
                    <div className={classes.join(" ")} style={{ paddingInlineStart: `${(level - 1) * 1.25 + 0.25}rem` }}
                        draggable={editable}
                        onDragStart={event => dragStart(event, node)}
                        {...(isDirectory ? dropProps(node.path) : {})}
                        onClick={event => {
                            setFocused(node.path);
                            if (editable && (event.ctrlKey || event.metaKey)) toggleSelected(node.path);
                            else activate(node);
                        }}>
                        <span className="fleet-tree-toggle" aria-hidden>
                            {isDirectory && (isOpen ? <AngleDownIcon /> : <AngleRightIcon />)}
                        </span>
                        {editable && (
                            <input type="checkbox" className="fleet-tree-check" tabIndex={-1} aria-hidden
                                checked={selected.has(node.path)}
                                onClick={event => event.stopPropagation()}
                                onChange={() => toggleSelected(node.path)} />
                        )}
                        <span className="fleet-tree-icon" aria-hidden>
                            {isDirectory ? (isOpen ? <FolderOpenIcon /> : <FolderIcon />) : <FileIcon />}
                        </span>
                        <span className="fleet-tree-name">{node.name}</span>
                        <span className="fleet-tree-actions" onClick={event => event.stopPropagation()}
                            onKeyDown={event => event.stopPropagation()}>
                            <ActionsColumn items={actions(node)} />
                        </span>
                    </div>
                    {isOpen && node.children!.length > 0 && <ul role="group">{render(node.children!, level + 1)}</ul>}
                </li>
            );
        });
    }

    return (
        <div className={`fleet-tree${target === "" ? " is-target" : ""}`} {...dropProps("")}>
            <ul role="tree" aria-label={label} aria-multiselectable={editable || undefined}>
                {render(tree, 1)}
            </ul>
            {editable && <p className="fleet-tree-hint">{t("Drag files and folders here to move them to the top, or files from your computer to upload them.")}</p>}
        </div>
    );
}
