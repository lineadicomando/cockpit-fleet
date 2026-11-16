import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { ExpandableSection, FormGroup, FormSelect, FormSelectOption, Grid, GridItem, HelperText, HelperTextItem, TextInput } from "@patternfly/react-core";
import { overridesOf, type OverrideFields } from "../lib/templates.ts";
import type { Template } from "../lib/types.ts";

// Tags, tags to skip, forks and output detail of a launch or a schedule: the
// template's at first; what differs from them replaces them.
export function OverridesSection({ id, template, fields, onChange }: {
    id: string; template: Template | undefined; fields: OverrideFields; onChange: (fields: OverrideFields) => void;
}) {
    const { t } = useTranslation();
    const replaced = Object.keys(overridesOf(template, fields)).length;
    const [open, setOpen] = useState(replaced > 0);
    const set = (key: keyof OverrideFields) => (_e: unknown, value: string) => onChange({ ...fields, [key]: value });
    return (
        <ExpandableSection isExpanded={open} onToggle={(_e, value) => setOpen(value)}
            toggleText={replaced ? t("replaced_values_count", { count: replaced }) : t("Values of the template")}>
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("Tags")} fieldId={`${id}-tags`}>
                        <TextInput id={`${id}-tags`} value={fields.tags} onChange={set("tags")} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Skip tags")} fieldId={`${id}-skip`}>
                        <TextInput id={`${id}-skip`} value={fields.skipTags} onChange={set("skipTags")} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Hosts in parallel (forks)")} fieldId={`${id}-forks`}>
                        <TextInput id={`${id}-forks`} type="number" min={1} value={fields.forks} onChange={set("forks")} placeholder={t("Default")} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Output detail")} fieldId={`${id}-verbosity`}>
                        <FormSelect id={`${id}-verbosity`} value={fields.verbosity} onChange={set("verbosity")}>
                            <FormSelectOption value="0" label={t("Normal")} />
                            <FormSelectOption value="1" label="-v" />
                            <FormSelectOption value="2" label="-vv" />
                            <FormSelectOption value="3" label="-vvv" />
                            {fields.verbosity === "4" && <FormSelectOption value="4" label="-vvvv" />}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
            </Grid>
            <HelperText>
                <HelperTextItem>{t("They start as the template's: what you change replaces them here only, and the template stays as it is.")}</HelperTextItem>
            </HelperText>
        </ExpandableSection>
    );
}
