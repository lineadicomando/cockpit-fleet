import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { calls, setSpawnHandler, type SpawnCall } from "./mocks/cockpit.ts";
import { LaunchModal } from "../src/components/TemplatesPage.tsx";
import type { Inventory, Template } from "../src/lib/types.ts";

afterEach(cleanup);

// The command of a call to fleetctl: the last of its argv.
const last = (args: string[]) => args[args.length - 1] ?? "";

const TEMPLATE: Template = {
    id: "t1", name: "shutdown", project_id: "p1", playbook: "shutdown.yml", inventory_id: null,
    inventory_ids: ["i1", "i2"], any_inventory: false, machine_credential_id: null, vault_credential_ids: [],
    limit_hosts: "", tags: [], skip_tags: [], extra_vars: {}, variable_set_ids: [], survey: [], forks: null,
    verbosity: 0, become: false, approval: "none", needs_approval: false, last_run: null,
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
