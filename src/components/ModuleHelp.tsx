import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Content, Label, ModalBody, ModalHeader, ModalVariant, TextInput, Title } from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { loadModules } from "../lib/ansible-source.ts";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import type { ModuleHelp, ModuleOption, ModuleReturn } from "../lib/types.ts";
import { Alert, Modal, Spinner } from "./pf.tsx";

function Paragraphs({ text }: { text: string[] }) {
    return <>{text.map((paragraph, index) => <Content key={index} component="p">{paragraph}</Content>)}</>;
}

function Options({ options, depth = 0 }: { options: ModuleOption[]; depth?: number }) {
    const { t } = useTranslation();
    return (
        <>
            {options.map(option => (
                <React.Fragment key={option.name}>
                    <Tr>
                        <Td dataLabel={t("Parameter")} style={{ paddingInlineStart: `${1 + depth * 1.5}rem`, whiteSpace: "nowrap" }}>
                            <code>{option.name}</code>
                            {option.required && <Label isCompact color="red" style={{ marginInlineStart: "0.5rem" }}>{t("required")}</Label>}
                            <Content component="small" style={{ display: "block" }}>{option.type}</Content>
                        </Td>
                        <Td dataLabel={t("Description")}>
                            <Paragraphs text={option.text ?? [option.description]} />
                            {option.choices.length > 0 && <Content component="small" style={{ display: "block" }}>{t("Choices: {{list}}", { list: option.choices.join(", ") })}</Content>}
                            {option.default !== null && option.default !== undefined && <Content component="small" style={{ display: "block" }}>{t("Default: {{value}}", { value: JSON.stringify(option.default) })}</Content>}
                            {option.aliases.length > 0 && <Content component="small" style={{ display: "block" }}>{t("Aliases: {{list}}", { list: option.aliases.join(", ") })}</Content>}
                        </Td>
                    </Tr>
                    {option.suboptions && option.suboptions.length > 0 && <Options options={option.suboptions} depth={depth + 1} />}
                </React.Fragment>
            ))}
        </>
    );
}

function Returns({ values, depth = 0 }: { values: ModuleReturn[]; depth?: number }) {
    const { t } = useTranslation();
    return (
        <>
            {values.map(value => (
                <React.Fragment key={value.name}>
                    <Tr>
                        <Td dataLabel={t("Value")} style={{ paddingInlineStart: `${1 + depth * 1.5}rem`, whiteSpace: "nowrap" }}>
                            <code>{value.name}</code>
                            <Content component="small" style={{ display: "block" }}>{value.type}</Content>
                        </Td>
                        <Td dataLabel={t("Description")}>
                            <Paragraphs text={value.text} />
                            {value.returned && <Content component="small" style={{ display: "block" }}>{t("Returned: {{when}}", { when: value.returned })}</Content>}
                            {value.sample !== null && value.sample !== undefined && <Content component="small" style={{ display: "block" }}>{t("Sample: {{value}}", { value: JSON.stringify(value.sample) })}</Content>}
                        </Td>
                    </Tr>
                    {value.contains.length > 0 && <Returns values={value.contains} depth={depth + 1} />}
                </React.Fragment>
            ))}
        </>
    );
}

function Help({ name }: { name: string }) {
    const { t } = useTranslation();
    const help = useLoad(() => call<ModuleHelp>("module.help", { name }), [name]);

    if (help.error) return <Alert variant="danger" isInline title={help.error.message} />;
    if (!help.data || help.loading) return <Spinner aria-label={t("Loading")} />;
    const data = help.data;
    return (
        <>
            <Title headingLevel="h3" size="md"><code>{data.name}</code></Title>
            <Content component="p"><strong>{data.description}</strong></Content>
            <Paragraphs text={data.text} />
            {data.requirements.length > 0 && (
                <>
                    <Title headingLevel="h4" size="md">{t("Requirements")}</Title>
                    <Content component="ul">{data.requirements.map((line, index) => <Content key={index} component="li">{line}</Content>)}</Content>
                </>
            )}
            {data.options.length > 0 && (
                <>
                    <Title headingLevel="h4" size="md">{t("Parameters")}</Title>
                    <Table variant="compact" aria-label={t("Parameters")}>
                        <Thead><Tr><Th>{t("Parameter")}</Th><Th>{t("Description")}</Th></Tr></Thead>
                        <Tbody><Options options={data.options} /></Tbody>
                    </Table>
                </>
            )}
            {data.notes.length > 0 && (
                <>
                    <Title headingLevel="h4" size="md">{t("Notes")}</Title>
                    <Content component="ul">{data.notes.map((line, index) => <Content key={index} component="li">{line}</Content>)}</Content>
                </>
            )}
            {data.examples && (
                <>
                    <Title headingLevel="h4" size="md">{t("Examples")}</Title>
                    <pre className="fleet-log">{data.examples}</pre>
                </>
            )}
            {data.returns.length > 0 && (
                <>
                    <Title headingLevel="h4" size="md">{t("Return values")}</Title>
                    <Table variant="compact" aria-label={t("Return values")}>
                        <Thead><Tr><Th>{t("Value")}</Th><Th>{t("Description")}</Th></Tr></Thead>
                        <Tbody><Returns values={data.returns} /></Tbody>
                    </Table>
                </>
            )}
        </>
    );
}

// The documentation of a module installed on the controller, as ansible-doc has it.
export function ModuleHelpModal({ name, onClose }: { name?: string; onClose: () => void }) {
    const { t } = useTranslation();
    const [typed, setTyped] = useState(name ?? "");
    const [shown, setShown] = useState(name ?? "");
    const modules = useLoad(loadModules);
    const known = modules.data ?? {};

    function show(value: string) {
        setTyped(value);
        // A short name is looked for among the built-in modules.
        const full = value in known ? value : `ansible.builtin.${value}` in known ? `ansible.builtin.${value}` : "";
        if (full) setShown(full);
    }

    return (
        <Modal variant={ModalVariant.large} isOpen onClose={onClose}>
            <ModalHeader title={t("Help on modules")} />
            <ModalBody>
                <TextInput id="module-help-name" value={typed} onChange={(_e, value) => show(value.trim())} list="module-help-names"
                    placeholder="ansible.builtin.copy" aria-label={t("Module")} spellCheck={false} style={{ marginBottom: "1rem" }} />
                <datalist id="module-help-names">
                    {Object.keys(known).map(module => <option key={module} value={module} />)}
                </datalist>
                {shown
                    ? <Help name={shown} />
                    : <Content component="p">{t("Type the name of a module installed on the controller.")}</Content>}
            </ModalBody>
        </Modal>
    );
}

export function ModuleHelpButton({ name }: { name?: string }) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    return (
        <>
            <Button variant={name ? "link" : "secondary"} isInline={!!name} onClick={() => setOpen(true)}>{name ? t("Help on this module") : t("Help on modules")}</Button>
            {open && <ModuleHelpModal name={name} onClose={() => setOpen(false)} />}
        </>
    );
}
