import "./dom.ts";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { calls, setSpawnHandler, type SpawnCall } from "./mocks/cockpit.ts";
import { WorkflowsPage } from "../src/components/WorkflowsPage.tsx";
import { SessionContext } from "../src/lib/session.tsx";
import type { Inventory, Role, Template, Workflow, WorkflowNode } from "../src/lib/types.ts";

afterEach(cleanup);

const last = (args: string[]) => args[args.length - 1] ?? "";
const inventory = (id: string, name: string) => ({ id, name, starter: false }) as Inventory;
const template = (id: string, name: string, approval = "none") => ({ id, name, project_id: "p1", approval, survey: [] }) as unknown as Template;
const node = (id: string, template_id: string, name = ""): WorkflowNode =>
    ({ id, name, template_id, limit: "", answers: {}, approval: "none", needs_approval: false });
const run = (id: string, status: string) => ({ id, status, created_at: 1_700_000_000 }) as Workflow["last_run"] & object;

// backup, then update on success or restore on failure, then reboot anyway; on
// aula-1 or aula-2, chosen at launch, or each step's own; its last run on aula-2.
const MAINTAIN: Workflow = {
    id: "w1", name: "maintain", description: "", project_id: null, inventory_id: null, inventory_ids: ["i1", "i2"],
    needs_inventory: false, created_at: 0, updated_at: 0,
    nodes: [node("a", "t1", "backup"), node("b", "t2"), node("c", "t3", "restore"), node("d", "t4")],
    edges: [
        { from: "a", to: "b", condition: "success" },
        { from: "a", to: "c", condition: "failure" },
        { from: "b", to: "d", condition: "always" },
        { from: "c", to: "d", condition: "always" },
    ],
    last_run: run("r1", "failed"), last_runs: { i2: run("r1", "failed") },
};

function backend() {
    setSpawnHandler((call: SpawnCall) => {
        const command = last(call.args);
        const data: Record<string, unknown> = {
            "workflow.list": { workflows: [MAINTAIN] },
            "template.list": { templates: [template("t1", "backup"), template("t2", "update"), template("t3", "restore"), template("t4", "reboot", "confirm")] },
            "schedule.list": { schedules: [{ id: "s1", workflow_id: "w1", template_id: null }] },
            "project.list": { projects: [{ id: "p1", name: "school", starter: false }] },
            "inventory.list": { inventories: [inventory("i1", "aula-1"), inventory("i2", "aula-2")] },
            "workflow.launch": { id: "r2", title: "maintain" },
        };
        return JSON.stringify({ ok: true, data: data[command] ?? null });
    });
}

// The commands of each role that the page asks about.
const COMMANDS: Record<string, string[]> = {
    viewer: ["workflow.list"],
    operator: ["workflow.list", "workflow.launch"],
};

async function page(role: Role) {
    window.localStorage.clear();
    backend();
    await act(async () => {
        render(<SessionContext.Provider value={{ user: "anna", role, rights: [], version: "0", commands: COMMANDS[role], chat: false }}>
            <WorkflowsPage />
        </SessionContext.Provider>);
    });
}

test("the workflows are listed under each inventory they may run on, with their last run there", async () => {
    await page("viewer");
    const heads = [...document.querySelectorAll(".fleet-group-row")].map(row => row.querySelector("strong")?.textContent);
    assert.deepEqual(heads, ["aula-1", "aula-2", "Each step's own inventory"]);
    const rows = screen.getAllByRole("row").filter(row => within(row).queryByText("maintain"));
    assert.equal(rows.length, 3);
    // On aula-1 never, its last on aula-2 muted; on aula-2 its own.
    assert.ok(within(rows[0]).getByText("Never here"));
    assert.ok(within(rows[0]).getByText("Last on aula-2:"));
    assert.ok(within(rows[1]).getByText("Failed"));
    // A viewer launches nothing; the schedule that launches it is counted.
    // Booleans, never elements: a failing assertion prints what it compares, and an element of jsdom is endless.
    assert.equal(within(rows[1]).queryByRole("button", { name: "Launch" }) !== null, false);
    assert.ok(within(rows[1]).getByRole("button", { name: "1" }));
});

test("the steps are in columns, alternatives and steps run anyway marked", async () => {
    await page("viewer");
    const row = screen.getAllByRole("row").find(item => within(item).queryByText("maintain"))!;
    const columns = within(row).getByRole("list", { name: "Steps in order" });
    const items = within(columns).getAllByRole("listitem").map(item => item.textContent);
    assert.deepEqual(items, ["backup", "updaterestore · if it fails", "reboot · anyway (waits for an approval)"]);
});

test("a workflow is launched from a group on that group's inventory", async () => {
    await page("operator");
    const rows = screen.getAllByRole("row").filter(row => within(row).queryByText("maintain"));
    await act(async () => fireEvent.click(within(rows[1]).getByRole("button", { name: "Launch" })));
    const dialog = screen.getByRole("dialog");
    assert.equal((within(dialog).getByLabelText(/^Inventory/) as HTMLSelectElement).value, "i2");
    await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Launch" })));
    const launch = calls.find(call => last(call.args) === "workflow.launch");
    assert.deepEqual(JSON.parse(launch?.input ?? ""), { id: "w1", inventory_id: "i2" });
});

// With nothing to choose, Launch still asks first, the steps in sight.
test("a workflow on its own inventory is launched only once confirmed", async () => {
    const own = { ...MAINTAIN, inventory_id: "i1", inventory_ids: null };
    const original = MAINTAIN;
    Object.assign(MAINTAIN, own);
    try {
        await page("operator");
        const row = screen.getAllByRole("row").find(item => within(item).queryByText("maintain"))!;
        await act(async () => fireEvent.click(within(row).getByRole("button", { name: "Launch" })));
        assert.equal(calls.some(call => last(call.args) === "workflow.launch"), false);
        const dialog = screen.getByRole("dialog");
        assert.ok(within(dialog).getByText("Every step runs on aula-1."));
        assert.ok(within(dialog).getByRole("list", { name: "Steps in order" }));
        await act(async () => fireEvent.click(within(dialog).getByRole("button", { name: "Launch" })));
        const launches = calls.filter(call => last(call.args) === "workflow.launch");
        const launch = launches[launches.length - 1];
        assert.deepEqual(JSON.parse(launch?.input ?? ""), { id: "w1" });
    } finally {
        Object.assign(original, { inventory_id: null, inventory_ids: ["i1", "i2"] });
    }
});

test("a filter that leaves nothing says so, and clears", async () => {
    await page("viewer");
    fireEvent.change(screen.getByLabelText("Filter by feature"), { target: { value: "unscheduled" } });
    assert.ok(screen.getByText("No workflow matches the filters"));
    fireEvent.click(screen.getAllByRole("button", { name: "Clear all filters" })[0]);
    assert.equal(screen.queryByText("No workflow matches the filters") !== null, false);
    assert.equal(screen.getAllByRole("row").filter(row => within(row).queryByText("maintain")).length, 3);
});
