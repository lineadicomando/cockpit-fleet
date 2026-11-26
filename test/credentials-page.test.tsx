import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { calls, setSpawnHandler, type SpawnCall } from "./mocks/cockpit.ts";
import { CredentialsPage } from "../src/components/CredentialsPage.tsx";
import { SessionContext } from "../src/lib/session.tsx";
import type { Credential, Session } from "../src/lib/types.ts";

afterEach(cleanup);

const SECRET = "s3cret-Pa55";

// The command of a call to fleetctl: the last of its argv.
const last = (args: string[]) => args[args.length - 1] ?? "";

const MACHINE: Credential = {
    id: "c1", name: "lab", kind: "machine", username: "ansible", vault_id: "",
    secrets: { password: true }, used_by: [],
    secret_names: { password: "cred-c1-password", private_key: "cred-c1-private_key", key_passphrase: "cred-c1-key_passphrase", become_password: "cred-c1-become_password" },
};

function session(commands: string[]): Session {
    return { user: "anna", role: "viewer", rights: [], version: "0", commands, chat: false };
}

// What the page asked of fleetctl, by command, and what reached fleet-secret.
function backend() {
    setSpawnHandler((call: SpawnCall) => {
        const command = last(call.args);
        if (command === "credential.list") return JSON.stringify({ ok: true, data: { credentials: [MACHINE] } });
        if (command === "credential.create") return JSON.stringify({ ok: true, data: { ...MACHINE, id: "c2", name: "new" } });
        return JSON.stringify({ ok: true, data: null });
    });
}

async function draw(commands: string[]) {
    backend();
    await act(async () => {
        render(<SessionContext.Provider value={session(commands)}><CredentialsPage /></SessionContext.Provider>);
    });
}

test("a viewer sees the credentials and nothing to change them", async () => {
    await draw(["credential.list"]);
    assert.ok(screen.getByText("lab"));
    assert.equal(screen.queryByRole("button", { name: "New credential" }), null);
    assert.equal(screen.queryByRole("button", { name: "Actions" }), null);
});

test("an operator has them", async () => {
    await draw(["credential.list", "credential.create", "credential.update", "credential.delete"]);
    assert.ok(screen.getByRole("button", { name: "New credential" }));
    assert.ok(screen.getByRole("button", { name: "Actions" }));
});

test("a secret goes to fleet-secret on stdin, never to fleetctl", async () => {
    await draw(["credential.list", "credential.create", "credential.update", "credential.delete"]);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "New credential" })));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: "new" } });
    fireEvent.change(within(dialog).getByLabelText("Password"), { target: { value: SECRET } });
    await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Create" })));

    const fleetctl = calls.filter(call => call.args.includes("/usr/libexec/cockpit-fleet/fleetctl"));
    const create = fleetctl.find(call => last(call.args) === "credential.create");
    assert.ok(create);
    assert.deepEqual(JSON.parse(create.input ?? ""), { kind: "machine", name: "new", username: "", vault_id: "" });
    for (const call of fleetctl) assert.ok(!(call.input ?? "").includes(SECRET), `${last(call.args)} got the secret`);

    const secret = calls.find(call => call.args.includes("/usr/libexec/cockpit-fleet/fleet-secret"));
    assert.ok(secret);
    assert.deepEqual(secret.args.slice(-3), ["/usr/libexec/cockpit-fleet/fleet-secret", "set", "cred-c1-password"]);
    assert.equal(secret.input, SECRET);
    for (const call of calls) assert.ok(!call.args.some(arg => arg.includes(SECRET)), "a secret in argv");
});
