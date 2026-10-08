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
