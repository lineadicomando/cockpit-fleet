export type Role = "viewer" | "operator" | "admin";

export interface Session {
    user: string;
    role: Role;
    version: string;
    // Names of the backend commands this role may call.
    commands: string[];
    // Whether config.toml names an agent for the chat page.
    chat: boolean;
}

export interface ChatAgentInfo {
    name: string;
    label: string;
    // What to type in a terminal to log the agent in to its model.
    login: string;
    installed: boolean;
    // What an administrator of the machine runs to install it, when known.
    setup: string;
}

export interface ChatInfo {
    agents: ChatAgentInfo[];
    // Why the chat cannot be used, or an empty string.
    unavailable: string;
    max_role: Role;
    max_sessions: number;
    open_sessions: number;
}

export type CheckStatus = "ok" | "warn" | "fail";

export interface Check {
    id: string;
    status: CheckStatus;
    detail: string;
}

export type AuditOutcome = "ok" | "denied" | "error";

export interface AuditEntry {
    id: number;
    ts: number;
    user: string;
    role: Role | null;
    command: string;
    params: string;
    outcome: AuditOutcome;
    detail: string;
    // What the call came through when it was not the interface: an agent.
    via: string;
}

export type CredentialKind = "machine" | "vault" | "source";

export interface Credential {
    id: string;
    name: string;
    kind: CredentialKind;
    username: string;
    // For a vault credential: the label Ansible matches encrypted data against.
    vault_id: string;
    // Which secret fields are set; values are never sent to the browser.
    secrets: Record<string, boolean>;
    secret_names: Record<string, string>;
}

export interface Project {
    id: string;
    name: string;
    // "managed": written in the interface, each change a commit.
    // "archive": uploaded as a whole, its revision the digest of the archive.
    kind: "local" | "git" | "managed" | "archive";
    path: string;
    url: string;
    ref: string;
    credential_id: string | null;
    revision: string;
    synced_at: number | null;
    sync_error: string;
    // What its requirements file asks for: "none" when it has no such file,
    // "missing" until a job has installed what the file lists now.
    collections: { file: string; state: "none" | "installed" | "missing"; installed_at: number | null; error: string };
}

export interface ProjectFiles {
    files: { path: string; size: number }[];
    truncated: boolean;
    editable: boolean;
}

export interface FileContent {
    path: string;
    content: string;
    // What a write quotes, so that a change made meanwhile is not overwritten.
    sha256: string;
}

export interface Commit {
    revision: string;
    author: string;
    time: number;
    subject: string;
}

export interface InventoryHost {
    name: string;
    address: string;
    port: number | null;
    connection: string;
    groups: string[];
}

export interface Inventory {
    id: string;
    name: string;
    kind: "file" | "managed";
    project_id: string | null;
    path: string;
    format: "yaml" | "ini";
    vault_credential_id: string | null;
    parsed_at: number | null;
    parse_error: string;
    host_count: number;
    hosts?: InventoryHost[];
    content?: string;
}

export interface HostKey {
    id: number;
    host: string;
    key_type: string;
    fingerprint: string;
    added_by: string;
    added_at: number;
}

export interface ScannedKey {
    host: string;
    key_type: string;
    key: string;
    fingerprint: string;
    // "changed": a different key of this type is trusted for this host.
    state: "new" | "trusted" | "changed";
}

export type SurveyType = "text" | "number" | "choice" | "boolean" | "secret";
export type SurveyValue = string | number | boolean;

// One question a template asks at launch; the answer becomes a variable.
export interface SurveyField {
    name: string;
    label?: string;
    help?: string;
    type: SurveyType;
    required?: boolean;
    default?: SurveyValue;
    // Text: bounds of its length. Number: bounds of its value.
    min_length?: number;
    max_length?: number;
    min?: number;
    max?: number;
    choices?: string[];
}

export interface Template {
    id: string;
    name: string;
    project_id: string;
    playbook: string;
    inventory_id: string;
    machine_credential_id: string | null;
    vault_credential_ids: string[];
    limit_hosts: string;
    tags: string[];
    skip_tags: string[];
    extra_vars: Record<string, unknown>;
    survey: SurveyField[];
    forks: number | null;
    verbosity: number;
    become: boolean;
}

export type Overlap = "skip" | "queue" | "cancel";

export interface Schedule {
    id: string;
    name: string;
    template_id: string;
    // The template's name, or "" when it is gone.
    template: string;
    calendar: string;
    timezone: string;
    enabled: boolean;
    limit: string;
    check: boolean;
    diff: boolean;
    answers: Record<string, SurveyValue>;
    overlap: Overlap;
    catch_up: boolean;
    priority: number;
    random_delay: number;
    next_at: number | null;
    last_at: number | null;
    last_outcome: "" | "launched" | "skipped" | "missed" | "error";
    last_error: string;
    last_run_id: string | null;
    last_status: RunStatus | "";
    created_by: string;
}

export interface SchedulePreview {
    calendar: string;
    normalized: string;
    next: number[];
}

export type RunStatus = "queued" | "running" | "successful" | "failed" | "canceled" | "error";

export interface HostStats {
    ok?: number;
    changed?: number;
    failures?: number;
    unreachable?: number;
    skipped?: number;
    rescued?: number;
    ignored?: number;
}

export interface Run {
    id: string;
    kind: "playbook" | "adhoc" | "inventory" | "sync" | "syntax" | "unpack" | "galaxy";
    status: RunStatus;
    title: string;
    template_id: string | null;
    project_id: string | null;
    inventory_id: string | null;
    created_by: string;
    created_at: number;
    // The schedule that launched it, when one did.
    schedule_id: string | null;
    priority: number;
    started_at: number | null;
    finished_at: number | null;
    rc: number | null;
    revision: string;
    error: string;
    options: {
        limit?: string; check?: boolean; diff?: boolean; answers?: Record<string, SurveyValue>; secret_answers?: string[];
        // A syntax check: the playbook, and what ansible-lint said if it is installed.
        // An installation of collections: the requirements file.
        path?: string; lint?: "clean" | "findings";
        // An uploaded archive.
        filename?: string; size?: number; sha256?: string;
        // An ad-hoc command: the module and its arguments, as those of a task.
        module?: string; free_form?: string; args?: Record<string, unknown>; become?: boolean;
    };
    stats: Record<string, HostStats>;
    cursor?: number;
}

// What ansible-doc says of a module: module.help.
export interface ModuleOption {
    name: string;
    type: string;
    required: boolean;
    default: unknown;
    choices: string[];
    aliases: string[];
    description: string;
    text?: string[];
    suboptions?: ModuleOption[];
}

export interface ModuleReturn {
    name: string;
    type: string;
    returned: string;
    text: string[];
    sample: unknown;
    contains: ModuleReturn[];
}

export interface ModuleHelp {
    name: string;
    collection: string;
    version_added: string;
    description: string;
    text: string[];
    requirements: string[];
    notes: string[];
    options: ModuleOption[];
    examples: string;
    returns: ModuleReturn[];
}

// What config.import did with each thing of the document.
export interface ImportReport {
    report: { section: string; name: string; outcome: "created" | "exists" | "failed"; detail: string }[];
    created: number;
    exists: number;
    failed: number;
}

export interface Chunk {
    text: string;
    offset: number;
    more: boolean;
}

export type ChannelKind = "telegram" | "webhook" | "smtp" | "matrix";
export type NotifyEvent = "failed" | "changed" | "succeeded" | "canceled";
export type RuleScope = "all" | "project" | "template" | "schedule";
export type NotificationState = "pending" | "sent" | "failed";

export interface Channel {
    id: string;
    name: string;
    kind: ChannelKind;
    // What the kind needs, never the secret: see src/lib/notify.ts.
    settings: Record<string, unknown>;
    language: "en" | "it";
    enabled: boolean;
    // Seconds to wait for more events, to send them as one message.
    window: number;
    max_per_hour: number;
    secret: boolean;
    secret_name: string;
    secret_required: boolean;
    pending: number;
    failed: number;
}

export interface NotifyRule {
    id: string;
    name: string;
    channel_id: string;
    channel: string;
    events: NotifyEvent[];
    scope: RuleScope;
    scope_id: string | null;
    scope_name: string;
    enabled: boolean;
}

export interface Notification {
    id: number;
    channel_id: string;
    channel: string;
    event: NotifyEvent | "test";
    run_id: string | null;
    title: string;
    state: NotificationState;
    attempts: number;
    next_at: number;
    error: string;
    created_at: number;
    sent_at: number | null;
}
