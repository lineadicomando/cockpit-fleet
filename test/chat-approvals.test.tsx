import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { calls, setSpawnHandler, type SpawnCall } from "./mocks/cockpit.ts";
import { ChatApprovals } from "../src/components/ApprovalsPage.tsx";
import { SessionContext } from "../src/lib/session.tsx";
import type { ApprovalEntry, Session } from "../src/lib/types.ts";

afterEach(cleanup);

const last = (args: string[]) => args[args.length - 1] ?? "";

function entry(id: string, title: string, canDecide = true): ApprovalEntry {
    return {
        id, kind: "adhoc", status: "pending_approval", title, created_by: "anna",
        options: { module: "ansible.windows.win_powershell", limit: "PC215", args: { script: "Get-Volume" } },
        approval: { mode: "confirm", requested_at: 0, expires_at: 0 },
        template: "", project: "", inventory: "lab", can_decide: canDecide,
    } as unknown as ApprovalEntry;
}

function session(commands: string[]): Session {
    return { user: "anna", role: "admin", rights: [], version: "0", commands, chat: true };
}

async function draw(runIds: string[], approvals: ApprovalEntry[], commands: string[]) {
    setSpawnHandler((call: SpawnCall) => {
        if (last(call.args) === "approval.list") return JSON.stringify({ ok: true, data: { approvals } });
        return JSON.stringify({ ok: true, data: approvals[0] ?? null });
    });
    await act(async () => {
        render(<SessionContext.Provider value={session(commands)}><ChatApprovals runIds={runIds} /></SessionContext.Provider>);
    });
}

const ALL = ["approval.list", "approval.approve", "approval.reject"];

test("a run of the conversation that waits is confirmed on the page of the chat", async () => {
    await draw(["r1"], [entry("r1", "ansible.windows.win_powershell [PC215]"), entry("r9", "another")], ALL);
    assert.ok(screen.getByText("A run the assistant launched waits for your decision"));
    assert.ok(screen.getByText("ansible.windows.win_powershell [PC215]"));
    assert.equal(screen.queryByText("another"), null, "a run of another conversation");
    assert.ok(screen.getByText(/Get-Volume/));

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Approve" })));
    const dialog = screen.getByRole("dialog");
    await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Approve" })));
    const approve = calls.find(call => last(call.args) === "approval.approve");
    assert.ok(approve);
    assert.deepEqual(JSON.parse(approve.input ?? ""), { id: "r1", comment: "" });
});

test("nothing is shown when no run of the conversation waits", async () => {
    await draw([], [entry("r9", "another")], ALL);
    assert.equal(screen.queryByRole("button", { name: "Approve" }), null);
    assert.equal(calls.filter(call => last(call.args) === "approval.list").length, 0);
});

test("who may not decide is told so, without buttons", async () => {
    await draw(["r1"], [entry("r1", "cmd", false)], ALL);
    assert.ok(screen.getByText("Another user must approve it"));
    assert.equal(screen.queryByRole("button", { name: "Approve" }), null);
});
