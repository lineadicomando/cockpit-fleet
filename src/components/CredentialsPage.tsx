import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, FormGroup, FormHelperText, FormSelect, FormSelectOption, HelperText, HelperTextItem, Label, TextArea, TextInput } from "@patternfly/react-core";
import { ActionsColumn, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { call, deleteSecret, setSecret } from "../lib/fleet.ts";
import { useLoad } from "../lib/hooks.ts";
import { useCan } from "../lib/session.tsx";
import type { Credential, CredentialKind } from "../lib/types.ts";
import { ConfirmDelete, FormModal, ListState, SectionHeader } from "./common.tsx";

const FIELDS: Record<CredentialKind, string[]> = {
    machine: ["private_key", "password", "become_password"],
    vault: ["vault_password"],
    source: ["private_key", "token"],
};

function useLabels() {
    const { t } = useTranslation();
    return {
        kinds: {
            machine: t("Machine (SSH login)"),
            vault: t("Ansible Vault"),
            source: t("Source control (git)"),
        } as Record<CredentialKind, string>,
        fields: {
            private_key: t("SSH private key"),
            password: t("Password"),
            become_password: t("Privilege escalation password"),
            vault_password: t("Vault password"),
            token: t("Access token"),
        } as Record<string, string>,
    };
}

// Secret inputs: empty means "leave as it is".
function SecretFields({ kind, values, onChange, existing }: {
    kind: CredentialKind;
    values: Record<string, string>;
    onChange: (field: string, value: string) => void;
    existing?: Record<string, boolean>;
}) {
    const { t } = useTranslation();
    const { fields } = useLabels();
    return (
        <>
            {FIELDS[kind].map(field => (
                <FormGroup key={field} label={fields[field]} fieldId={`cred-${field}`}>
                    {field === "private_key"
                        ? <TextArea id={`cred-${field}`} value={values[field] ?? ""} rows={4} resizeOrientation="vertical"
                            onChange={(_e, value) => onChange(field, value)} spellCheck={false}
                            style={{ fontFamily: "var(--pf-t--global--font--family--mono)" }} />
                        : <TextInput id={`cred-${field}`} type="password" autoComplete="new-password"
                            value={values[field] ?? ""} onChange={(_e, value) => onChange(field, value)} />}
                    {existing?.[field] && (
                        <FormHelperText>
                            <HelperText><HelperTextItem>{t("Already set. Leave empty to keep it.")}</HelperTextItem></HelperText>
                        </FormHelperText>
                    )}
                </FormGroup>
            ))}
        </>
    );
}

async function storeSecrets(credential: Credential, values: Record<string, string>) {
    for (const [field, value] of Object.entries(values)) {
        if (!value) continue;
        // A private key file must end with a newline or ssh rejects it.
        const text = field === "private_key" && !value.endsWith("\n") ? `${value}\n` : value;
        await setSecret(credential.secret_names[field], text);
    }
}

function CreateModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const { kinds } = useLabels();
    const [name, setName] = useState("");
    const [kind, setKind] = useState<CredentialKind>("machine");
    const [username, setUsername] = useState("");
    const [secrets, setSecrets] = useState<Record<string, string>>({});

    async function submit() {
        const credential = await call<Credential>("credential.create", { name, kind, username });
        try {
            await storeSecrets(credential, secrets);
        } finally {
            onDone();
        }
        onClose();
    }

    return (
        <FormModal title={t("New credential")} submitLabel={t("Create")} onSubmit={submit} onClose={onClose} isDisabled={!name.trim()}>
            <FormGroup label={t("Name")} isRequired fieldId="cred-name">
                <TextInput id="cred-name" value={name} onChange={(_e, value) => setName(value)} isRequired />
            </FormGroup>
            <FormGroup label={t("Type")} fieldId="cred-kind">
                <FormSelect id="cred-kind" value={kind} onChange={(_e, value) => { setKind(value as CredentialKind); setSecrets({}); }}>
                    {(Object.keys(kinds) as CredentialKind[]).map(key => <FormSelectOption key={key} value={key} label={kinds[key]} />)}
                </FormSelect>
            </FormGroup>
            {kind !== "vault" && (
                <FormGroup label={t("User name")} fieldId="cred-user">
                    <TextInput id="cred-user" value={username} onChange={(_e, value) => setUsername(value)} autoComplete="off" />
                </FormGroup>
            )}
            <SecretFields kind={kind} values={secrets} onChange={(field, value) => setSecrets(current => ({ ...current, [field]: value }))} />
        </FormModal>
    );
}

function SecretsModal({ credential, onClose, onDone }: { credential: Credential; onClose: () => void; onDone: () => void }) {
    const { t } = useTranslation();
    const [secrets, setSecrets] = useState<Record<string, string>>({});

    async function submit() {
        try {
            await storeSecrets(credential, secrets);
        } finally {
            onDone();
        }
        onClose();
    }

    return (
        <FormModal title={t("Secrets of {{name}}", { name: credential.name })} submitLabel={t("Save")} onSubmit={submit} onClose={onClose}
            isDisabled={!Object.values(secrets).some(Boolean)}>
            <SecretFields kind={credential.kind} values={secrets} existing={credential.secrets}
                onChange={(field, value) => setSecrets(current => ({ ...current, [field]: value }))} />
        </FormModal>
    );
}

export function CredentialsPage() {
    const { t } = useTranslation();
    const can = useCan();
    const labels = useLabels();
    const list = useLoad(() => call<{ credentials: Credential[] }>("credential.list"));
    const [creating, setCreating] = useState(false);
    const [editing, setEditing] = useState<Credential | null>(null);
    const [deleting, setDeleting] = useState<Credential | null>(null);

    async function remove(credential: Credential) {
        // Secrets first: the backend refuses to forget a credential that still has some.
        for (const [field, isSet] of Object.entries(credential.secrets)) {
            if (isSet) await deleteSecret(credential.secret_names[field]);
        }
        await call("credential.delete", { id: credential.id });
        setDeleting(null);
        list.reload();
    }

    return (
        <>
            <SectionHeader title={t("Credentials")}>
                {can("credential.create") && <Button variant="primary" onClick={() => setCreating(true)}>{t("New credential")}</Button>}
            </SectionHeader>
            <ListState loaded={list} isEmpty={data => data.credentials.length === 0}
                emptyTitle={t("No credentials")}
                emptyBody={t("A credential holds what a run needs to log in to the nodes, to open an Ansible Vault or to fetch a git repository. Secrets are encrypted and can never be read back.")}>
                {data => (
                    <Table variant="compact" aria-label={t("Credentials")}>
                        <Thead>
                            <Tr>
                                <Th>{t("Name")}</Th><Th>{t("Type")}</Th><Th>{t("User name")}</Th><Th>{t("Secrets set")}</Th><Th screenReaderText={t("Actions")} />
                            </Tr>
                        </Thead>
                        <Tbody>
                            {data.credentials.map(credential => (
                                <Tr key={credential.id}>
                                    <Td dataLabel={t("Name")}>{credential.name}</Td>
                                    <Td dataLabel={t("Type")}>{labels.kinds[credential.kind]}</Td>
                                    <Td dataLabel={t("User name")}>{credential.username}</Td>
                                    <Td dataLabel={t("Secrets set")}>
                                        {Object.entries(credential.secrets).filter(([, isSet]) => isSet).map(([field]) => (
                                            <Label key={field} isCompact style={{ marginInlineEnd: "0.25rem" }}>{labels.fields[field]}</Label>
                                        ))}
                                    </Td>
                                    <Td isActionCell>
                                        {can("credential.delete") && (
                                            <ActionsColumn items={[
                                                { title: t("Change secrets"), onClick: () => setEditing(credential) },
                                                { title: t("Delete"), onClick: () => setDeleting(credential) },
                                            ]} />
                                        )}
                                    </Td>
                                </Tr>
                            ))}
                        </Tbody>
                    </Table>
                )}
            </ListState>
            {creating && <CreateModal onClose={() => setCreating(false)} onDone={list.reload} />}
            {editing && <SecretsModal credential={editing} onClose={() => setEditing(null)} onDone={list.reload} />}
            {deleting && (
                <ConfirmDelete name={deleting.name} what={t("The credential and its secrets are deleted. This cannot be undone.")}
                    onDelete={() => remove(deleting)} onClose={() => setDeleting(null)} />
            )}
        </>
    );
}
