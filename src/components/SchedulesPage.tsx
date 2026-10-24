import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Alert,
    Button,
    Checkbox,
    Content,
    Flex,
    FormGroup,
    FormHelperText,
    FormSelect,
    FormSelectOption,
    Grid,
    GridItem,
    HelperText,
    HelperTextItem,
    InputGroup,
    InputGroupItem,
    Switch,
    TextInput,
} from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { DEFAULT_SHAPE, EVERY_MINUTES, WEEKDAYS, calendarOf, shapeOf, type Repeat, type Shape } from "../lib/schedule.ts";
import { useCan } from "../lib/session.tsx";
import { answerProblem, collectAnswers, storedAnswers, type Answers } from "../lib/survey.ts";
import type { Overlap, Run, Schedule, SchedulePreview, Template } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, RunStatusLabel, SectionHeader, message } from "./common.tsx";
import { StatusLabel } from "./StatusLabel.tsx";
import { SurveyAnswers } from "./SurveyEditor.tsx";

// The time zones the browser knows, to pick from; the backend checks the name.
const TIME_ZONES: string[] = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];

// When the calendar says, from the backend: the expression is systemd's to read.
function Preview({ calendar, timezone }: { calendar: string; timezone: string }) {
    const { t, i18n } = useTranslation();
    const [preview, setPreview] = useState<SchedulePreview | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setPreview(null);
        setError(null);
        if (!calendar.trim()) return;
        let current = true;
        const timer = window.setTimeout(() => {
            call<SchedulePreview>("schedule.preview", { calendar, timezone: timezone.trim(), count: 5 }).then(
                result => { if (current) setPreview(result) },
                ex => { if (current) setError(message(ex)) });
        }, 400);
        return () => { current = false; window.clearTimeout(timer) };
    }, [calendar, timezone]);

    if (error) return <HelperTextItem variant="error">{error}</HelperTextItem>;
    if (!preview) return null;
    if (preview.next.length === 0) return <HelperTextItem variant="error">{t("This calendar has no time left to run at.")}</HelperTextItem>;
    return (
        <HelperTextItem>
            {t("Next times: {{times}}", { times: preview.next.map(time => formatTime(time, i18n.language)).join(" · ") })}
        </HelperTextItem>
    );
}

function CalendarBuilder({ calendar, onChange }: { calendar: string; onChange: (calendar: string) => void }) {
    const { t } = useTranslation();
    const [shape, setShape] = useState<Shape>(() => calendar ? shapeOf(calendar) : DEFAULT_SHAPE);
    const [cron, setCron] = useState("");
    const [cronError, setCronError] = useState<string | null>(null);

    function change(next: Partial<Shape>) {
        const changed = { ...shape, ...next };
        setShape(changed);
        const expression = calendarOf(changed);
        if (expression !== null) onChange(expression);
    }

    async function convert() {
        setCronError(null);
        try {
            const result = await call<SchedulePreview>("schedule.preview", { cron: cron.trim(), count: 1 });
            setShape(shapeOf(result.calendar));
            onChange(result.calendar);
            setCron("");
        } catch (ex) {
            setCronError(message(ex));
        }
    }

    const repeats: [Repeat, string][] = [
        ["minutes", t("Every few minutes")], ["hourly", t("Every hour")], ["daily", t("Every day")],
        ["weekdays", t("Monday to Friday")], ["weekly", t("Every week")], ["monthly", t("Every month")],
        ["custom", t("Custom expression")],
    ];
    const dayNames: Record<string, string> = {
        Mon: t("Monday"), Tue: t("Tuesday"), Wed: t("Wednesday"), Thu: t("Thursday"), Fri: t("Friday"), Sat: t("Saturday"), Sun: t("Sunday"),
    };
    const withTime = ["daily", "weekdays", "weekly", "monthly"].includes(shape.repeat);

    return (
        <>
            <Grid hasGutter md={4}>
                <GridItem>
                    <FormGroup label={t("Repeat")} fieldId="sch-repeat">
                        <FormSelect id="sch-repeat" value={shape.repeat} onChange={(_e, value) => change({ repeat: value as Repeat })}>
                            {repeats.map(([value, label]) => <FormSelectOption key={value} value={value} label={label} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                {shape.repeat === "minutes" && (
                    <GridItem>
                        <FormGroup label={t("Every")} fieldId="sch-every">
                            <FormSelect id="sch-every" value={String(shape.every)} onChange={(_e, value) => change({ every: Number(value) })}>
                                {EVERY_MINUTES.map(value => <FormSelectOption key={value} value={String(value)} label={t("minutes_count", { count: value })} />)}
                            </FormSelect>
                        </FormGroup>
                    </GridItem>
                )}
                {shape.repeat === "hourly" && (
                    <GridItem>
                        <FormGroup label={t("At minute")} fieldId="sch-minute">
                            <TextInput id="sch-minute" type="number" min={0} max={59} value={String(shape.minute)} onChange={(_e, value) => change({ minute: Number(value) })} />
                        </FormGroup>
                    </GridItem>
                )}
                {shape.repeat === "weekly" && (
                    <GridItem>
                        <FormGroup label={t("On")} fieldId="sch-weekday">
                            <FormSelect id="sch-weekday" value={shape.weekday} onChange={(_e, value) => change({ weekday: value })}>
                                {WEEKDAYS.map(day => <FormSelectOption key={day} value={day} label={dayNames[day]} />)}
                            </FormSelect>
                        </FormGroup>
                    </GridItem>
                )}
                {shape.repeat === "monthly" && (
                    <GridItem>
                        <FormGroup label={t("On day")} fieldId="sch-day">
                            <TextInput id="sch-day" type="number" min={1} max={28} value={String(shape.day)} onChange={(_e, value) => change({ day: Number(value) })} />
                        </FormGroup>
                    </GridItem>
                )}
                {withTime && (
                    <GridItem>
                        <FormGroup label={t("At")} fieldId="sch-time">
                            <TextInput id="sch-time" type="time" value={shape.time} onChange={(_e, value) => change({ time: value })} />
                        </FormGroup>
                    </GridItem>
                )}
            </Grid>
            <FormGroup label={t("Calendar")} isRequired fieldId="sch-calendar">
                <TextInput id="sch-calendar" value={calendar} isRequired
                    onChange={(_e, value) => { setShape({ ...shape, repeat: "custom" }); onChange(value); }} />
                <FormHelperText>
                    <HelperText>
                        <HelperTextItem>{t("An OnCalendar expression of systemd, such as \"Mon..Fri *-*-* 06:00\" or \"*:0/15\". The builder above writes it; it can also be typed.")}</HelperTextItem>
                    </HelperText>
                </FormHelperText>
            </FormGroup>
            <FormGroup label={t("From a cron line")} fieldId="sch-cron">
                <InputGroup>
                    <InputGroupItem isFill>
                        <TextInput id="sch-cron" value={cron} onChange={(_e, value) => setCron(value)} placeholder="30 6 * * 1-5" />
                    </InputGroupItem>
                    <InputGroupItem>
                        <Button variant="control" onClick={convert} isDisabled={!cron.trim()}>{t("Convert")}</Button>
                    </InputGroupItem>
                </InputGroup>
                {cronError && (
                    <FormHelperText><HelperText><HelperTextItem variant="error">{cronError}</HelperTextItem></HelperText></FormHelperText>
                )}
            </FormGroup>
        </>
    );
}

function ScheduleModal({ schedule, templates, onClose, onDone }: { schedule?: Schedule; templates: Template[]; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [name, setName] = useState(schedule?.name ?? "");
    const [templateId, setTemplateId] = useState(schedule?.template_id ?? "");
    const [calendar, setCalendar] = useState(schedule?.calendar ?? calendarOf(DEFAULT_SHAPE)!);
    const [timezone, setTimezone] = useState(schedule?.timezone ?? "");
    const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
    const [limit, setLimit] = useState(schedule?.limit ?? "");
    const [check, setCheck] = useState(schedule?.check ?? false);
    const [overlap, setOverlap] = useState<Overlap>(schedule?.overlap ?? "skip");
    const [catchUp, setCatchUp] = useState(schedule?.catch_up ?? false);
    const [priority, setPriority] = useState(String(schedule?.priority ?? 0));
    const [delay, setDelay] = useState(String((schedule?.random_delay ?? 0) / 60));
    const template = templates.find(item => item.id === templateId);
    const [answers, setAnswers] = useState<Answers>(() => storedAnswers(template, schedule?.answers ?? {}));

    // A secret is never stored with a schedule: the questions that ask for one are left out.
    const survey = (template?.survey ?? []).filter(field => field.type !== "secret");
    const needsSecret = (template?.survey ?? []).some(field => field.type === "secret" && field.required);

    async function submit() {
        const params = {
            name, template_id: templateId, calendar: calendar.trim(), timezone: timezone.trim(), enabled,
            limit: limit.trim(), check, answers: collectAnswers(survey, answers), overlap, catch_up: catchUp,
            priority: Number(priority), random_delay: Math.round(Number(delay) * 60),
        };
        await call(schedule ? "schedule.update" : "schedule.create", schedule ? { id: schedule.id, ...params } : params);
        onDone();
        onClose();
    }

    return (
        <FormModal title={schedule ? t("Edit schedule") : t("New schedule")} submitLabel={t("Save")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || !templateId || !calendar.trim() || needsSecret || survey.some(field => answerProblem(field, answers[field.name]))}>
            <Grid hasGutter md={6}>
                <GridItem>
                    <FormGroup label={t("Name")} isRequired fieldId="sch-name">
                        <TextInput id="sch-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Job template")} isRequired fieldId="sch-template">
                        <FormSelect id="sch-template" value={templateId}
                            onChange={(_e, value) => { setTemplateId(value); setAnswers(storedAnswers(templates.find(item => item.id === value), {})); }}>
                            <FormSelectOption value="" label={t("Choose a job template")} isPlaceholder />
                            {templates.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                        </FormSelect>
                    </FormGroup>
                </GridItem>
            </Grid>
            {needsSecret && <Alert variant="warning" isInline title={t("This job template asks for a secret at launch, so it cannot be scheduled: a schedule never stores a secret.")} />}
            <CalendarBuilder calendar={calendar} onChange={setCalendar} />
            <FormGroup label={t("Time zone")} fieldId="sch-timezone">
                <TextInput id="sch-timezone" value={timezone} onChange={(_e, value) => setTimezone(value)} list="sch-timezones" placeholder={t("The controller's own")} />
                <datalist id="sch-timezones">
                    {TIME_ZONES.map(zone => <option key={zone} value={zone} />)}
                </datalist>
                <FormHelperText><HelperText><Preview calendar={calendar} timezone={timezone} /></HelperText></FormHelperText>
            </FormGroup>
            <SurveyAnswers survey={survey} answers={answers} onChange={setAnswers} />
            <FormGroup label={t("Limit to hosts")} fieldId="sch-limit">
                <TextInput id="sch-limit" value={limit} onChange={(_e, value) => setLimit(value)} placeholder={template?.limit_hosts || "web01,web02"} />
                <FormHelperText><HelperText><HelperTextItem>{t("Narrows the template's own limit; it cannot widen it.")}</HelperTextItem></HelperText></FormHelperText>
            </FormGroup>
            <Grid hasGutter md={4}>
                <GridItem>
                    <FormGroup label={t("If the previous run is not over")} fieldId="sch-overlap">
                        <FormSelect id="sch-overlap" value={overlap} onChange={(_e, value) => setOverlap(value as Overlap)}>
                            <FormSelectOption value="skip" label={t("Skip this time")} />
                            <FormSelectOption value="queue" label={t("Queue this one too")} />
                            <FormSelectOption value="cancel" label={t("Cancel the previous one")} />
                        </FormSelect>
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Priority in the queue")} fieldId="sch-priority">
                        <TextInput id="sch-priority" type="number" min={-10} max={10} value={priority} onChange={(_e, value) => setPriority(value)} />
                    </FormGroup>
                </GridItem>
                <GridItem>
                    <FormGroup label={t("Random delay, up to (minutes)")} fieldId="sch-delay">
                        <TextInput id="sch-delay" type="number" min={0} max={360} value={delay} onChange={(_e, value) => setDelay(value)} />
                    </FormGroup>
                </GridItem>
            </Grid>
            <FormGroup fieldId="sch-flags" role="group">
                <Checkbox id="sch-catch-up" label={t("When times were missed while the controller was off, launch once at start-up")} isChecked={catchUp} onChange={(_e, value) => setCatchUp(value)} />
                <Checkbox id="sch-check" label={t("Dry run: show what would change, change nothing")} isChecked={check} onChange={(_e, value) => setCheck(value)} />
                <Checkbox id="sch-enabled" label={t("Enabled")} isChecked={enabled} onChange={(_e, value) => setEnabled(value)} />
            </FormGroup>
        </FormModal>
    );
}

function LastTime({ schedule }: { schedule: Schedule }) {
    const { t, i18n } = useTranslation();
    if (!schedule.last_at) return <>{t("Never")}</>;
    const when = formatTime(schedule.last_at, i18n.language);
    let outcome: React.ReactNode;
    if (schedule.last_outcome === "launched" && schedule.last_run_id) {
        outcome = (
            <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", schedule.last_run_id!])}>
                {schedule.last_status ? <RunStatusLabel status={schedule.last_status} /> : t("Run")}
            </Button>
        );
    } else if (schedule.last_outcome === "error") {
        outcome = <StatusLabel tone="bad">{t("Not launched")}</StatusLabel>;
    } else if (schedule.last_outcome === "skipped") {
        outcome = <StatusLabel tone="warning">{t("Skipped: the previous run was not over")}</StatusLabel>;
    } else if (schedule.last_outcome === "missed") {
        outcome = <StatusLabel tone="warning">{t("Missed while the controller was off")}</StatusLabel>;
    }
    return (
        <Flex direction={{ default: "column" }} gap={{ default: "gapXs" }}>
            <span>{when}</span>
            {outcome}
            {schedule.last_error && <Content component="small">{schedule.last_error}</Content>}
        </Flex>
    );
}

export function SchedulesPage() {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const list = useLoad(() => call<{ schedules: Schedule[] }>("schedule.list"));
    const templates = useLoad(() => call<{ templates: Template[] }>("template.list"));
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<Schedule | null>(null);
    const [deleting, setDeleting] = useState<Schedule | null>(null);
    const [error, setError] = useState<string | null>(null);

    async function act(action: () => Promise<unknown>) {
        setError(null);
        try {
            await action();
        } catch (ex) {
            setError(message(ex));
        }
        list.reload();
    }

    async function runNow(schedule: Schedule) {
        setError(null);
        try {
            const run = await call<Run>("schedule.run", { id: schedule.id });
            cockpit.location.go(["runs", run.id]);
        } catch (ex) {
            setError(message(ex));
        }
    }

    return (
        <>
            <SectionHeader title={t("Schedules")}>
                {can("schedule.create") && (
                    <Button variant="primary" onClick={() => setCreating(true)} isDisabled={!templates.data}>{t("New schedule")}</Button>
                )}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.schedules.length === 0}
                emptyTitle={t("No schedules")}
                emptyBody={t("A schedule launches a job template at the times of a calendar: every night, every quarter of an hour, on the first of the month. Its runs go through the same queue as the others.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Schedules")}>
                        <Thead>
                            <Tr>
                                <Th>{t("Name")}</Th><Th>{t("Job template")}</Th><Th>{t("Calendar")}</Th><Th>{t("Next time")}</Th>
                                <Th>{t("Last time")}</Th><Th>{t("Enabled")}</Th><Th screenReaderText={t("Run now")} /><Th screenReaderText={t("Actions")} />
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.schedules.map(schedule => (
                                <Tr key={schedule.id}>
                                    <Td dataLabel={t("Name")}>{schedule.name}</Td>
                                    <Td dataLabel={t("Job template")}>{schedule.template}</Td>
                                    <Td dataLabel={t("Calendar")}>
                                        <code>{schedule.calendar}</code>
                                        {schedule.timezone && <Content component="small"> {schedule.timezone}</Content>}
                                    </Td>
                                    <Td dataLabel={t("Next time")}>
                                        {schedule.next_at ? formatTime(schedule.next_at, i18n.language) : (schedule.enabled ? t("Never") : t("Off"))}
                                    </Td>
                                    <Td dataLabel={t("Last time")}><LastTime schedule={schedule} /></Td>
                                    <Td dataLabel={t("Enabled")}>
                                        <Switch id={`sch-switch-${schedule.id}`} aria-label={t("Enabled")} isChecked={schedule.enabled}
                                            isDisabled={!can("schedule.enable")}
                                            onChange={(_e, on) => act(() => call(on ? "schedule.enable" : "schedule.disable", { id: schedule.id }))} />
                                    </Td>
                                    <Td isActionCell style={{ whiteSpace: "nowrap" }}>
                                        {can("schedule.run") && <Button variant="secondary" size="sm" onClick={() => runNow(schedule)}>{t("Run now")}</Button>}
                                    </Td>
                                    <Td isActionCell>
                                        {can("schedule.update") && (
                                            <ActionsColumn items={[
                                                { title: t("Edit"), onClick: () => setEditing(schedule) },
                                                { title: t("Delete"), onClick: () => setDeleting(schedule) },
                                            ]} />
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {creating && templates.data && <ScheduleModal templates={templates.data.templates} onClose={() => setCreating(false)} onDone={list.reload} />}
            {editing && templates.data && <ScheduleModal schedule={editing} templates={templates.data.templates} onClose={() => setEditing(null)} onDone={list.reload} />}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The schedule is deleted. The runs it launched stay in the history.")}
                    onDelete={async () => { await call("schedule.delete", { id: deleting.id }); setDeleting(null); list.reload(); }}
                    onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
