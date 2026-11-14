import React from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem } from "@patternfly/react-core";
import { call } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import type { Starter } from "../lib/types.ts";

// The package's starters in the language of the page; an administrator's
// are shown as they wrote them.
function packaged(t: TFunction): Record<string, { title: string; description: string }> {
    return {
        "project/linux": {
            title: t("Linux project"),
            description: t("A playbook for every host, one to see that they answer, a role for what they share and variables by group."),
        },
        "project/windows": {
            title: t("Windows project"),
            description: t("The same for Windows hosts over SSH: a playbook for the group windows, one to see that they answer, a role, and the collection ansible.windows."),
        },
        "inventory/linux": {
            title: t("Linux hosts"),
            description: t("A group linux, in YAML, with the variables of the group beside it."),
        },
        "inventory/windows": {
            title: t("Windows hosts"),
            description: t("A group windows, in YAML, with what Ansible needs to reach Windows over SSH."),
        },
        "playbook/linux": {
            title: t("Linux playbook"),
            description: t("A play for the group linux, with privileges, to fill with tasks."),
        },
        "playbook/windows": {
            title: t("Windows playbook"),
            description: t("A play for the group windows, to fill with tasks; it needs the collection ansible.windows."),
        },
    };
}

export function starterText(t: TFunction, starter: Starter): { title: string; description: string } {
    const own = starter.source === "package" ? packaged(t)[`${starter.kind}/${starter.name}`] : undefined;
    return own ?? { title: starter.title, description: starter.description };
}

// "Empty", or one of the starters of KIND; VALUE is the ref of the one chosen.
export function StarterSelect({ kind, fieldId, value, onChange }: {
    kind: Starter["kind"];
    fieldId: string;
    value: Starter | null;
    onChange: (starter: Starter | null) => void;
}) {
    const { t } = useTranslation();
    const loaded = useLoad(() => call<{ starters: Starter[] }>("starter.list", { kind }));
    const starters = loaded.data?.starters ?? [];
    // Both sources may have one of the same name: say which one it is.
    const label = (starter: Starter) => {
        const { title } = starterText(t, starter);
        const shared = starters.some(other => other.name === starter.name && other.source !== starter.source);
        return shared ? `${title} (${starter.source === "package" ? t("package") : t("this controller")})` : title;
    };
    const help = loaded.error
        ? loaded.error.message
        : value ? (starterText(t, value).description || value.files.join(", ")) : t("Nothing to begin with.");
    return (
        <FormGroup label={t("Start from")} fieldId={fieldId}>
            <FormSelect id={fieldId} value={value?.ref ?? ""} isDisabled={!starters.length}
                onChange={(_e, ref) => onChange(starters.find(starter => starter.ref === ref) ?? null)}>
                <FormSelectOption value="" label={t("Empty")} />
                {starters.map(starter => (
                    <FormSelectOption key={starter.ref} value={starter.ref} isDisabled={!!starter.error}
                        label={starter.error ? t("{{title}}: cannot be used ({{error}})", { title: label(starter), error: starter.error }) : label(starter)} />
                ))}
            </FormSelect>
            <FormHelperText>
                <HelperText><HelperTextItem variant={loaded.error ? "error" : "default"}>{help}</HelperTextItem></HelperText>
            </FormHelperText>
        </FormGroup>
    );
}
