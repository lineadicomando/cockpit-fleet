import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { FormGroup, FormHelperText, HelperText, HelperTextItem, ModalVariant } from "@patternfly/react-core";
import { isInside, isUsablePath, relocate, type PathChoice } from "../lib/files.ts";
import { FormModal } from "./common.tsx";
import { PathInput } from "./PathInput.tsx";

// Moves or copies files and folders into a folder chosen, or typed: of a
// project, or of the store.
export function RelocateModal({ paths, copy, directories, onSubmit, onClose }: {
    paths: string[];
    copy: boolean;
    directories: string[];
    onSubmit: (into: string) => Promise<void>;
    onClose: () => void;
}) {
    const { t } = useTranslation();
    const [into, setInto] = useState("");
    const target = into.trim().replace(/\/+$/, "");
    const moves = target === "" || isUsablePath(target) ? relocate(paths, target) : null;
    const choices: PathChoice[] = directories.filter(path => !paths.some(moved => isInside(path, moved))).map(path => ({ path, directory: true }));
    const what = paths.length === 1 ? paths[0] : t("files_count", { count: paths.length });
    return (
        <FormModal title={copy ? t("Copy {{what}}", { what }) : t("Move {{what}}", { what })} submitLabel={copy ? t("Copy") : t("Move")}
            onSubmit={() => onSubmit(target)} onClose={onClose} isDisabled={!moves || moves.length === 0} variant={ModalVariant.small}>
            <FormGroup label={t("To the folder")} fieldId="relocate-into">
                <PathInput id="relocate-into" label={t("To the folder")} value={into} onChange={setInto} choices={choices} placeholder={t("The top")}
                    emptyText={t("No other folder to suggest")} toggleLabel={t("Show the folders")} />
                <FormHelperText><HelperText><HelperTextItem variant={moves === null ? "error" : "default"}>
                    {moves === null ? t("A folder cannot go into itself.") : t("Empty for the top. A folder that does not exist is created.")}
                </HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
        </FormModal>
    );
}
