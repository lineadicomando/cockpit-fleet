import React, { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, type CompletionSource } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { jinja } from "@codemirror/lang-jinja";
import { json } from "@codemirror/lang-json";
import { python } from "@codemirror/lang-python";
import { yaml } from "@codemirror/lang-yaml";
import {
    HighlightStyle, StreamLanguage, bracketMatching, foldGutter, foldKeymap, indentOnInput, indentService, indentUnit, syntaxHighlighting,
} from "@codemirror/language";
import { properties } from "@codemirror/legacy-modes/mode/properties";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, highlightSpecialChars, keymap, lineNumbers, placeholder as hint } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { indentAfter } from "../lib/ansible.ts";

// The language of a file, from its name. A YAML file is read as Jinja around
// YAML, which is what Ansible makes of it.
function languageFor(path: string): Extension {
    const name = path.slice(path.lastIndexOf("/") + 1);
    if (/\.ya?ml$/.test(name)) return [jinja({ base: yaml() }), yamlIndent];
    if (/\.j2$/.test(name)) return jinja();
    if (/\.py$/.test(name)) return python();
    if (/\.json$/.test(name)) return json();
    if (/\.(ini|cfg|conf|toml)$/.test(name) || name === "hosts" || name === "inventory") return StreamLanguage.define(properties);
    return [];
}

// Where a new line starts in YAML. The grammar leaves it at the level of the
// line above, also after "key:", where what follows always goes further in.
const yamlIndent = indentService.of((context, pos) => {
    if (context.simulatedBreak === null) return undefined;
    return indentAfter(context.lineAt(pos, -1).text);
});

// The theme's own variables, so that the editor follows the light and the
// dark theme like the rest of the page.
const color = (name: string) => `var(--pf-t--global--${name})`;

const look = EditorView.theme({
    "&": {
        // Fills the box the page gives it and scrolls inside.
        blockSize: "100%",
        color: color("text--color--regular"),
        backgroundColor: color("background--color--primary--default"),
        border: `${color("border--width--regular")} solid ${color("border--color--default")}`,
        borderRadius: color("border--radius--small"),
        fontSize: color("font--size--sm"),
    },
    "&.cm-focused": { outline: `${color("border--width--strong")} solid ${color("border--color--clicked")}`, outlineOffset: "-1px" },
    ".cm-scroller": { fontFamily: color("font--family--mono"), lineHeight: "1.5" },
    ".cm-content": { caretColor: color("text--color--regular") },
    ".cm-cursor": { borderLeftColor: color("text--color--regular") },
    ".cm-gutters": {
        color: color("text--color--subtle"),
        backgroundColor: color("background--color--secondary--default"),
        borderRight: `${color("border--width--regular")} solid ${color("border--color--default")}`,
    },
    ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: color("background--color--primary--hover") },
    "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground": {
        backgroundColor: color("background--color--action--plain--clicked"),
    },
    ".cm-selectionMatch": { backgroundColor: color("background--color--secondary--hover") },
    ".cm-tooltip": {
        color: color("text--color--regular"),
        backgroundColor: color("background--color--floating--default"),
        border: `${color("border--width--regular")} solid ${color("border--color--default")}`,
        borderRadius: color("border--radius--small"),
        boxShadow: color("box-shadow--md"),
    },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": {
        color: color("text--color--regular"),
        backgroundColor: color("background--color--action--plain--clicked"),
    },
    ".cm-tooltip.cm-completionInfo": { maxWidth: "28rem", padding: color("spacer--sm") },
    ".cm-completionDetail": { color: color("text--color--subtle") },
    ".cm-panels": {
        color: color("text--color--regular"),
        backgroundColor: color("background--color--secondary--default"),
    },
    ".cm-panels input, .cm-panels button": { fontSize: "inherit" },
    ".cm-foldPlaceholder": { color: color("text--color--subtle"), backgroundColor: "transparent", border: "none" },
});

const status = (name: string) => color(`text--color--status--${name}--default`);

const highlight = HighlightStyle.define([
    { tag: [tags.propertyName, tags.definition(tags.propertyName), tags.keyword, tags.heading], color: status("info") },
    { tag: [tags.string, tags.special(tags.string), tags.content], color: status("success") },
    { tag: [tags.number, tags.bool, tags.null, tags.atom], color: status("warning") },
    { tag: [tags.comment, tags.meta], color: color("text--color--subtle"), fontStyle: "italic" },
    { tag: [tags.variableName, tags.function(tags.variableName), tags.labelName], color: status("custom") },
    { tag: [tags.brace, tags.operator, tags.punctuation, tags.separator], color: color("text--color--subtle") },
    { tag: tags.invalid, color: status("danger") },
]);

interface Props {
    value: string;
    onChange: (value: string) => void;
    // Decides the language; not shown.
    path: string;
    label: string;
    readOnly?: boolean;
    // Called on Ctrl+S.
    onSave?: () => void;
    // Suggestions on top of the language's own.
    complete?: CompletionSource;
    // Shown while there is no text.
    placeholder?: string;
    className?: string;
}

// A text editor with highlighting, automatic indentation and completion.
export function CodeEditor({ value, onChange, path, label, readOnly = false, onSave, complete, placeholder, className }: Props) {
    const { t } = useTranslation();
    const host = useRef<HTMLDivElement>(null);
    const view = useRef<EditorView | null>(null);
    const latest = useRef({ onChange, onSave });
    latest.current = { onChange, onSave };
    const parts = useRef({ language: new Compartment(), access: new Compartment(), completion: new Compartment() });

    const completion = (source?: CompletionSource) => (source ? EditorState.languageData.of(() => [{ autocomplete: source }]) : []);
    const access = (locked: boolean) => [EditorState.readOnly.of(locked), EditorView.contentAttributes.of({ "aria-label": label, "aria-readonly": String(locked) })];

    useEffect(() => {
        const { language, access: accessPart, completion: completionPart } = parts.current;
        // CodeMirror writes its styles into the page, which Cockpit's content
        // security policy refuses. In a shadow root it uses constructed style
        // sheets instead, which the policy allows; the theme's variables and
        // fonts are inherited through it.
        const root = host.current!.shadowRoot ?? host.current!.attachShadow({ mode: "open" });
        const created = new EditorView({
            parent: root,
            root,
            state: EditorState.create({
                doc: value,
                extensions: [
                    lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(), foldGutter(), drawSelection(),
                    EditorState.allowMultipleSelections.of(true), indentOnInput(), bracketMatching(), closeBrackets(),
                    autocompletion(), highlightActiveLine(), highlightSelectionMatches(),
                    // YAML takes no tabs: two spaces, as Ansible's own examples.
                    indentUnit.of("  "), EditorState.tabSize.of(2),
                    keymap.of([
                        { key: "Mod-s", preventDefault: true, run: () => { latest.current.onSave?.(); return true; } },
                        ...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, ...completionKeymap,
                        indentWithTab,
                    ]),
                    look, syntaxHighlighting(highlight), placeholder ? hint(placeholder) : [],
                    EditorState.phrases.of({
                        Find: t("Find"), Replace: t("Replace"), next: t("next"), previous: t("previous"), all: t("all"),
                        "match case": t("match case"), regexp: t("regexp"), "by word": t("by word"), replace: t("replace"),
                        "replace all": t("replace all"), close: t("close"),
                    }),
                    language.of(languageFor(path)), accessPart.of(access(readOnly)), completionPart.of(completion(complete)),
                    EditorView.updateListener.of(update => {
                        if (update.docChanged) latest.current.onChange(update.state.doc.toString());
                    }),
                ],
            }),
        });
        view.current = created;
        return () => created.destroy();
    }, []);

    useEffect(() => {
        view.current?.dispatch({ effects: parts.current.language.reconfigure(languageFor(path)) });
    }, [path]);
    useEffect(() => {
        view.current?.dispatch({ effects: parts.current.access.reconfigure(access(readOnly)) });
    }, [readOnly, label]);
    useEffect(() => {
        view.current?.dispatch({ effects: parts.current.completion.reconfigure(completion(complete)) });
    }, [complete]);
    // A value set from outside: a file loaded, changes given up.
    useEffect(() => {
        const current = view.current;
        if (current && value !== current.state.doc.toString()) {
            current.dispatch({ changes: { from: 0, to: current.state.doc.length, insert: value } });
        }
    }, [value]);

    return <div ref={host} className={`fleet-code${className ? ` ${className}` : ""}`} />;
}
