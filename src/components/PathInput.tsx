import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, MenuToggle, Select, SelectList, SelectOption, TextInputGroup, TextInputGroupMain, TextInputGroupUtilities } from "@patternfly/react-core";
import { FileIcon, FolderIcon, TimesIcon } from "@patternfly/react-icons";
import { matchPaths } from "../lib/files.ts";
import type { PathChoice } from "../lib/files.ts";

const SHOWN = 50;

// A path inside a project, typed freely or picked from the project's own:
// the list narrows down as one types, and a path not in it is still accepted.
export function PathInput({ id, label, value, onChange, choices, placeholder }: {
    id: string;
    // What a screen reader says of the field: PatternFly's own is in English.
    label: string;
    value: string;
    onChange: (value: string) => void;
    choices: PathChoice[];
    placeholder?: string;
}) {
    const { t } = useTranslation();
    const [isOpen, setIsOpen] = useState(false);
    const [focused, setFocused] = useState<number | null>(null);
    const input = useRef<HTMLInputElement>(null);
    const { shown, more } = matchPaths(choices, value, SHOWN);
    const listId = `${id}-listbox`;
    const optionId = (index: number) => `${id}-option-${index}`;
    const listShown = isOpen && (shown.length > 0 || choices.length === 0);

    // Escape closes the list, not the dialog around it. The dialog hears the
    // key before React does, so it is stopped here, on the input itself.
    useEffect(() => {
        const element = input.current;
        if (!element || !listShown) return;
        const onEscape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            setIsOpen(false);
            setFocused(null);
        };
        element.addEventListener("keydown", onEscape);
        return () => element.removeEventListener("keydown", onEscape);
    }, [listShown]);

    function choose(path: string) {
        onChange(path);
        setIsOpen(false);
        setFocused(null);
        input.current?.focus();
    }

    function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!shown.length) return;
            setIsOpen(true);
            const step = event.key === "ArrowDown" ? 1 : -1;
            setFocused(current => current === null
                ? (step > 0 ? 0 : shown.length - 1)
                : (current + step + shown.length) % shown.length);
        } else if (event.key === "Enter" && listShown && focused !== null && shown[focused]) {
            // Without this the form would be submitted with what was typed.
            event.preventDefault();
            choose(shown[focused].path);
        }
    }

    const toggle = (toggleRef: React.Ref<HTMLDivElement>) => (
        <MenuToggle ref={toggleRef} variant="typeahead" aria-label={t("Show the project's paths")} isFullWidth
            isExpanded={listShown} onClick={() => { setIsOpen(!listShown); input.current?.focus(); }}>
            <TextInputGroup isPlain>
                <TextInputGroupMain inputId={id} aria-label={label} value={value} placeholder={placeholder} innerRef={input}
                    autoComplete="off" role="combobox" isExpanded={listShown} aria-controls={listShown ? listId : undefined}
                    aria-activedescendant={listShown && focused !== null ? optionId(focused) : undefined}
                    onClick={() => setIsOpen(true)} onKeyDown={onKeyDown}
                    onChange={(_event, typed) => { onChange(typed); setIsOpen(true); setFocused(null); }} />
                {value && (
                    <TextInputGroupUtilities>
                        <Button variant="plain" icon={<TimesIcon />} aria-label={t("Clear")}
                            onClick={() => { onChange(""); setFocused(null); input.current?.focus(); }} />
                    </TextInputGroupUtilities>
                )}
            </TextInputGroup>
        </MenuToggle>
    );

    return (
        <Select id={`${id}-select`} isOpen={listShown} selected={value}
            onSelect={(_event, selected) => { if (typeof selected === "string") choose(selected); }}
            onOpenChange={open => { setIsOpen(open); if (!open) setFocused(null); }}
            toggle={toggle} shouldFocusFirstItemOnOpen={false} isScrollable>
            <SelectList id={listId}>
                {choices.length === 0 && <SelectOption isDisabled>{t("No file of this project to suggest")}</SelectOption>}
                {shown.map((choice, index) => (
                    <SelectOption key={choice.path} id={optionId(index)} value={choice.path} isFocused={focused === index}
                        icon={choice.directory ? <FolderIcon /> : <FileIcon />}>
                        {choice.path}
                    </SelectOption>
                ))}
                {more > 0 && <SelectOption isDisabled>{t("paths_more_count", { count: more })}</SelectOption>}
            </SelectList>
        </Select>
    );
}
