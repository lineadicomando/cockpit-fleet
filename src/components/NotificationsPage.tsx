import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    Alert,
    Button,
    Checkbox,
    Content,
    FormGroup,
    FormHelperText,
    FormSelect,
    FormSelectOption,
    HelperText,
    HelperTextItem,
    Label,
    NumberInput,
    Switch,
    TextArea,
    TextInput,
} from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import cockpit from "cockpit";
import { call, deleteSecret, setSecret } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { usePolling, useLoad } from "../lib/hooks.ts";
import { EMPTY_FORM, destination, formOf, settingsOf, type ChannelForm } from "../lib/notify.ts";
import { useCan } from "../lib/session.tsx";
import type { Channel, ChannelKind, CheckDef, CheckEvent, FleetHost, Inventory, Notification, NotifyEvent, NotifyRule, Project, RuleScope, RunEvent, Schedule, SystemEvent, Template } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader, message } from "./common.tsx";
import { StatusLabel } from "./StatusLabel.tsx";

const KINDS: ChannelKind[] = ["telegram", "webhook", "smtp", "matrix"];
const RUN_EVENTS: RunEvent[] = ["failed", "changed", "succeeded", "canceled"];
const CHECK_EVENTS: CheckEvent[] = ["problem", "recovery", "reminder"];
const SYSTEM_EVENTS: SystemEvent[] = ["late", "caught_up"];
// The scopes in which check cycles falling behind can be told about.
const SYSTEM_SCOPES: RuleScope[] = ["all", "inventory"];
// Scopes that only runs have, and those that only checks have.
const RUN_SCOPES: RuleScope[] = ["project", "template", "schedule"];
const CHECK_SCOPES: RuleScope[] = ["group", "check"];
const MONO = { fontFamily: "var(--pf-t--global--font--family--mono)" };

function useLabels() {
    const { t } = useTranslation();
    return {
        kinds: {
            telegram: t("Telegram"),
            webhook: t("Webhook"),
            smtp: t("Mail (SMTP)"),
            matrix: t("Matrix"),
        } as Record<ChannelKind, string>,
        secrets: {
            telegram: t("Bot token"),
            webhook: t("Secret"),
            smtp: t("Password"),
            matrix: t("Access token"),
        } as Record<ChannelKind, string>,
        events: {
            failed: t("Failed"),
            changed: t("With changes"),
            succeeded: t("Successful"),
            canceled: t("Canceled"),
            problem: t("A problem"),
            recovery: t("Its recovery"),
            reminder: t("A reminder of it"),
            late: t("Falling behind"),
            caught_up: t("Back on time"),
            test: t("Test"),
        } as Record<NotifyEvent | "test", string>,
        scopes: {
            all: t("Everything"),
            project: t("Runs of a project"),
            template: t("Runs of a job template"),
            schedule: t("Runs of a schedule"),
            inventory: t("Runs and checks of an inventory"),
            group: t("Checks of a group of hosts"),
            check: t("One check"),
        } as Record<RuleScope, string>,
    };
}

function Help({ children }: { children: React.ReactNode }) {
    return <FormHelperText><HelperText><HelperTextItem>{children}</HelperTextItem></HelperText></FormHelperText>;
}

// The fields of one kind of channel. Values are kept for every kind, so that
// switching kind while creating loses nothing.
function KindFields({ kind, form, set }: { kind: ChannelKind; form: ChannelForm; set: (key: keyof ChannelForm, value: string) => void }) {
    const { t } = useTranslation();
    const text = (key: keyof ChannelForm, label: string, help?: string, required = false, mono = false) => (
        <FormGroup label={label} isRequired={required} fieldId={`ch-${key}`}>
            <TextInput id={`ch-${key}`} value={form[key]} onChange={(_e, value) => set(key, value)} isRequired={required}
                autoComplete="off" style={mono ? MONO : undefined} />
            {help && <Help>{help}</Help>}
        </FormGroup>
    );

    if (kind === "telegram") {
        return (
            <>
                {text("chat_id", t("Chat"), t("The number of the chat or group (negative for a group), or the @name of a channel. The bot must be a member."), true)}
                {text("server", t("Bot API server"), t("Leave as it is unless you run your own Bot API server."))}
            </>
        );
    }
    if (kind === "webhook") {
        return (
            <>
                {text("url", t("URL"), t("Where the message is sent. Write $secret where the secret goes when the service wants it in the address, as Slack and Discord do."), true)}
                <FormGroup label={t("Method")} fieldId="ch-method">
                    <FormSelect id="ch-method" value={form.method} onChange={(_e, value) => set("method", value)}>
                        <FormSelectOption value="POST" label="POST" />
                        <FormSelectOption value="PUT" label="PUT" />
                    </FormSelect>
                </FormGroup>
                <FormGroup label={t("Body")} fieldId="ch-template">
                    <TextArea id="ch-template" value={form.template} rows={4} resizeOrientation="vertical" spellCheck={false}
                        onChange={(_e, value) => set("template", value)} style={MONO} />
                    <Help>{t("JSON. In its strings, $title, $text, $link and $count are replaced; a string that is just \"$events\" becomes the list of events.")}</Help>
                </FormGroup>
                {text("header", t("Header for the secret"), t("When the secret is not in the URL, it is sent in this header, as it is: write \"Bearer …\" if the service wants that."))}
            </>
        );
    }
    if (kind === "smtp") {
        return (
            <>
                {text("host", t("Mail server"), undefined, true)}
                <FormGroup label={t("Security")} fieldId="ch-security">
                    <FormSelect id="ch-security" value={form.security} onChange={(_e, value) => set("security", value)}>
                        <FormSelectOption value="starttls" label={t("STARTTLS (port 587)")} />
                        <FormSelectOption value="tls" label={t("TLS (port 465)")} />
                        <FormSelectOption value="none" label={t("None (port 25, only on a trusted network)")} />
                    </FormSelect>
                </FormGroup>
                {text("port", t("Port"), t("Empty for the usual port of the security chosen."))}
                {text("username", t("User name"), t("Empty when the server takes mail without logging in."))}
                {text("sender", t("From"), undefined, true)}
                <FormGroup label={t("To")} isRequired fieldId="ch-recipients">
                    <TextArea id="ch-recipients" value={form.recipients} rows={2} resizeOrientation="vertical"
                        onChange={(_e, value) => set("recipients", value)} />
                    <Help>{t("One address per line, at most 20.")}</Help>
                </FormGroup>
            </>
        );
    }
    return (
        <>
            {text("homeserver", t("Homeserver"), t("The address of the homeserver's client API, like https://matrix.example.org."), true)}
            {text("room_id", t("Room"), t("The identifier of the room, like !abcdef:example.org (or !abcdef in newer rooms), which the account of the token has joined."), true, true)}
        </>
    );
}

function ChannelModal({ channel, onClose, onDone }: { channel?: Channel; onClose: () => void; onDone: () => void }) {
    const { t, i18n } = useTranslation();
    const labels = useLabels();
    const [name, setName] = useState(channel?.name ?? "");
    const [kind, setKind] = useState<ChannelKind>(channel?.kind ?? "telegram");
    const [form, setForm] = useState<ChannelForm>(channel ? formOf(channel) : EMPTY_FORM);
    const [language, setLanguage] = useState(channel?.language ?? (i18n.language.startsWith("it") ? "it" : "en"));
    const [enabled, setEnabled] = useState(channel?.enabled ?? true);
    const [windowMinutes, setWindowMinutes] = useState(Math.round((channel?.window ?? 0) / 60));
    const [perHour, setPerHour] = useState(channel?.max_per_hour ?? 20);
    const [secret, setSecretValue] = useState("");

    async function submit() {
        const params = { name, settings: settingsOf(kind, form), language, enabled, window: windowMinutes * 60, max_per_hour: perHour };
        const saved = channel
            ? await call<Channel>("channel.update", { id: channel.id, ...params })
            : await call<Channel>("channel.create", { kind, ...params });
        try {
            if (secret) await setSecret(saved.secret_name, secret);
        } finally {
            onDone();
        }
        onClose();
    }

    return (
        <FormModal title={channel ? t("Edit channel") : t("New channel")} submitLabel={channel ? t("Save") : t("Create")}
            onSubmit={submit} onClose={onClose} isDisabled={!name.trim()}>
            <FormGroup label={t("Name")} isRequired fieldId="ch-name">
                <TextInput id="ch-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("Type")} fieldId="ch-kind">
                <FormSelect id="ch-kind" value={kind} isDisabled={!!channel} onChange={(_e, value) => setKind(value as ChannelKind)}>
                    {KINDS.map(key => <FormSelectOption key={key} value={key} label={labels.kinds[key]} />)}
                </FormSelect>
            </FormGroup>
            <KindFields kind={kind} form={form} set={(key, value) => setForm(current => ({ ...current, [key]: value }))} />
            <FormGroup label={labels.secrets[kind]} isRequired={kind === "telegram" || kind === "matrix"} fieldId="ch-secret">
                <TextInput id="ch-secret" type="password" autoComplete="new-password" value={secret} onChange={(_e, value) => setSecretValue(value)} />
                <Help>
                    {channel?.secret
                        ? t("Already set. Leave empty to keep it.")
                        : t("Encrypted on this machine and never shown again, not even to administrators.")}
                </Help>
            </FormGroup>
            <FormGroup label={t("Language of the messages")} fieldId="ch-language">
                <FormSelect id="ch-language" value={language} onChange={(_e, value) => setLanguage(value as "en" | "it")}>
                    <FormSelectOption value="en" label="English" />
                    <FormSelectOption value="it" label="Italiano" />
                </FormSelect>
            </FormGroup>
            <FormGroup label={t("Group events over")} fieldId="ch-window">
                <NumberInput id="ch-window" value={windowMinutes} min={0} max={60} unit={t("minutes")}
                    onMinus={() => setWindowMinutes(value => Math.max(0, value - 1))}
                    onPlus={() => setWindowMinutes(value => Math.min(60, value + 1))}
                    onChange={event => setWindowMinutes(Math.min(60, Math.max(0, Number((event.target as HTMLInputElement).value) || 0)))} />
                <Help>{t("Runs that end within this time of the first one are told in one message. 0 sends each one at once.")}</Help>
            </FormGroup>
            <FormGroup label={t("Messages an hour at most")} fieldId="ch-rate">
                <NumberInput id="ch-rate" value={perHour} min={1} max={600}
                    onMinus={() => setPerHour(value => Math.max(1, value - 1))}
                    onPlus={() => setPerHour(value => Math.min(600, value + 1))}
                    onChange={event => setPerHour(Math.min(600, Math.max(1, Number((event.target as HTMLInputElement).value) || 1)))} />
                <Help>{t("Beyond it, events wait and leave together in the next message.")}</Help>
            </FormGroup>
            <Switch id="ch-enabled" label={t("Enabled")} isChecked={enabled} onChange={(_e, on) => setEnabled(on)} />
        </FormModal>
    );
}

function RuleModal({ rule, channels, onClose, onDone }: { rule?: NotifyRule; channels: Channel[]; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const labels = useLabels();
    const [name, setName] = useState(rule?.name ?? "");
    const [channelId, setChannelId] = useState(rule?.channel_id ?? channels[0]?.id ?? "");
    const [events, setEvents] = useState<NotifyEvent[]>(rule?.events ?? ["failed"]);
    const [scope, setScope] = useState<RuleScope>(rule?.scope ?? "all");
    const [scopeId, setScopeId] = useState(rule?.scope_id ?? "");
    const [group, setGroup] = useState(rule?.group ?? "");
    const [enabled, setEnabled] = useState(rule?.enabled ?? true);
    const targets = useLoad(async () => {
        const [projects, templates, schedules, inventories, checks, hosts] = await Promise.all([
            call<{ projects: Project[] }>("project.list"),
            call<{ templates: Template[] }>("template.list"),
            call<{ schedules: Schedule[] }>("schedule.list"),
            call<{ inventories: Inventory[] }>("inventory.list"),
            call<{ checks: CheckDef[] }>("check.list"),
            call<{ hosts: FleetHost[] }>("host.list"),
        ]);
        return {
            lists: {
                project: projects.projects, template: templates.templates, schedule: schedules.schedules,
                inventory: inventories.inventories, group: inventories.inventories, check: checks.checks,
            } as Record<string, { id: string; name: string }[]>,
            hosts: hosts.hosts,
        };
    });
    const choices = scope === "all" ? [] : targets.data?.lists[scope] ?? [];
    // The groups of the inventory chosen, as its hosts last listed them.
    const groups = [...new Set((targets.data?.hosts ?? []).filter(host => host.inventory_id === scopeId).flatMap(host => host.groups))].sort();
    const runsAllowed = !CHECK_SCOPES.includes(scope);
    const checksAllowed = !RUN_SCOPES.includes(scope);
    const systemAllowed = SYSTEM_SCOPES.includes(scope);

    function changeScope(value: RuleScope) {
        setScope(value);
        setScopeId("");
        setGroup("");
        // What the scope cannot have is left out, rather than refused when saved.
        setEvents(current => current.filter(event => RUN_EVENTS.includes(event as RunEvent)
            ? !CHECK_SCOPES.includes(value)
            : SYSTEM_EVENTS.includes(event as SystemEvent) ? SYSTEM_SCOPES.includes(value) : !RUN_SCOPES.includes(value)));
    }

    async function submit() {
        const params = {
            name, channel_id: channelId, events, scope, enabled,
            ...(scope === "all" ? {} : { scope_id: scopeId }), ...(scope === "group" ? { group } : {}),
        };
        if (rule) await call("rule.update", { id: rule.id, ...params });
        else await call("rule.create", params);
        onDone();
        onClose();
    }

    return (
        <FormModal title={rule ? t("Edit rule") : t("New rule")} submitLabel={rule ? t("Save") : t("Create")} onSubmit={submit} onClose={onClose}
            isDisabled={!name.trim() || !channelId || events.length === 0 || (scope !== "all" && !scopeId) || (scope === "group" && !group)}>
            <FormGroup label={t("Name")} isRequired fieldId="rule-name">
                <TextInput id="rule-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("When a run ends")} role="group" isStack fieldId="rule-events">
                {RUN_EVENTS.map(event => (
                    <Checkbox key={event} id={`rule-event-${event}`} label={labels.events[event]} isChecked={events.includes(event)}
                        isDisabled={!runsAllowed}
                        onChange={(_e, on) => setEvents(current => on ? [...current, event] : current.filter(item => item !== event))} />
                ))}
                <Help>{t("A run with changes is also successful: choose both and it is told once, as with changes.")}</Help>
            </FormGroup>
            <FormGroup label={t("When a check on a host has")} role="group" isStack fieldId="rule-check-events">
                {CHECK_EVENTS.map(event => (
                    <Checkbox key={event} id={`rule-event-${event}`} label={labels.events[event]} isChecked={events.includes(event)}
                        isDisabled={!checksAllowed}
                        onChange={(_e, on) => setEvents(current => on ? [...current, event] : current.filter(item => item !== event))} />
                ))}
                <Help>{t("A problem is told once it is confirmed, after the attempts its check asks for. A reminder comes while nobody has acknowledged it.")}</Help>
            </FormGroup>
            <FormGroup label={t("When the check cycles of an inventory are")} role="group" isStack fieldId="rule-system-events">
                {SYSTEM_EVENTS.map(event => (
                    <Checkbox key={event} id={`rule-event-${event}`} label={labels.events[event]} isChecked={events.includes(event)}
                        isDisabled={!systemAllowed}
                        onChange={(_e, on) => setEvents(current => on ? [...current, event] : current.filter(item => item !== event))} />
                ))}
                <Help>{t("Falling behind: cycles still running when their checks are due again, as many times in a row as the configuration says. Back on time: a cycle ends with nothing skipped. For everything or for one inventory.")}</Help>
            </FormGroup>
            <FormGroup label={t("About what")} fieldId="rule-scope">
                <FormSelect id="rule-scope" value={scope} onChange={(_e, value) => changeScope(value as RuleScope)}>
                    {(Object.keys(labels.scopes) as RuleScope[]).map(key => <FormSelectOption key={key} value={key} label={labels.scopes[key]} />)}
                </FormSelect>
            </FormGroup>
            {scope !== "all" && (
                <FormGroup label={labels.scopes[scope]} isRequired fieldId="rule-target">
                    <FormSelect id="rule-target" value={scopeId} onChange={(_e, value) => setScopeId(value)} isDisabled={!targets.data}>
                        <FormSelectOption value="" label={t("Choose…")} isPlaceholder />
                        {choices.map(item => <FormSelectOption key={item.id} value={item.id} label={item.name} />)}
                    </FormSelect>
                </FormGroup>
            )}
            {scope === "group" && (
                <FormGroup label={t("Group")} isRequired fieldId="rule-group">
                    <FormSelect id="rule-group" value={group} onChange={(_e, value) => setGroup(value)} isDisabled={!scopeId}>
                        <FormSelectOption value="" label={t("Choose…")} isPlaceholder />
                        <FormSelectOption value="all" label={t("Every host")} />
                        {groups.map(name => <FormSelectOption key={name} value={name} label={name} />)}
                    </FormSelect>
                </FormGroup>
            )}
            <FormGroup label={t("Channel")} isRequired fieldId="rule-channel">
                <FormSelect id="rule-channel" value={channelId} onChange={(_e, value) => setChannelId(value)}>
                    {channels.map(channel => <FormSelectOption key={channel.id} value={channel.id} label={channel.name} />)}
                </FormSelect>
            </FormGroup>
            <Switch id="rule-enabled" label={t("Enabled")} isChecked={enabled} onChange={(_e, on) => setEnabled(on)} />
        </FormModal>
    );
}

function NotificationState({ notification }: { notification: Notification }) {
    const { t, i18n } = useTranslation();
    if (notification.state === "sent") return <StatusLabel tone="good">{t("Sent")}</StatusLabel>;
    if (notification.state === "failed") return <StatusLabel tone="bad">{t("Given up")}</StatusLabel>;
    return (
        <>
            <Label color="grey" isCompact>{notification.attempts ? t("To be tried again") : t("Waiting")}</Label>
            {notification.attempts > 0 && <Content component="small"> {formatTime(notification.next_at, i18n.language)}</Content>}
        </>
    );
}

function Outbox({ reloadKey }: { reloadKey: number }) {
    const { t, i18n } = useTranslation();
    const can = useCan();
    const labels = useLabels();
    const list = useLoad(() => call<{ notifications: Notification[] }>("notification.list", { limit: 50 }));
    const [error, setError] = useState<string | null>(null);
    const waiting = !!list.data?.notifications.some(item => item.state === "pending");
    usePolling(list.reload, 3000, waiting);
    const { reload } = list;
    useEffect(() => { if (reloadKey) reload(); }, [reloadKey, reload]);

    async function retry(notification: Notification) {
        setError(null);
        try {
            await call("notification.retry", { id: notification.id });
        } catch (ex) {
            setError(message(ex));
        }
        list.reload();
    }

    return (
        <>
            <SectionHeader title={t("Recent notifications")} />
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={list} isEmpty={data => data.notifications.length === 0}
                emptyTitle={t("No notifications yet")}
                emptyBody={t("What the rules send appears here: when it was sent, or why it was not.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Recent notifications")}>
                        <Thead>
                            <Tr>
                                <Th>{t("When")}</Th><Th>{t("Channel")}</Th><Th>{t("Event")}</Th><Th>{t("About")}</Th><Th>{t("State")}</Th><Th screenReaderText={t("Actions")} />
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.notifications.map(item => (
                                <Tr key={item.id}>
                                    <Td dataLabel={t("When")}>{formatTime(item.sent_at ?? item.created_at, i18n.language)}</Td>
                                    <Td dataLabel={t("Channel")}>{item.channel}</Td>
                                    <Td dataLabel={t("Event")}>{labels.events[item.event]}</Td>
                                    <Td dataLabel={t("About")}>
                                        {item.run_id && <Button variant="link" isInline onClick={() => cockpit.location.go(["runs", item.run_id!])}>{item.title || item.run_id}</Button>}
                                        {item.host_id && <Button variant="link" isInline onClick={() => cockpit.location.go(["hosts", item.host_id!])}>{item.title}</Button>}
                                        {!item.run_id && !item.host_id && item.title}
                                    </Td>
                                    <Td dataLabel={t("State")}>
                                        <NotificationState notification={item} />
                                        {item.error && <Content component="small" style={{ display: "block" }}>{item.error}</Content>}
                                    </Td>
                                    <Td isActionCell>
                                        {item.state === "failed" && can("notification.retry") && (
                                            <Button variant="secondary" size="sm" onClick={() => retry(item)}>{t("Try again")}</Button>
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
        </>
    );
}

export function NotificationsPage() {
    const { t } = useTranslation();
    const can = useCan();
    const labels = useLabels();
    const channels = useLoad(() => call<{ channels: Channel[] }>("channel.list"));
    const rules = useLoad(() => call<{ rules: NotifyRule[] }>("rule.list"));
    const [editingChannel, setEditingChannel] = useState<Channel | "new" | null>(null);
    const [deletingChannel, setDeletingChannel] = useState<Channel | null>(null);
    const [editingRule, setEditingRule] = useState<NotifyRule | "new" | null>(null);
    const [deletingRule, setDeletingRule] = useState<NotifyRule | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [info, setInfo] = useState<string | null>(null);
    const [sent, setSent] = useState(0);

    async function test(channel: Channel) {
        setError(null);
        setInfo(null);
        try {
            await call("channel.test", { id: channel.id });
            setInfo(t("A test message is on its way to {{name}}: see how it went below.", { name: channel.name }));
            setSent(count => count + 1);
        } catch (ex) {
            setError(message(ex));
        }
    }

    async function removeChannel(channel: Channel) {
        // The secret first: the backend refuses to forget a channel that still has one.
        if (channel.secret) await deleteSecret(channel.secret_name);
        await call("channel.delete", { id: channel.id });
        setDeletingChannel(null);
        channels.reload();
        rules.reload();
    }

    const channelList = channels.data?.channels ?? [];

    return (
        <>
            <SectionHeader title={t("Channels")}>
                {can("channel.create") && <Button variant="primary" onClick={() => setEditingChannel("new")}>{t("New channel")}</Button>}
            </SectionHeader>
            {error && <Alert variant="danger" isInline title={error} style={{ marginBottom: "1rem" }} />}
            {info && <Alert variant="info" isInline title={info} style={{ marginBottom: "1rem" }} />}
            <ListState loaded={channels} isEmpty={data => data.channels.length === 0}
                emptyTitle={t("No channels")}
                emptyBody={t("A channel is where notifications go: a Telegram chat, a webhook, a mail address, a Matrix room. Its token or password is encrypted and never shown again.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Channels")}>
                        <Thead>
                            <Tr>
                                <Th>{t("Name")}</Th><Th>{t("Type")}</Th><Th>{t("Destination")}</Th><Th>{t("State")}</Th><Th screenReaderText={t("Send a test")} /><Th screenReaderText={t("Actions")} />
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.channels.map(channel => (
                                <Tr key={channel.id}>
                                    <Td dataLabel={t("Name")}>{channel.name}</Td>
                                    <Td dataLabel={t("Type")}>{labels.kinds[channel.kind]}</Td>
                                    <Td dataLabel={t("Destination")} modifier="truncate">{destination(channel)}</Td>
                                    <Td dataLabel={t("State")}>
                                        {!channel.enabled && <Label color="grey" isCompact>{t("Off")}</Label>}
                                        {channel.enabled && channel.secret_required && !channel.secret && <StatusLabel tone="warning">{t("Secret not set")}</StatusLabel>}
                                        {channel.enabled && (channel.secret || !channel.secret_required) && channel.failed === 0 && <StatusLabel tone="good">{t("Ready")}</StatusLabel>}
                                        {channel.enabled && channel.failed > 0 && <StatusLabel tone="bad">{t("given_up_count", { count: channel.failed })}</StatusLabel>}
                                    </Td>
                                    <Td isActionCell style={{ whiteSpace: "nowrap" }}>
                                        {can("channel.test") && <Button variant="secondary" size="sm" onClick={() => test(channel)}>{t("Send a test")}</Button>}
                                    </Td>
                                    <Td isActionCell>
                                        {can("channel.update") && (
                                            <ActionsColumn items={[
                                                { title: t("Edit"), onClick: () => setEditingChannel(channel) },
                                                { title: t("Delete"), onClick: () => setDeletingChannel(channel) },
                                            ]} />
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>

            <div style={{ marginBlockStart: "2rem" }}>
                <SectionHeader title={t("Rules")}>
                    {can("rule.create") && (
                        <Button variant="secondary" onClick={() => setEditingRule("new")} isDisabled={channelList.length === 0}>{t("New rule")}</Button>
                    )}
                </SectionHeader>
                <ListState loaded={rules} isEmpty={data => data.rules.length === 0}
                    emptyTitle={t("No rules")}
                    emptyBody={t("A rule says what to tell about, and through which channel: for example, every failed run, the runs of one schedule with changes, or the problems of the checks of a group of hosts.")}>
                    {data => (
                        <Table variant="compact" aria-label={t("Rules")}>
                            <Thead>
                                <Tr>
                                    <Th>{t("Name")}</Th><Th>{t("When")}</Th><Th>{t("About what")}</Th><Th>{t("Channel")}</Th><Th>{t("State")}</Th><Th screenReaderText={t("Actions")} />
                                </Tr>
                            </Thead>
                            <Tbody>
                                {data.rules.map(rule => (
                                    <Tr key={rule.id}>
                                        <Td dataLabel={t("Name")}>{rule.name}</Td>
                                        <Td dataLabel={t("When")}>
                                            {rule.events.map(event => <Label key={event} isCompact style={{ marginInlineEnd: "0.25rem" }}>{labels.events[event]}</Label>)}
                                        </Td>
                                        <Td dataLabel={t("About what")}>
                                            {rule.scope === "all" ? labels.scopes.all : `${labels.scopes[rule.scope]}: ${rule.scope_name}${rule.scope === "group" ? ` / ${rule.group === "all" ? t("Every host") : rule.group}` : ""}`}
                                        </Td>
                                        <Td dataLabel={t("Channel")}>{rule.channel}</Td>
                                        <Td dataLabel={t("State")}>{rule.enabled ? t("Enabled") : t("Off")}</Td>
                                        <Td isActionCell>
                                            {can("rule.update") && (
                                                <ActionsColumn items={[
                                                    { title: t("Edit"), onClick: () => setEditingRule(rule) },
                                                    { title: t("Delete"), onClick: () => setDeletingRule(rule) },
                                                ]} />
                                            )}
                                        </Td>
                                    </Tr>
                                ))}
                            </Tbody>
                        </Table>
                    )}
                </ListState>
            </div>

            <div style={{ marginBlockStart: "2rem" }}>
                <Outbox reloadKey={sent} />
            </div>

            {editingChannel && (
                <ChannelModal channel={editingChannel === "new" ? undefined : editingChannel}
                    onClose={() => setEditingChannel(null)} onDone={channels.reload} />
            )}
            {deletingChannel && (
                <ConfirmDelete name={deletingChannel.name}
                    what={t("The channel, its secret, its rules and its notifications are deleted. This cannot be undone.")}
                    onDelete={() => removeChannel(deletingChannel)} onClose={() => setDeletingChannel(null)} />
            )}
            {editingRule && (
                <RuleModal rule={editingRule === "new" ? undefined : editingRule} channels={channelList}
                    onClose={() => setEditingRule(null)} onDone={rules.reload} />
            )}
            {deletingRule && (
                <ConfirmDelete name={deletingRule.name} what={t("The rule is deleted. What it already sent stays in the list.")}
                    onDelete={async () => { await call("rule.delete", { id: deletingRule.id }); setDeletingRule(null); rules.reload(); }}
                    onClose={() => setDeletingRule(null)} />
            )}
        </>
    );
}
