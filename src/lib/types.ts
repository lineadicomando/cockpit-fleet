export type Role = "viewer" | "operator" | "admin";

export interface Session {
    user: string;
    // None for an approver who has no role: such a session only approves.
    role: Role | null;
    // Rights apart from the role: "approve", from cockpit-fleet-approver.
    rights: string[];
    version: string;
    // Names of the backend commands this role and these rights may call.
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

export interface ChatGuide {
    installed: boolean;
    configured: boolean;
    mcp_enabled: boolean;
    command: string;
    login: string;
}

export interface ChatInfo {
    agents: ChatAgentInfo[];
    // Why the chat cannot be used, or an empty string.
    unavailable: string;
    // How far this machine is from a chat with Claude, and the line that goes the rest of the way.
    guide: ChatGuide;
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
    // The profile of the assistant an agent's call was held to.
    profile: string;
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

// What project.files offers to choose a playbook or an inventory from.
export interface ProjectPaths {
    files: string[];
    directories: string[];
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
    directories: string[];
    truncated: boolean;
    editable: boolean;
    // What a change of several files says it was made on.
    revision: string;
}

export interface FileContent {
    path: string;
    content: string;
    // What a write quotes, so that a change made meanwhile is not overwritten.
    sha256: string;
    // Not text: shown, never edited.
    binary?: boolean;
    size?: number;
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
    // The commit its tree is at, for one written here.
    revision: string;
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

export interface VariableSet {
    id: string;
    name: string;
    description: string;
    variables: Record<string, unknown>;
    // The job templates that take it.
    templates: { id: string; name: string }[];
    updated_at: number;
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
    // Sets of variables taken before its own, in order: a later one wins.
    variable_set_ids: string[];
    survey: SurveyField[];
    forks: number | null;
    verbosity: number;
    become: boolean;
    // Whether each run waits for an approval, and from whom.
    approval: ApprovalMode;
    // approval other than none: what it was before approval had modes.
    needs_approval: boolean;
    // Its latest run, a step of a workflow included; null when never launched.
    last_run: { id: string; status: RunStatus; created_at: number; finished_at: number | null } | null;
}

export type StepCondition = "success" | "failure" | "always";

// None; confirm, which who launched the run may give too; other, another person.
export type ApprovalMode = "none" | "confirm" | "other";

export interface WorkflowNode {
    id: string;
    name: string;
    template_id: string;
    limit: string;
    answers: Record<string, SurveyValue>;
    approval: ApprovalMode;
    needs_approval: boolean;
}

export interface WorkflowEdge {
    from: string;
    to: string;
    condition: StepCondition;
}

export interface Workflow {
    id: string;
    name: string;
    description: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
    created_at: number;
    updated_at: number;
    last_run: { id: string; status: RunStatus; created_at: number } | null;
}

// active, success and failure: the step has a run (or could not start: failure);
// ready: it starts now; waiting: for the steps before it; skipped: none of its links held.
export type StepState = "active" | "success" | "failure" | "ready" | "waiting" | "skipped";

export interface WorkflowProgress {
    run: Run;
    nodes: (WorkflowNode & { state: StepState; run: Run | null; carried: boolean; error: string })[];
    edges: WorkflowEdge[];
    resumed_from: string | null;
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

export type RunStatus = "pending_approval" | "queued" | "running" | "successful" | "failed" | "canceled" | "error" | "rejected" | "expired";

export interface Approval {
    run_id: string;
    requested_by: string;
    requested_at: number;
    expires_at: number;
    decision: "" | "approved" | "rejected" | "expired";
    decided_by: string;
    decided_at: number | null;
    comment: string;
    // What it waits for: a confirmation, or another person.
    mode: "confirm" | "other";
    // Whether the caller may approve or reject it now.
    can_decide?: boolean;
}

// A run as approval.list gives it: with its approval and the names of what it uses.
export interface ApprovalEntry extends Run {
    approval: Approval;
    template: string;
    project: string;
    inventory: string;
    can_decide: boolean;
}

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
    kind: "playbook" | "adhoc" | "inventory" | "sync" | "syntax" | "unpack" | "galaxy" | "check" | "workflow";
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
    // When its output was deleted, [runs] keep_days after it ended.
    pruned_at: number | null;
    // run.get only, for a run of a template that needs approval.
    approval?: Approval;
    // A workflow run: its workflow. A step of one: the workflow run, and which step.
    workflow_id: string | null;
    workflow_run_id: string | null;
    workflow_node: string;
    rc: number | null;
    revision: string;
    error: string;
    options: {
        limit?: string; check?: boolean; diff?: boolean; answers?: Record<string, SurveyValue>; secret_answers?: string[];
        // The sets of variables a run of a template took, by name as they were then.
        variable_sets?: { id: string; name: string }[];
        // A syntax check: the playbook, and what ansible-lint said if it is installed.
        // An installation of collections: the requirements file.
        path?: string; lint?: "clean" | "findings";
        // An uploaded archive.
        filename?: string; size?: number; sha256?: string;
        // An ad-hoc command: the module and its arguments, as those of a task.
        module?: string; free_form?: string; args?: Record<string, unknown>; become?: boolean;
        // Launched in series: it waits for the runs on its inventory before it.
        serial?: boolean;
    };
    stats: Record<string, HostStats>;
    cursor?: number;
    // run.get and run.list: what a queued run in series waits for.
    waiting_for?: { id: string; title: string } | null;
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
export type RunEvent = "failed" | "changed" | "succeeded" | "canceled" | "approval";
// What the states of checks give.
export type CheckEvent = "problem" | "recovery" | "reminder";
// What the controller says of itself: check cycles falling behind, and back on
// time; that cannot be prepared, and can again.
export type SystemEvent = "late" | "caught_up" | "unprepared" | "prepared";
export type NotifyEvent = RunEvent | CheckEvent | SystemEvent;
export type RuleScope = "all" | "project" | "template" | "schedule" | "inventory" | "group" | "check";
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
    // The group of the inventory, for the scope group.
    group: string;
    enabled: boolean;
}

export interface Notification {
    id: number;
    channel_id: string;
    channel: string;
    event: NotifyEvent | "test";
    run_id: string | null;
    host_id: string | null;
    check_id: string | null;
    title: string;
    state: NotificationState;
    attempts: number;
    next_at: number;
    error: string;
    created_at: number;
    sent_at: number | null;
}

// --- checks ---------------------------------------------------------------------

export type CheckKind = "reachability" | "disk" | "cpu" | "memory" | "services" | "uptime" | "certificates" | "clock" | "cmdb" | "updates"
    | "playbook" | "compliance";
export type StateName = "ok" | "warning" | "critical" | "unknown";

export interface Thresholds {
    [metric: string]: { warning: number | null; critical: number | null };
}

export interface CatalogEntry {
    name: CheckKind;
    description: string;
    // Each parameter is a list, of paths or of names, or a flag.
    params: Record<string, { kind: "paths" | "names" | "flag"; help: string }>;
    required: string[];
    thresholds: Thresholds;
    // In cycles of its own, with a longer time limit; and the interval it suggests.
    slow: boolean;
    interval: number;
}

export interface StateCounts {
    ok: number;
    warning: number;
    critical: number;
    unknown: number;
    // Not checked yet.
    pending: number;
}

export interface CheckDef {
    id: string;
    name: string;
    kind: CheckKind;
    inventory_id: string;
    inventory: string;
    group: string;
    interval: number;
    retry_interval: number;
    max_attempts: number;
    params: Record<string, string[] | boolean>;
    // What the check sets, and those with the defaults of the catalog.
    thresholds: Thresholds;
    effective_thresholds: Thresholds;
    machine_credential_id: string | null;
    // A check of the kind playbook or compliance: where its playbook is.
    project_id: string | null;
    playbook: string;
    become: boolean;
    enabled: boolean;
    states: StateCounts;
}

export interface Metric {
    name: string;
    value: number;
    unit: string;
    instance?: string;
    min?: number;
    max?: number;
}

export interface CheckState {
    host_id: string;
    check_id: string;
    host: string;
    check: string;
    kind: CheckKind;
    inventory_id: string;
    // The last result; null until the first.
    state: StateName | null;
    hard_state: StateName;
    state_type: "soft" | "hard";
    attempt: number;
    max_attempts: number;
    summary: string;
    metrics: Metric[];
    details: Record<string, unknown>;
    checked_at: number | null;
    changed_at: number | null;
    next_at: number | null;
    running: boolean;
    flapping: boolean;
    acknowledged: boolean;
    ack_by: string;
    ack_at: number | null;
    ack_comment: string;
    in_maintenance: boolean;
    // Unknown because the host could not be reached: not told about.
    suppressed: boolean;
}

export interface FleetHost {
    id: string;
    inventory_id: string;
    inventory: string;
    name: string;
    address: string;
    groups: string[];
    platform: string;
    os: string;
    facts: Record<string, string>;
    facts_at: number | null;
    reachable: boolean | null;
    states: StateCounts;
}

export interface StateChange {
    check_id: string;
    check: string;
    ts: number;
    state: StateName;
    state_type: "soft" | "hard";
    summary: string;
}

export interface HostDetail extends FleetHost {
    checks: CheckState[];
    changes: StateChange[];
    in_maintenance: boolean;
}

export interface Maintenance {
    id: string;
    inventory_id: string;
    inventory: string;
    host_id: string | null;
    host: string;
    group: string;
    starts_at: number;
    ends_at: number;
    comment: string;
    created_by: string;
}

// --- the inventory of hosts (CMDB) -------------------------------------------------

export interface Disk {
    name: string;
    size: number;
    model: string;
}

export interface NetworkInterface {
    name: string;
    mac: string;
    addresses: string[];
}

// Empty until a cmdb or updates check has reported on the host.
export interface Hardware {
    vendor?: string;
    model?: string;
    serial?: string;
    bios?: string;
    virtual?: string;
    cpu_model?: string;
    cpus?: number | null;
    memory?: number | null;
    boot_at?: number | null;
    collected_at?: number | null;
    package_manager?: string;
    updates_at?: number | null;
    update_manager?: string;
    disks?: Disk[];
    interfaces?: NetworkInterface[];
}

export interface CmdbHost {
    id: string;
    name: string;
    inventory_id: string;
    inventory: string;
    groups: string[];
    platform: string;
    os: string;
    os_id: string;
    os_version: string;
    kernel: string;
    arch: string;
    reachable: boolean | null;
    hardware: Hardware;
    packages: number;
    services: number;
    updates: number;
    security_updates: number;
    // Null when no compliance check has a result for it.
    compliant: boolean | null;
}

export interface Package {
    name: string;
    version: string;
    arch: string;
}

export interface Service {
    name: string;
    state: string;
    enabled: string;
}

export interface Update {
    name: string;
    current: string;
    available: string;
    security: boolean;
    source: string;
}

export interface CmdbChange {
    ts: number;
    area: "hardware" | "network" | "package" | "service";
    name: string;
    before: string;
    after: string;
}

export interface CmdbDetail extends CmdbHost {
    package_list: Package[];
    service_list: Service[];
    update_list: Update[];
    changes: CmdbChange[];
}

export interface PackageHit extends Package {
    host_id: string;
    host: string;
    inventory: string;
}

export interface Drift {
    check_id: string;
    check: string;
    ts: number;
    changes: { task: string; diff: string; note?: string }[];
}

// A group of check cycles of an inventory, as system.health reads it.
export interface HealthGroup {
    inventory: string;
    inventory_id: string;
    // "" for the checks that go together, a slow kind, or the id of a check of the user.
    group: string;
    runs: "checks" | "slow" | "own";
    // The kind for a slow one, the name of the check for one of the user.
    name: string;
    interval: number;
    checks: number;
    cycles: number;
    unsuccessful: number;
    duration_median: number | null;
    duration_max: number | null;
    memory_peak: number | null;
    skipped: number;
    last: { status: string; started_at: number; finished_at: number; hosts: number; forks: number; duration: number } | null;
    active: number;
    behind: { streak: number; since: number; told: boolean } | null;
    unprepared: { reason: string; since: number } | null;
    sizing: { forks: number; shard_size: number; basis: "configured" | "measured"; seconds_per_host?: number };
}

export interface HealthLane {
    lane: "runs" | "checks" | "slow";
    limit: number;
    running: number;
    queued: number;
    oldest_wait: number | null;
}

export interface Health {
    now: number;
    window: number;
    late_after: number;
    memory: { budget: number; configured_mb: number; cycles_at_once: number };
    groups: HealthGroup[];
    queue: HealthLane[];
    database: { bytes: number; free_bytes: number; wal_bytes: number; incremental: boolean };
}
