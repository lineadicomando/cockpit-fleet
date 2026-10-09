import React from "react";
import { useTranslation } from "react-i18next";
import { Button, Checkbox, Content, Flex, FlexItem, FormGroup, FormHelperText, FormSelect, FormSelectOption, Grid, GridItem, HelperText, HelperTextItem, TextArea, TextInput } from "@patternfly/react-core";
import { type Answers, type DraftProblem, type FieldDraft, answerProblem, draftProblem, newDraft } from "../lib/survey.ts";
import type { SurveyField, SurveyType } from "../lib/types.ts";

function useDraftProblems(): Record<DraftProblem, string> {
    const { t } = useTranslation();
    return {
        name: t("The variable name takes letters, digits and underscores, and does not start with a digit."),
        reserved: t("Variables starting with ansible_ change how a run connects: set them in the template's extra variables."),
        duplicate: t("Another question sets this variable."),
        number: t("Minimum and maximum must be numbers."),
        choices: t("List at least one choice, each one once."),
        default: t("The default is not a valid answer."),
    };
}

// The questions of a template, edited as a list of drafts.
export function SurveyEditor({ drafts, onChange }: { drafts: FieldDraft[]; onChange: (drafts: FieldDraft[]) => void }) {
    const { t } = useTranslation();
    const problems = useDraftProblems();
    const types: Record<SurveyType, string> = { text: t("Text"), number: t("Number"), choice: t("Choice"), boolean: t("Yes or no"), secret: t("Secret") };

    const change = (draft: FieldDraft, changes: Partial<FieldDraft>) =>
        onChange(drafts.map(other => other === draft ? { ...draft, ...changes } : other));
    function move(index: number, by: number) {
        const next = [...drafts];
        [next[index], next[index + by]] = [next[index + by], next[index]];
        onChange(next);
    }

    return (
        <FormGroup label={t("Questions at launch")} role="group" fieldId="tpl-survey">
            <FormHelperText><HelperText><HelperTextItem>{t("Each answer becomes a variable of the run. This is how an operator gives variables to a playbook.")}</HelperTextItem></HelperText></FormHelperText>
            {drafts.map((draft, index) => {
                const id = `tpl-survey-${draft.key}`;
                const problem = draftProblem(draft, drafts);
                const bounded = draft.type === "text" || draft.type === "number";
                return (
                    <div key={draft.key} className="fleet-survey-field">
                        <Grid hasGutter md={4}>
                            <GridItem>
                                <FormGroup label={t("Variable")} isRequired fieldId={`${id}-name`}>
                                    <TextInput id={`${id}-name`} value={draft.name} onChange={(_e, value) => change(draft, { name: value })} placeholder="app_version" spellCheck={false} />
                                </FormGroup>
                            </GridItem>
                            <GridItem>
                                <FormGroup label={t("Question")} fieldId={`${id}-label`}>
                                    <TextInput id={`${id}-label`} value={draft.label} onChange={(_e, value) => change(draft, { label: value })} />
                                </FormGroup>
                            </GridItem>
                            <GridItem>
                                <FormGroup label={t("Type")} fieldId={`${id}-type`}>
                                    <FormSelect id={`${id}-type`} value={draft.type} onChange={(_e, value) => change(draft, { type: value as SurveyType, default: "", low: "", high: "" })}>
                                        {Object.entries(types).map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                                    </FormSelect>
                                </FormGroup>
                            </GridItem>
                            {draft.type === "choice" && (
                                <GridItem md={8}>
                                    <FormGroup label={t("Choices, one per line")} isRequired fieldId={`${id}-choices`}>
                                        <TextArea id={`${id}-choices`} value={draft.choices} onChange={(_e, value) => change(draft, { choices: value })} rows={3} resizeOrientation="vertical" />
                                    </FormGroup>
                                </GridItem>
                            )}
                            {bounded && (
                                <GridItem>
                                    <FormGroup label={draft.type === "text" ? t("Minimum length") : t("Minimum")} fieldId={`${id}-low`}>
                                        <TextInput id={`${id}-low`} type="number" value={draft.low} onChange={(_e, value) => change(draft, { low: value })} />
                                    </FormGroup>
                                </GridItem>
                            )}
                            {bounded && (
                                <GridItem>
                                    <FormGroup label={draft.type === "text" ? t("Maximum length") : t("Maximum")} fieldId={`${id}-high`}>
                                        <TextInput id={`${id}-high`} type="number" value={draft.high} onChange={(_e, value) => change(draft, { high: value })} />
                                    </FormGroup>
                                </GridItem>
                            )}
                            {draft.type !== "secret" && <GridItem>
                                <FormGroup label={t("Default")} fieldId={`${id}-default`}>
                                    {draft.type === "boolean" || draft.type === "choice" ? (
                                        <FormSelect id={`${id}-default`} value={draft.default} onChange={(_e, value) => change(draft, { default: value })}>
                                            <FormSelectOption value="" label={t("None")} />
                                            {draft.type === "boolean" && <FormSelectOption value="true" label={t("Yes")} />}
                                            {draft.type === "boolean" && <FormSelectOption value="false" label={t("No")} />}
                                            {draft.type === "choice" && draft.choices.split("\n").map(line => line.trim()).filter(Boolean)
                                                .map((choice, position) => <FormSelectOption key={position} value={choice} label={choice} />)}
                                        </FormSelect>
                                    ) : (
                                        <TextInput id={`${id}-default`} type={draft.type === "number" ? "number" : "text"} value={draft.default} onChange={(_e, value) => change(draft, { default: value })} />
                                    )}
                                </FormGroup>
                            </GridItem>}
                            <GridItem md={8}>
                                <FormGroup label={t("Help text")} fieldId={`${id}-help`}>
                                    <TextInput id={`${id}-help`} value={draft.help} onChange={(_e, value) => change(draft, { help: value })} />
                                </FormGroup>
                            </GridItem>
                            <GridItem md={12}>
                                <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} alignItems={{ default: "alignItemsCenter" }}>
                                    <FlexItem>
                                        {draft.type !== "boolean" && (
                                            <Checkbox id={`${id}-required`} label={t("An answer is required")} isChecked={draft.required} onChange={(_e, value) => change(draft, { required: value })} />
                                        )}
                                    </FlexItem>
                                    <Flex gap={{ default: "gapSm" }}>
                                        <Button variant="link" isInline isDisabled={index === 0} onClick={() => move(index, -1)}>{t("Move up")}</Button>
                                        <Button variant="link" isInline isDisabled={index === drafts.length - 1} onClick={() => move(index, 1)}>{t("Move down")}</Button>
                                        <Button variant="link" isInline isDanger onClick={() => onChange(drafts.filter(other => other !== draft))}>{t("Remove")}</Button>
                                    </Flex>
                                </Flex>
                                {draft.type === "secret" && (
                                    <FormHelperText><HelperText><HelperTextItem>{t("Encrypted as soon as it is typed and given to that run only. It is not kept and cannot be read back, but a playbook that prints it puts it in the output.")}</HelperTextItem></HelperText></FormHelperText>
                                )}
                                {problem && draft.name !== "" && (
                                    <FormHelperText><HelperText><HelperTextItem variant="error">{problems[problem]}</HelperTextItem></HelperText></FormHelperText>
                                )}
                            </GridItem>
                        </Grid>
                    </div>
                );
            })}
            <div>
                <Button variant="secondary" size="sm" onClick={() => onChange([...drafts, newDraft()])}>{t("Add a question")}</Button>
            </div>
        </FormGroup>
    );
}

// The questions as whoever launches the template sees them.
export function SurveyAnswers({ survey, answers, onChange }: { survey: SurveyField[]; answers: Answers; onChange: (answers: Answers) => void }) {
    const { t } = useTranslation();
    const set = (name: string, value: string | boolean) => onChange({ ...answers, [name]: value });

    function said(field: SurveyField): string | null {
        switch (answerProblem(field, answers[field.name])) {
        case "number": return t("Enter a number.");
        case "min": return t("At least {{value}}.", { value: field.min });
        case "max": return t("At most {{value}}.", { value: field.max });
        case "short": return t("At least {{length}} characters.", { length: field.min_length });
        case "long": return t("At most {{length}} characters.", { length: field.max_length });
        default: return null;
        }
    }

    return (
        <>
            {survey.map(field => {
                const id = `launch-answer-${field.name}`;
                const label = field.label || field.name;
                const value = answers[field.name];
                const text = typeof value === "string" ? value : "";
                const problem = said(field);
                return (
                    <FormGroup key={field.name} label={field.type === "boolean" ? undefined : label} isRequired={field.required && field.type !== "boolean"} fieldId={id}>
                        {field.type === "boolean" && (
                            <Checkbox id={id} label={label} isChecked={value === true} onChange={(_e, checked) => set(field.name, checked)} />
                        )}
                        {field.type === "choice" && (
                            <FormSelect id={id} value={text} onChange={(_e, chosen) => set(field.name, chosen)}>
                                {!(field.required && field.default !== undefined) && <FormSelectOption value="" label={field.required ? t("Choose") : t("None")} isPlaceholder={field.required} />}
                                {(field.choices ?? []).map(choice => <FormSelectOption key={choice} value={choice} label={choice} />)}
                            </FormSelect>
                        )}
                        {(field.type === "text" || field.type === "number") && (
                            <TextInput id={id} type={field.type === "number" ? "number" : "text"} value={text} onChange={(_e, typed) => set(field.name, typed)}
                                validated={problem ? "error" : "default"} />
                        )}
                        {field.type === "secret" && (
                            <TextInput id={id} type="password" autoComplete="new-password" value={text} onChange={(_e, typed) => set(field.name, typed)} />
                        )}
                        {(field.help || problem) && (
                            <FormHelperText>
                                <HelperText>
                                    {problem && <HelperTextItem variant="error">{problem}</HelperTextItem>}
                                    {field.help && <HelperTextItem>{field.help}</HelperTextItem>}
                                </HelperText>
                            </FormHelperText>
                        )}
                    </FormGroup>
                );
            })}
        </>
    );
}

export function Answered({ answers, secrets = [] }: { answers: Record<string, unknown>; secrets?: string[] }) {
    return (
        <Content component="ul" isPlainList>
            {secrets.map(name => <Content component="li" key={name}><code>{name}</code> = ••••••</Content>)}
            {Object.entries(answers).map(([name, value]) => (
                <Content component="li" key={name}><code>{name}</code> = <code>{typeof value === "string" ? value : JSON.stringify(value)}</code></Content>
            ))}
        </Content>
    );
}
