export type Role = "viewer" | "operator" | "admin";

export interface Session {
    user: string;
    role: Role;
    version: string;
    // Names of the backend commands this role may call.
    commands: string[];
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
    kind: "local" | "git";
    path: string;
    url: string;
    ref: string;
    credential_id: string | null;
    revision: string;
    synced_at: number | null;
    sync_error: string;
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
    forks: number | null;
    verbosity: number;
    become: boolean;
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
    kind: "playbook" | "inventory" | "sync";
    status: RunStatus;
    title: string;
    template_id: string | null;
    project_id: string | null;
    inventory_id: string | null;
    created_by: string;
    created_at: number;
    started_at: number | null;
    finished_at: number | null;
    rc: number | null;
    revision: string;
    error: string;
    options: { limit?: string; check?: boolean; diff?: boolean };
    stats: Record<string, HostStats>;
    cursor?: number;
}

export interface Chunk {
    text: string;
    offset: number;
    more: boolean;
}
