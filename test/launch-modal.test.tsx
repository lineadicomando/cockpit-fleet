import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { calls, setSpawnHandler, type SpawnCall } from "./mocks/cockpit.ts";
import { LaunchModal, TemplatesPage } from "../src/components/TemplatesPage.tsx";
import { SessionContext } from "../src/lib/session.tsx";
import type { Inventory, Template } from "../src/lib/types.ts";

afterEach(cleanup);

// The command of a call to fleetctl: the last of its argv.
const last = (args: string[]) => args[args.length - 1] ?? "";

const TEMPLATE: Template = {
    id: "t1", name: "shutdown", project_id: "p1", playbook: "shutdown.yml", inventory_id: null,
    inventory_ids: ["i1", "i2"], any_inventory: false, machine_credential_id: null, vault_credential_ids: [],
    limit_hosts: "", tags: [], skip_tags: [], extra_vars: {}, variable_set_ids: [], survey: [], forks: null,
    verbosity: 0, become: false, approval: "none", needs_approval: false, last_run: null, last_runs: {},
};

const inventory = (id: string, name: string) => ({ id, name, starter: false }) as Inventory;

function backend() {
    setSpawnHandler((call: SpawnCall) => {
        const command = last(call.args);
        if (command === "inventory.list")
            return JSON.stringify({ ok: true, data: { inventories: [inventory("i1", "aula-1"), inventory("i2", "aula-2")] } });
        if (command === "run.start") return JSON.stringify({ ok: true, data: { id: "r1", title: "shutdown" } });
        return JSON.stringify({ ok: true, data: null });
    });
}

test("a template with no default inventory is launched only once one is chosen", async () => {
    backend();
    await act(async () => {
        render(<LaunchModal template={TEMPLATE} onClose={() => {}} />);
    });
    const dialog = screen.getByRole("dialog");
    const launch = within(dialog).getByRole("button", { name: "Launch" });
    assert.equal(launch.hasAttribute("disabled") || launch.getAttribute("aria-disabled") === "true", true);
    const select = within(dialog).getByLabelText(/^Inventory/) as HTMLSelectElement;
    assert.deepEqual([...select.options].map(option => option.textContent), ["Choose an inventory", "aula-1", "aula-2"]);

    fireEvent.change(select, { target: { value: "i2" } });
    await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Launch" })));
    const start = calls.find(call => last(call.args) === "run.start");
    assert.ok(start);
    assert.equal(JSON.parse(start.input ?? "").inventory_id, "i2");
});

test("the list of templates shows, under each inventory, the last run there", async () => {
    const run = (id: string, status: string, inventory: string) => ({ id, status, created_at: 1_700_000_000, finished_at: null, inventory_id: inventory });
    const lab = run("r-lab", "failed", "i2");
    const listed: Template[] = [
        { ...TEMPLATE, id: "t1", name: "wol", inventory_id: "i1", inventory_ids: ["i2"], last_run: lab, last_runs: { i2: lab } } as Template,
    ];
    window.localStorage.removeItem("templates-view");
    setSpawnHandler((call: SpawnCall) => {
        const command = last(call.args);
        if (command === "template.list") return JSON.stringify({ ok: true, data: { templates: listed } });
        if (command === "inventory.list")
            return JSON.stringify({ ok: true, data: { inventories: [inventory("i1", "aula-1"), inventory("i2", "aula-2")] } });
        if (command === "schedule.list") return JSON.stringify({ ok: true, data: { schedules: [] } });
        if (command === "project.list") return JSON.stringify({ ok: true, data: { projects: [] } });
        return JSON.stringify({ ok: true, data: null });
    });
    await act(async () => {
        render(<SessionContext.Provider value={{ user: "anna", role: "viewer", rights: [], version: "0", commands: ["template.list"], chat: false }}>
            <TemplatesPage />
        </SessionContext.Provider>);
    });
    const rows = screen.getAllByRole("row").filter(row => within(row).queryByText("wol"));
    assert.equal(rows.length, 2);
    // Under aula-1 (first by name): never there, and the last on aula-2, muted.
    assert.ok(within(rows[0]).getByText("Never here"));
    assert.ok(within(rows[0]).getByText("Last on aula-2:"));
    // Under aula-2: its own, failed.
    assert.equal(within(rows[1]).queryByText("Never here"), null);
    assert.ok(within(rows[1]).getByText("Failed"));
});
