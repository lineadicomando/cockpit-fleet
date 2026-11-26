import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Label, LabelGroup, MenuToggle, Select, SelectGroup, SelectList, SelectOption, TextInputGroup, TextInputGroupMain, TextInputGroupUtilities } from "@patternfly/react-core";
import { TimesIcon } from "@patternfly/react-icons";
import { flatOptions, searchGroups, type SearchGroup } from "../lib/search.ts";

const SHOWN = 200;

// One of a long list, found by typing: the list narrows to the options every
// word typed is in. Only an option of the list is taken: leaving the field
// without choosing one keeps the one chosen before.
export function SearchSelect({ id, label, value, onChange, groups, placeholder, emptyText, toggleLabel }: {
    id: string;
    // What a screen reader says of the field: PatternFly's own is in English.
    label: string;
    value: string;
    onChange: (value: string) => void;
    groups: SearchGroup[];
    placeholder?: string;
    emptyText?: string;
    toggleLabel?: string;
}) {
    const { t } = useTranslation();
    const [isOpen, setIsOpen] = useState(false);
    // What is typed, null while nothing is: the field then shows the option chosen.
    const [typed, setTyped] = useState<string | null>(null);
    const [focused, setFocused] = useState<number | null>(null);
    const input = useRef<HTMLInputElement>(null);
    const chosen = flatOptions(groups).find(option => option.id === value);
    const found = searchGroups(groups, typed ?? "", SHOWN);
    const shown = flatOptions(found.groups);
    const listId = `${id}-listbox`;
    const optionId = (index: number) => `${id}-option-${index}`;

    function close() {
        setIsOpen(false);
        setTyped(null);
        setFocused(null);
    }

    function open() {
        if (isOpen) return;
        setIsOpen(true);
        // What is typed replaces the option shown rather than adding to it.
        input.current?.select();
    }

    function choose(chosenId: string) {
        onChange(chosenId);
        close();
        input.current?.focus();
        // Once the field shows the option chosen, what is typed next replaces it.
        setTimeout(() => input.current?.select());
    }

    // Escape closes the list, not the dialog around it. The dialog hears the
    // key before React does, so it is stopped here, on the input itself.
    useEffect(() => {
        const element = input.current;
        if (!element || !isOpen) return;
        const onEscape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            close();
        };
        element.addEventListener("keydown", onEscape);
        return () => element.removeEventListener("keydown", onEscape);
    }, [isOpen]);

    useEffect(() => {
        if (focused !== null) document.getElementById(optionId(focused))?.scrollIntoView({ block: "nearest" });
    }, [focused]);

    function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            open();
            if (!shown.length) return;
            const step = event.key === "ArrowDown" ? 1 : -1;
            setFocused(current => current === null
                ? (step > 0 ? 0 : shown.length - 1)
                : (current + step + shown.length) % shown.length);
        } else if (event.key === "Enter" && isOpen) {
            // Without this the form would be submitted.
            event.preventDefault();
            if (focused !== null && shown[focused]) choose(shown[focused].id);
        }
    }

    const toggle = (toggleRef: React.Ref<HTMLDivElement>) => (
        <MenuToggle ref={toggleRef} variant="typeahead" aria-label={toggleLabel ?? t("Show the choices")} isFullWidth
            isExpanded={isOpen} onClick={() => { if (isOpen) close(); else open(); input.current?.focus(); }}>
            <TextInputGroup isPlain>
                <TextInputGroupMain inputId={id} aria-label={label} value={typed ?? chosen?.label ?? ""} placeholder={placeholder}
                    innerRef={input} autoComplete="off" role="combobox" isExpanded={isOpen} aria-controls={isOpen ? listId : undefined}
                    aria-activedescendant={isOpen && focused !== null ? optionId(focused) : undefined}
                    onClick={open} onKeyDown={onKeyDown} onFocus={event => { if (typed === null) event.target.select(); }}
                    onChange={(_event, text) => { setTyped(text); setIsOpen(true); setFocused(text.trim() ? 0 : null); }} />
                {typed && (
                    <TextInputGroupUtilities>
                        <Button variant="plain" icon={<TimesIcon />} aria-label={t("Clear")}
                            onClick={() => { setTyped(""); setFocused(null); input.current?.focus(); }} />
                    </TextInputGroupUtilities>
                )}
            </TextInputGroup>
        </MenuToggle>
    );

    let index = 0;
    const option = (item: SearchGroup["options"][number]) => {
        const at = index++;
        return (
            <SelectOption key={item.id} id={optionId(at)} value={item.id} isFocused={focused === at}
                isSelected={item.id === value} description={item.description || undefined}>
                {item.label}
            </SelectOption>
        );
    };

    return (
        <Select id={`${id}-select`} isOpen={isOpen} selected={value}
            onSelect={(_event, selected) => { if (typeof selected === "string") choose(selected); }}
            onOpenChange={next => { if (!next) close(); }}
            toggle={toggle} shouldFocusFirstItemOnOpen={false} isScrollable>
            {shown.length === 0 && (
                <SelectList id={listId}>
                    <SelectOption isDisabled>{emptyText ?? t("Nothing matches what was typed")}</SelectOption>
                </SelectList>
            )}
            {found.groups.map((group, at) => group.label
                ? (
                    <SelectGroup key={group.label} label={group.label}>
                        <SelectList id={at === 0 ? listId : undefined}>{group.options.map(option)}</SelectList>
                    </SelectGroup>
                )
                : <SelectList key="" id={at === 0 ? listId : undefined}>{group.options.map(option)}</SelectList>)}
            {found.more > 0 && (
                <SelectList>
                    <SelectOption isDisabled>{t("choices_more_count", { count: found.more })}</SelectOption>
                </SelectList>
            )}
        </Select>
    );
}

// Several of a long list, found by typing: each one chosen becomes a label
// in the field, removed with its cross or with Backspace on an empty field.
// The list stays open while choosing, each option taken or left in turn.
export function MultiSearchSelect({ id, label, values, onChange, groups, placeholder, emptyText, toggleLabel }: {
    id: string;
    label: string;
    values: string[];
    onChange: (values: string[]) => void;
    groups: SearchGroup[];
    placeholder?: string;
    emptyText?: string;
    toggleLabel?: string;
}) {
    const { t } = useTranslation();
    const [isOpen, setIsOpen] = useState(false);
    const [typed, setTyped] = useState("");
    const [focused, setFocused] = useState<number | null>(null);
    const input = useRef<HTMLInputElement>(null);
    const all = flatOptions(groups);
    const found = searchGroups(groups, typed, SHOWN);
    const shown = flatOptions(found.groups);
    const listId = `${id}-listbox`;
    const optionId = (index: number) => `${id}-option-${index}`;
    const labelOf = (value: string) => all.find(option => option.id === value)?.label ?? value;

    function close() {
        setIsOpen(false);
        setTyped("");
        setFocused(null);
    }

    function toggleValue(value: string) {
        onChange(values.includes(value) ? values.filter(one => one !== value) : [...values, value]);
        input.current?.focus();
    }

    useEffect(() => {
        const element = input.current;
        if (!element || !isOpen) return;
        const onEscape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            close();
        };
        element.addEventListener("keydown", onEscape);
        return () => element.removeEventListener("keydown", onEscape);
    }, [isOpen]);

    useEffect(() => {
        if (focused !== null) document.getElementById(optionId(focused))?.scrollIntoView({ block: "nearest" });
    }, [focused]);

    function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setIsOpen(true);
            if (!shown.length) return;
            const step = event.key === "ArrowDown" ? 1 : -1;
            setFocused(current => current === null
                ? (step > 0 ? 0 : shown.length - 1)
                : (current + step + shown.length) % shown.length);
        } else if (event.key === "Enter" && isOpen) {
            event.preventDefault();
            if (focused !== null && shown[focused]) toggleValue(shown[focused].id);
        } else if (event.key === "Backspace" && !typed && values.length) {
            onChange(values.slice(0, -1));
        }
    }

    const toggle = (toggleRef: React.Ref<HTMLDivElement>) => (
        <MenuToggle ref={toggleRef} variant="typeahead" aria-label={toggleLabel ?? t("Show the choices")} isFullWidth className="fleet-multi-select"
            isExpanded={isOpen} onClick={() => { setIsOpen(!isOpen); input.current?.focus(); }}>
            <TextInputGroup isPlain>
                <TextInputGroupMain inputId={id} aria-label={label} value={typed} placeholder={values.length ? undefined : placeholder}
                    innerRef={input} autoComplete="off" role="combobox" isExpanded={isOpen} aria-controls={isOpen ? listId : undefined}
                    aria-activedescendant={isOpen && focused !== null ? optionId(focused) : undefined}
                    onClick={() => setIsOpen(true)} onKeyDown={onKeyDown}
                    onChange={(_event, text) => { setTyped(text); setIsOpen(true); setFocused(text.trim() ? 0 : null); }}>
                    {values.length > 0 && (
                        <LabelGroup numLabels={8} collapsedText={t("labels_more_count", { count: values.length - 8 })} expandedText={t("Show less")}>
                            {values.map(value => (
                                <Label key={value} variant="outline" isCompact closeBtnAriaLabel={t("Remove {{name}}", { name: labelOf(value) })}
                                    onClose={event => { event.stopPropagation(); toggleValue(value); }}>
                                    {labelOf(value)}
                                </Label>
                            ))}
                        </LabelGroup>
                    )}
                </TextInputGroupMain>
                {(typed || values.length > 0) && (
                    <TextInputGroupUtilities>
                        <Button variant="plain" icon={<TimesIcon />} aria-label={t("Clear")}
                            onClick={event => { event.stopPropagation(); setTyped(""); onChange([]); input.current?.focus(); }} />
                    </TextInputGroupUtilities>
                )}
            </TextInputGroup>
        </MenuToggle>
    );

    let index = 0;
    const option = (item: SearchGroup["options"][number]) => {
        const at = index++;
        return (
            <SelectOption key={item.id} id={optionId(at)} value={item.id} isFocused={focused === at} hasCheckbox
                isSelected={values.includes(item.id)} description={item.description || undefined}>
                {item.label}
            </SelectOption>
        );
    };

    return (
        <Select id={`${id}-select`} role="menu" isOpen={isOpen} selected={values}
            onSelect={(_event, selected) => { if (typeof selected === "string") toggleValue(selected); }}
            onOpenChange={next => { if (!next) close(); }}
            toggle={toggle} shouldFocusFirstItemOnOpen={false} isScrollable>
            {shown.length === 0 && (
                <SelectList id={listId}>
                    <SelectOption isDisabled>{emptyText ?? t("Nothing matches what was typed")}</SelectOption>
                </SelectList>
            )}
            {found.groups.map((group, at) => group.label
                ? (
                    <SelectGroup key={group.label} label={group.label}>
                        <SelectList id={at === 0 ? listId : undefined} isAriaMultiselectable>{group.options.map(option)}</SelectList>
                    </SelectGroup>
                )
                : <SelectList key="" id={at === 0 ? listId : undefined} isAriaMultiselectable>{group.options.map(option)}</SelectList>)}
            {found.more > 0 && (
                <SelectList>
                    <SelectOption isDisabled>{t("choices_more_count", { count: found.more })}</SelectOption>
                </SelectList>
            )}
        </Select>
    );
}
