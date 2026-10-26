import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import {
    Button,
    Content,
    DescriptionList,
    DescriptionListDescription,
    DescriptionListGroup,
    DescriptionListTerm,
    EmptyState,
    EmptyStateBody,
    ExpandableSection,
    Flex,
    FlexItem,
    FormSelect,
    FormSelectOption,
    Label,
    ModalBody,
    ModalFooter,
    ModalHeader,
    ModalVariant,
    TextArea,
} from "@patternfly/react-core";
import type { Item, ToolItem, ToolStatus } from "../lib/acp.ts";
import { chat } from "../lib/chat-agent.ts";
import type { Ask, ChatState } from "../lib/chat.ts";
import { call } from "../lib/fleet.ts";
import { formatTime } from "../lib/format.ts";
import { useLoad } from "../lib/hooks.ts";
import { useSession } from "../lib/session.tsx";
import type { ChatAgentInfo, ChatInfo } from "../lib/types.ts";
import { SectionHeader, TerminalCommand as Command } from "./common.tsx";
import { StatusLabel } from "./StatusLabel.tsx";
import { Alert, Modal, Spinner } from "./pf.tsx";

function shown(value: unknown): string {
    return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function ToolCall({ item }: { item: ToolItem }) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const status: Record<ToolStatus, React.ReactNode> = {
        pending: <Label isCompact color="grey">{t("Waiting")}</Label>,
        in_progress: <Label isCompact color="blue" icon={<Spinner size="sm" aria-hidden />}>{t("Running")}</Label>,
        completed: <StatusLabel tone="good">{t("Done")}</StatusLabel>,
        failed: <StatusLabel tone="bad">{t("Failed")}</StatusLabel>,
    };
    return (
        <div className="fleet-chat-tool">
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                <FlexItem>{status[item.status]}</FlexItem>
                <FlexItem>
                    <ExpandableSection toggleText={item.tool || item.title || t("Tool")} isExpanded={open}
                        onToggle={(_event, expanded) => setOpen(expanded)} isIndented>
                        {item.input !== null && <pre className="fleet-log">{shown(item.input)}</pre>}
                        {item.output && <pre className="fleet-log">{item.output}</pre>}
                    </ExpandableSection>
                </FlexItem>
            </Flex>
        </div>
    );
}

function Conversation({ items, busy, children }: { items: Item[]; busy: boolean; children?: React.ReactNode }) {
    const { t } = useTranslation();
    const log = useRef<HTMLDivElement>(null);
    // The box scrolls, not the page: the message box below must stay in sight.
    useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [items, busy]);
    const who: Record<string, string> = { user: t("You"), agent: t("Assistant"), thought: t("Assistant, thinking") };
    return (
        <div ref={log} className="fleet-chat-log" role="log" aria-label={t("Conversation")}>
            {items.length === 0 && children}
            {items.map((item, index) => {
                if (item.kind === "tool") return <ToolCall key={`tool-${item.id}`} item={item} />;
                if (item.kind === "error") return <Alert key={index} variant="danger" isInline isPlain title={item.text} />;
                return (
                    <div key={index} className={`fleet-chat-message fleet-chat-${item.kind}`}>
                        <div className="fleet-chat-who">{who[item.kind]}</div>
                        {item.text}
                    </div>
                );
            })}
            {busy && <Spinner size="md" aria-label={t("The assistant is answering")} />}
        </div>
    );
}

const MIN_HEIGHT = 320;

// The height left below an element, down to the bottom of the window: what
// the chat takes, so that its message box is never pushed out of sight.
function useHeightBelow(active: boolean): [React.RefObject<HTMLDivElement>, number | undefined] {
    const ref = useRef<HTMLDivElement>(null);
    const [height, setHeight] = useState<number>();
    useEffect(() => {
        if (!active) return;
        const measure = () => {
            const element = ref.current;
            if (!element) return;
            // Where it begins in the page, however the page is scrolled. In
            // PatternFly 6 what scrolls is the rounded box around the page,
            // which ends above the bottom of the window.
            const scroller = element.closest(".pf-v6-c-page__main-container") ?? document.documentElement;
            const top = element.getBoundingClientRect().top + scroller.scrollTop;
            // What comes after it is the padding of what holds it.
            let below = 0;
            for (let parent = element.parentElement; parent && parent !== scroller.parentElement; parent = parent.parentElement) {
                below += parseFloat(getComputedStyle(parent).paddingBlockEnd) || 0;
            }
            setHeight(Math.max(MIN_HEIGHT, Math.floor(window.innerHeight - top - below)));
            // Whatever else is below it shows as a page that still scrolls.
            requestAnimationFrame(() => {
                const over = scroller.scrollHeight - scroller.clientHeight;
                if (over > 0) setHeight(now => (now ? Math.max(MIN_HEIGHT, now - over) : now));
            });
        };
        measure();
        window.addEventListener("resize", measure);
        return () => window.removeEventListener("resize", measure);
    }, [active]);
    return [ref, active ? height : undefined];
}

// A call that changes something, said in words before it is made.
function AskModal({ ask }: { ask: Ask }) {
    const { t } = useTranslation();
    const fields = ask.input && typeof ask.input === "object" && !Array.isArray(ask.input)
        ? Object.entries(ask.input as Record<string, unknown>)
        : [];
    const explained = ask.tool?.inputSchema.properties ?? {};
    return (
        <Modal variant={ModalVariant.medium} isOpen onClose={() => chat.answer("refuse")}>
            <ModalHeader title={t("Allow this action?")} titleIconVariant="warning" />
            <ModalBody>
                {ask.tool
                    ? <Content component="p">{ask.tool.description}</Content>
                    : <Alert variant="warning" isInline title={t("The assistant asks to use something that is not one of your tools in Fleet: {{name}}", { name: ask.title })} />}
                {fields.length > 0 && (
                    <DescriptionList isHorizontal isCompact style={{ marginBlockStart: "1rem" }}>
                        {fields.map(([name, value]) => (
                            <DescriptionListGroup key={name}>
                                <DescriptionListTerm>{name}</DescriptionListTerm>
                                <DescriptionListDescription>
                                    <code className="fleet-chat-value">{shown(value)}</code>
                                    {explained[name]?.description && <Content component="small">{explained[name].description}</Content>}
                                </DescriptionListDescription>
                            </DescriptionListGroup>
                        ))}
                    </DescriptionList>
                )}
                {fields.length === 0 && ask.input !== null && <pre className="fleet-log">{shown(ask.input)}</pre>}
            </ModalBody>
            <ModalFooter>
                <Button variant="primary" onClick={() => chat.answer("once")}>{t("Allow once")}</Button>
                {ask.tool && <Button variant="secondary" onClick={() => chat.answer("always")}>{t("Always allow in this conversation")}</Button>}
                <Button variant="link" onClick={() => chat.answer("refuse")}>{t("Refuse")}</Button>
            </ModalFooter>
        </Modal>
    );
}

// What happens when the chat is started, said before it is.
function Start({ info, state }: { info: ChatInfo; state: ChatState }) {
    const { t } = useTranslation();
    const [name, setName] = useState(state.agent || info.agents[0].name);
    const full = info.open_sessions >= info.max_sessions;
    const agent = info.agents.find(one => one.name === name) ?? info.agents[0];
    return (
        <>
            {state.error && <Alert variant="danger" isInline title={t("The assistant ended")} style={{ marginBlockEnd: "1rem" }}><pre>{state.error}</pre></Alert>}
            <Content component="p">{t("An assistant that works on this controller through Fleet: it reads what you may read and launches what you may launch, and asks you before every action that changes something.")}</Content>
            <Content component="ul">
                <Content component="li">{t("What you write, and what Fleet answers to the assistant (inventories, job templates, the outcome of runs and, when asked, their output), is sent to the provider of the model.")}</Content>
                <Content component="li">{t("It acts with your role, and never above the one set for agents on this controller: {{role}}.", { role: info.max_role })}</Content>
                <Content component="li">{t("It runs while this page is open: closing or reloading the page ends it. The conversation is kept, and can be continued later.")}</Content>
            </Content>
            {full && <Alert variant="info" isInline title={t("{{open}} sessions of the assistant are open on this controller, the most it allows.", { open: info.open_sessions })} style={{ marginBlockEnd: "1rem" }} />}
            {!agent.installed && (
                <Alert variant="warning" isInline title={t("{{agent}} is not installed on this machine", { agent: agent.label })} style={{ marginBlockEnd: "1rem" }}>
                    {agent.setup
                        ? (
                            <>
                                <Content component="p">{t("An administrator of this machine installs it once, for all users, from a terminal:")}</Content>
                                <Command text={agent.setup} />
                            </>
                        )
                        : <Content component="p">{t("An administrator of this machine has to install it, or correct its command in /etc/cockpit-fleet/config.toml.")}</Content>}
                </Alert>
            )}
            <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsCenter" }}>
                {info.agents.length > 1 && (
                    <FlexItem>
                        <FormSelect value={name} onChange={(_event, value) => setName(value)} aria-label={t("Assistant")}>
                            {info.agents.map(agent => <FormSelectOption key={agent.name} value={agent.name} label={agent.label} />)}
                        </FormSelect>
                    </FlexItem>
                )}
                <Button variant="primary" isDisabled={!agent.installed} onClick={() => chat.start(agent.name)}>{t("Start the assistant")}</Button>
            </Flex>
        </>
    );
}

function Composer({ state }: { state: ChatState }) {
    const { t } = useTranslation();
    const [text, setText] = useState("");
    const idle = !state.busy && !state.loading;

    function send() {
        if (!idle || !text.trim()) return;
        chat.send(text.trim());
        setText("");
    }

    return (
        <Flex gap={{ default: "gapSm" }} alignItems={{ default: "alignItemsFlexEnd" }} flexWrap={{ default: "nowrap" }}>
            <FlexItem grow={{ default: "grow" }}>
                <TextArea value={text} onChange={(_event, value) => setText(value)} rows={2} resizeOrientation="vertical"
                    aria-label={t("Message")} placeholder={t("Write a message; Enter sends it, Shift+Enter goes to a new line")}
                    onKeyDown={event => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                            event.preventDefault();
                            send();
                        }
                    }} />
            </FlexItem>
            {state.busy
                ? <Button variant="secondary" onClick={() => chat.cancel()}>{t("Stop")}</Button>
                : <Button variant="primary" onClick={send} isDisabled={!idle || !text.trim()}>{t("Send")}</Button>}
        </Flex>
    );
}

function Login({ agent }: { agent: ChatAgentInfo | undefined }) {
    const { t } = useTranslation();
    return (
        <Alert variant="warning" isInline title={t("The assistant is not logged in to its model")} style={{ marginBlockEnd: "1rem" }}>
            <Content component="p">{t("Log in once, as your user, from a terminal on this machine, then send the message again. The login is kept for the next times.")}</Content>
            {agent?.login && <Command text={agent.login} />}
        </Alert>
    );
}

export function ChatPage() {
    const { t, i18n } = useTranslation();
    const session = useSession();
    const state = useSyncExternalStore(chat.subscribe, chat.getState);
    // Asked again when the agent starts or ends: the sessions open change.
    const info = useLoad(() => call<ChatInfo>("chat.agents"));
    const reload = info.reload;
    useEffect(() => { if (state.phase === "ended" || state.phase === "idle") reload(); }, [state.phase, reload]);
    const [box, height] = useHeightBelow(state.phase === "ready" && Boolean(info.data));

    if (info.error) return <Alert variant="danger" isInline title={info.error.message} />;
    if (!info.data) return <Spinner aria-label={t("Loading")} />;
    if (info.data.unavailable || info.data.agents.length === 0) {
        return (
            <EmptyState titleText={t("The assistant cannot be used yet")} headingLevel="h3">
                <EmptyStateBody>
                    {t("The assistant works through the agents of Fleet, which an administrator of this machine enables in /etc/cockpit-fleet/config.toml ([mcp] enabled = true), together with the agent to start ([chat]).")}
                    {session.role === "admin" && info.data.unavailable && <><br />{info.data.unavailable}</>}
                </EmptyStateBody>
            </EmptyState>
        );
    }

    const agent = info.data.agents.find(one => one.name === state.agent);
    const running = state.phase === "ready";
    const idle = !state.busy && !state.loading;
    return (
        <div ref={box} className={running ? "fleet-chat" : undefined} style={{ blockSize: height }}>
            <SectionHeader title={running && agent ? t("Assistant: {{agent}}", { agent: agent.label }) : t("Assistant")}>
                {running && (
                    <>
                        {state.sessions.length > 0 && (
                            <FlexItem style={{ inlineSize: "20rem", maxInlineSize: "100%" }}>
                                <FormSelect value={state.sessions.some(one => one.sessionId === state.sessionId) ? state.sessionId : ""}
                                    isDisabled={!idle} aria-label={t("Earlier conversations")}
                                    onChange={(_event, value) => { if (value) chat.open(value); }}>
                                    <FormSelectOption value="" label={t("Earlier conversations…")} isPlaceholder />
                                    {state.sessions.map(one => (
                                        <FormSelectOption key={one.sessionId} value={one.sessionId}
                                            label={`${one.updatedAt ? formatTime(Date.parse(one.updatedAt) / 1000, i18n.language) + " · " : ""}${(one.title || one.sessionId).slice(0, 60)}`} />
                                    ))}
                                </FormSelect>
                            </FlexItem>
                        )}
                        <Button variant="secondary" isDisabled={!idle || state.items.length === 0} onClick={() => chat.fresh()}>{t("New conversation")}</Button>
                        <Button variant="secondary" onClick={() => chat.stop()}>{t("End the session")}</Button>
                    </>
                )}
            </SectionHeader>
            {state.phase === "starting" && <Spinner aria-label={t("Starting the assistant")} />}
            {state.phase === "ended" && state.items.length > 0 && <Conversation items={state.items} busy={false} />}
            {running && (
                <Conversation items={state.items} busy={state.busy || state.loading}>
                    {!state.loading && <Content component="p">{t("The assistant is ready. It ends when this page is closed or reloaded; the conversation is kept.")}</Content>}
                </Conversation>
            )}
            {running && state.needsLogin && <Login agent={agent} />}
            {running && <Composer state={state} />}
            {(state.phase === "idle" || state.phase === "ended") && <Start info={info.data} state={state} />}
            {state.ask && <AskModal ask={state.ask} />}
        </div>
    );
}
