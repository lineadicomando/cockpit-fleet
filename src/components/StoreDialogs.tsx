import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Checkbox,
    Content,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    DrawerActions,
    DrawerCloseButton,
    DrawerHead,
    DrawerPanelBody,
    DrawerPanelContent,
    Flex,
    FormSelect,
    FormSelectOption,
    FlexItem,
    FormGroup,
    FormHelperText,
    HelperText,
    HelperTextItem,
    Label,
    ModalVariant,
    Radio,
    TextInput,
    Title,
} from "@patternfly/react-core";
import cockpit from "cockpit";
import { formatBytes } from "../lib/checks.ts";
import { basename, dirname, type PathChoice } from "../lib/files.ts";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { EVERY, joinPath, nameFromUrl, playbookPath, sizeOf, type Clash, type Every, type StoreDownload, type StoreFile, type StoreList } from "../lib/store.ts";
import { FormModal } from "./common.tsx";
import { PathInput } from "./PathInput.tsx";
import { ClipboardCopy } from "./pf.tsx";

export function Origin({ file }: { file: StoreFile }) {
    const { t } = useTranslation();
    if (file.origin === "run") {
        return <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", file.source])}>{t("Run {{id}}", { id: file.source })}</Button>;
    }
    if (file.origin === "upload") return <>{t("Uploaded")}</>;
    if (file.origin === "url") return <span title={file.source}>{t("Downloaded")}</span>;
    if (file.origin === "copy") return <span title={file.source}>{t("Copied")}</span>;
    return <>{t("Put there by hand")}</>;
}

// What a folder field of the store offers: its folders.
export function folderChoices(list: StoreList | null): PathChoice[] {
    return (list?.directories ?? []).map(path => ({ path, directory: true }));
}

const trimSlashes = (path: string) => path.trim().replace(/^\/+|\/+$/g, "");

export function useEveryLabels(): Record<Every, string> {
    const { t } = useTranslation();
    return { never: t("Never"), daily: t("Every day"), weekly: t("Every week"), monthly: t("Every month") };
}

// A download from a URL, queued for a job that runs beside the playbooks:
// into a folder picked from those of the store, under a name that follows
// the URL until it is written by hand.
export function FetchModal({ directory, again, list, onClose, onQueued }: {
    directory: string;
    again: StoreDownload | null;
    list: StoreList | null;
    onClose: () => void;
    onQueued: () => void;
}) {
    const { t } = useTranslation();
    const [url, setUrl] = useState(again?.url ?? "");
    const [folder, setFolder] = useState(again ? dirname(again.path) : directory);
    const [name, setName] = useState(again ? basename(again.path) : "");
    const [named, setNamed] = useState(!!again);
    const [sha256, setSha256] = useState(again?.sha256 ?? "");
    const [replace, setReplace] = useState(again?.replace ?? false);
    const [every, setEvery] = useState<Every>(again?.every ?? "never");
    const everyLabels = useEveryLabels();
    const digestValid = !sha256.trim() || /^[0-9a-fA-F]{64}$/.test(sha256.trim());
    const nameValid = !name.includes("/");
    const path = joinPath(trimSlashes(folder), name.trim());
    const present = !replace && (list?.files ?? []).some(file => file.path === path);

    function changeUrl(value: string) {
        setUrl(value);
        if (!named) setName(nameFromUrl(value.trim()));
    }

    return (
        <FormModal title={t("Download from a URL")} submitLabel={t("Download")} onClose={onClose}
            isDisabled={!url.trim() || !name.trim() || !nameValid || !digestValid}
            onSubmit={async () => {
                // What a download that ended short left is taken up, if it is the same URL and path.
                const resume = again && again.partial && again.url === url.trim() && again.path === path ? again.run_id : "";
                await call("store.fetch", { url: url.trim(), path, sha256: sha256.trim(), replace, every, resume });
                onQueued();
                onClose();
            }}>
            <FormGroup label={t("URL")} isRequired fieldId="fetch-url">
                <TextInput id="fetch-url" value={url} onChange={(_e, value) => changeUrl(value)} placeholder="https://example.org/setup.msi" isRequired />
                <FormHelperText><HelperText><HelperTextItem>{t("http or https. Addresses of this server and link-local ones are refused.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            <FormGroup label={t("Folder")} fieldId="fetch-folder">
                <PathInput id="fetch-folder" label={t("Folder")} value={folder} onChange={setFolder} choices={folderChoices(list)}
                    placeholder={t("The top")} emptyText={t("No folder to suggest")} toggleLabel={t("Show the folders")} />
                <FormHelperText><HelperText><HelperTextItem>{t("Empty for the top. A folder that does not exist is created.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            <FormGroup label={t("Name")} isRequired fieldId="fetch-name">
                <TextInput id="fetch-name" value={name} onChange={(_e, value) => { setName(value); setNamed(true); }} isRequired
                    validated={nameValid && !present ? "default" : "error"} />
                {(!nameValid || present) && (
                    <FormHelperText><HelperText><HelperTextItem variant="error">
                        {!nameValid ? t("A name, without /: the folder is chosen above.") : t("There is a file with this name in the folder already.")}
                    </HelperTextItem></HelperText></FormHelperText>
                )}
            </FormGroup>
            <FormGroup label={t("SHA-256")} fieldId="fetch-sha">
                <TextInput id="fetch-sha" value={sha256} onChange={(_e, value) => setSha256(value)} validated={digestValid ? "default" : "error"} />
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem variant={digestValid ? "default" : "error"}>
                            {t("Optional: the download fails when the file has another digest.")}
                        </HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
            <FormGroup label={t("Check for a newer version")} fieldId="fetch-every">
                <FormSelect id="fetch-every" value={every} onChange={(_e, value) => setEvery(value as Every)}>
                    {EVERY.map(value => <FormSelectOption key={value} value={value} label={everyLabels[value]} />)}
                </FormSelect>
                <FormHelperText><HelperText><HelperTextItem>{t("The URL is asked whether the file changed, and it is downloaded again only when it did.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            <Checkbox id="fetch-replace" label={t("Replace a file already there")} isChecked={replace} onChange={(_e, value) => setReplace(value)} />
            {again && !!again.partial && (
                <Content component="p">{t("{{size}} arrived before: the download goes on from there, if the file did not change meanwhile.", { size: formatBytes(again.partial) })}</Content>
            )}
        </FormModal>
    );
}

// A name for a new folder, which may hold a / to make folders within folders.
export function NewFolderModal({ directory, onSubmit, onClose }: {
    directory: string;
    onSubmit: (path: string) => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [value, setValue] = useState("");
    return (
        <FormModal title={t("New folder")} submitLabel={t("Create")} onSubmit={() => onSubmit(joinPath(directory, trimSlashes(value)))}
            onClose={onClose} isDisabled={!trimSlashes(value)} variant={ModalVariant.small}>
            <FormGroup label={t("Name")} isRequired fieldId="store-name">
                <TextInput id="store-name" value={value} onChange={(_e, text) => setValue(text)} isRequired autoFocus />
                <FormHelperText><HelperText><HelperTextItem>
                    {directory ? t("In {{path}}. A / makes folders within folders.", { path: directory }) : t("A / makes folders within folders.")}
                </HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}

// A new name in the same folder: moving elsewhere is "Move to…".
export function RenameModal({ path, taken, onSubmit, onClose }: {
    path: string;
    // The names already in its folder.
    taken: Set<string>;
    onSubmit: (to: string) => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const initial = basename(path);
    const [value, setValue] = useState(initial);
    const name = value.trim();
    const problem = name.includes("/") ? t("A name, without /: to put it elsewhere, use Move to…")
        : name !== initial && taken.has(name) ? t("There is already something with this name in the folder.") : null;
    return (
        <FormModal title={t("Rename {{name}}", { name: initial })} submitLabel={t("Rename")} variant={ModalVariant.small}
            onSubmit={() => onSubmit(joinPath(dirname(path), name))} onClose={onClose}
            isDisabled={!name || name === initial || problem !== null}>
            <FormGroup label={t("Name")} isRequired fieldId="store-rename">
                <TextInput id="store-rename" value={value} onChange={(_e, text) => setValue(text)} isRequired autoFocus
                    validated={problem ? "error" : "default"}
                    onFocus={event => {
                        // The name is selected without its extension, as file managers do.
                        const dot = initial.lastIndexOf(".");
                        event.currentTarget.setSelectionRange(0, dot > 0 ? dot : initial.length);
                    }} />
                {problem && <FormHelperText><HelperText><HelperTextItem variant="error">{problem}</HelperTextItem></HelperText></FormHelperText>}
            </FormGroup>
        </FormModal>
    );
}

// What to do with names already in the store, for all of them at once.
export function ConflictModal({ paths, canReplace, onChoose, onClose }: {
    paths: string[];
    // Uploads may replace a file; a move may not.
    canReplace: boolean;
    onChoose: (clash: Clash) => void;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [clash, setClash] = useState<Clash>(canReplace ? "replace" : "keep");
    const shown = paths.slice(0, 10);
    return (
        <FormModal title={t("Names already in the store")} submitLabel={t("Continue")} variant={ModalVariant.small}
            isDanger={clash === "replace"} onClose={onClose}
            onSubmit={async () => { onChoose(clash); onClose(); }}>
            <Content component="p">{t("These are already in the store:")}</Content>
            <Content component="ul">
                {shown.map(path => <Content component="li" key={path}>{path}</Content>)}
                {paths.length > shown.length && <Content component="li">{t("files_count", { count: paths.length - shown.length })}</Content>}
            </Content>
            {canReplace && (
                <Radio id="clash-replace" name="clash" label={t("Replace them")} isChecked={clash === "replace"} onChange={() => setClash("replace")}
                    description={t("The files there now are gone. A folder is never replaced.")} />
            )}
            <Radio id="clash-keep" name="clash" label={t("Keep both")} isChecked={clash === "keep"} onChange={() => setClash("keep")}
                description={t("What comes in gets a number after its name: setup (1).msi.")} />
            <Radio id="clash-skip" name="clash" label={t("Skip them")} isChecked={clash === "skip"} onChange={() => setClash("skip")}
                description={t("Only the others go ahead.")} />
        </FormModal>
    );
}

// The panel beside the list: all that is known of a file or a folder, and
// what can be done with it.
export function StoreDetails({ list, path, actions, onClose, onTrack, onRefresh }: {
    list: StoreList;
    path: string;
    actions: React.ReactNode;
    onClose: () => void;
    // Given to who may change how a file is checked against its source.
    onTrack?: (every: Every) => void;
    onRefresh?: () => void;
}) {
    const { t, i18n } = useTranslation();
    const everyLabels = useEveryLabels();
    const file = list.files.find(candidate => candidate.path === path) ?? null;
    const inside = file ? null : sizeOf(list, [path]);
    return (
        <DrawerPanelContent widths={{ default: "width_33" }} minSize="18rem">
            <DrawerHead>
                <Title headingLevel="h3" size="lg" style={{ wordBreak: "break-all" }}>{basename(path)}</Title>
                <DrawerActions><DrawerCloseButton onClick={onClose} aria-label={t("Close the details")} /></DrawerActions>
            </DrawerHead>
            <DrawerPanelBody>
                <Flex spaceItems={{ default: "spaceItemsSm" }} style={{ marginBlockEnd: "1rem" }}>{React.Children.map(actions, action => action && <FlexItem>{action}</FlexItem>)}</Flex>
                <DescriptionList isCompact>
                    <DescriptionListGroup>
                        <DescriptionListTerm>{t("Path")}</DescriptionListTerm>
                        <DescriptionListDescription style={{ wordBreak: "break-all" }}>{path}</DescriptionListDescription>
                    </DescriptionListGroup>
                    {file && (
                        <>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Size")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {formatBytes(file.size)}{file.size >= 1024 && ` (${file.size.toLocaleString(i18n.language)} B)`}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("In a playbook")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    <ClipboardCopy isReadOnly aria-label={t("In a playbook")}>{playbookPath(path)}</ClipboardCopy>
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("SHA-256")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {file.sha256
                                        ? <ClipboardCopy isReadOnly aria-label={t("SHA-256")}>{file.sha256}</ClipboardCopy>
                                        : <Label isCompact color="grey">{t("Being computed")}</Label>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Origin")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    <Origin file={file} />
                                    {file.origin === "copy" && <div style={{ wordBreak: "break-all" }}><small>{file.source}</small></div>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            <DescriptionListGroup>
                                <DescriptionListTerm>{t("Updated")}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    {formatTime(file.updated_at, i18n.language)}{file.updated_by ? `, ${file.updated_by}` : ""}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                            {file.refresh && (
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Source")}</DescriptionListTerm>
                                    <DescriptionListDescription>
                                        <div style={{ wordBreak: "break-all" }}>{file.refresh.url}</div>
                                        <small>
                                            {file.refresh.checked_at ? t("Checked {{time}}", { time: formatTime(file.refresh.checked_at, i18n.language) }) : t("Not checked yet")}
                                            {file.refresh.changed_at ? `; ${t("changed {{time}}", { time: formatTime(file.refresh.changed_at, i18n.language) })}` : ""}
                                        </small>
                                        {file.refresh.error && <div><Label isCompact color="red">{t("The last check failed")}</Label> <small>{file.refresh.error}</small></div>}
                                        {onTrack
                                            ? (
                                                <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }} style={{ marginBlockStart: "0.5rem" }}>
                                                    <FlexItem>
                                                        <FormSelect id="store-every" aria-label={t("Check for a newer version")} value={file.refresh.every}
                                                            onChange={(_e, value) => onTrack(value as Every)}>
                                                            {EVERY.map(value => <FormSelectOption key={value} value={value} label={everyLabels[value]} />)}
                                                        </FormSelect>
                                                    </FlexItem>
                                                    {onRefresh && <FlexItem><Button variant="secondary" size="sm" onClick={onRefresh}>{t("Check now")}</Button></FlexItem>}
                                                </Flex>
                                            )
                                            : <div><small>{t("Checked: {{every}}", { every: everyLabels[file.refresh.every] })}</small></div>}
                                    </DescriptionListDescription>
                                </DescriptionListGroup>
                            )}
                            {!file.usable && (
                                <DescriptionListGroup>
                                    <DescriptionListTerm>{t("Runs")}</DescriptionListTerm>
                                    <DescriptionListDescription>
                                        <Label isCompact color="orange">{t("Not for runs")}</Label>{" "}
                                        {t("Not given to runs: the file is not the service account's own. Put files here as cockpit-fleet, or change their owner.")}
                                    </DescriptionListDescription>
                                </DescriptionListGroup>
                            )}
                        </>
                    )}
                    {inside && (
                        <DescriptionListGroup>
                            <DescriptionListTerm>{t("Holds")}</DescriptionListTerm>
                            <DescriptionListDescription>
                                {t("{{what}}, {{size}}", { what: t("files_count", { count: inside.files }), size: formatBytes(inside.bytes) })}
                            </DescriptionListDescription>
                        </DescriptionListGroup>
                    )}
                </DescriptionList>
            </DrawerPanelBody>
        </DrawerPanelContent>
    );
}
