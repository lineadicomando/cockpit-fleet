// Phase 21, the inventory chosen at launch (architecture/inventories.md):
// one template for two labs, the updates of one lab run to its end, the
// wake of the other, a schedule for it, the workflow launched on it.
// On a VM of the lab with win11 up: 'BROWSER_LAB=deb13 browser.sh setup labs'.
// It runs Windows Update on win11 for real: take a snapshot of it first.

const ENDED = ["Successful", "Failed", "Canceled", "Error"];
// Runs of Windows Update with its restarts: an hour and a half at most.
const LONG = 90 * 60_000;

// The row of a template under the group of an inventory: the rows of a
// group follow the row of its header, whose button is named as the inventory.
async function templateRow(s, inventory, template) {
    await s.see(s.app.getByRole("button", { name: inventory, exact: true }));
    const rows = s.app.getByRole("row");
    const count = await rows.count();
    let inGroup = false;
    for (let i = 0; i < count; i++) {
        const row = rows.nth(i);
        if (await row.getByRole("button", { name: /^aula-\d+$/ }).count()) {
            inGroup = (await row.getByRole("button", { name: inventory, exact: true }).count()) > 0;
        } else if (inGroup && await row.getByRole("gridcell", { name: template, exact: true }).count()) {
            return row;
        }
    }
    throw new Error(`no ${template} under ${inventory}`);
}

// The status of the run whose page is open.
function runStatus(s) {
    return s.app.locator(`xpath=//dt[normalize-space()="${s.t("Status")}"]/following-sibling::dd[1]`).first();
}

// Until the run whose page is open has ended, its page loaded again now and
// then; its status.
async function untilEnded(s) {
    const ended = ENDED.map((status) => s.t(status));
    const deadline = Date.now() + LONG;
    let checked = Date.now();
    for (;;) {
        const text = (await runStatus(s).textContent().catch(() => "")) ?? "";
        if (ended.some((status) => text.includes(status))) return text;
        if (Date.now() > deadline) throw new Error(`still running after ${LONG / 60_000} minutes`);
        await s.page.waitForTimeout(5000);
        if (Date.now() - checked > 60_000) { await s.refresh(); checked = Date.now(); }
    }
}

async function launchFromList(s, inventory, template) {
    await s.section("Automation", "Job templates");
    const row = await templateRow(s, inventory, template);
    await s.click(row.getByRole("button", { name: s.t("Launch"), exact: true }));
    const dialog = s.app.getByRole("dialog");
    const choice = dialog.getByRole("combobox", { name: s.t("Inventory") });
    await s.see(choice);
    await s.click(dialog.getByRole("button", { name: s.t("Launch"), exact: true }));
    await s.click(s.app.getByRole("button", { name: s.t("Open the run") })
        .or(s.app.getByRole("link", { name: s.t("Open the run") })).first());
    await s.see(runStatus(s));
}

export default {
    title: "One template for several labs",
    start: "#/templates",
    steps: [
        {
            name: "a template listed under each inventory it allows",
            run: async (s) => {
                await templateRow(s, "aula-2", "aggiornamenti");
            },
        },
        {
            name: "the updates launched from the row of aula-1",
            run: async (s) => {
                await launchFromList(s, "aula-1", "aggiornamenti");
            },
        },
        {
            name: "Windows Update to its end",
            run: async (s) => {
                await untilEnded(s);
            },
        },
        {
            name: "the wake of aula-2, from the same template, fails",
            run: async (s) => {
                await launchFromList(s, "aula-2", "accensione");
                const status = await untilEnded(s);
                if (status.includes(s.t("Successful"))) throw new Error("the PC that is off woke up");
            },
        },
        {
            name: "a schedule that chooses aula-2",
            run: async (s) => {
                await s.section("Automation", "Schedules");
                await s.click(s.app.getByRole("button", { name: s.t("New schedule") }));
                const dialog = s.app.getByRole("dialog");
                await s.type(dialog.getByRole("textbox", { name: s.t("Name") }), "aggiornamenti aula-2");
                const template = dialog.getByRole("combobox", { name: s.t("Job template") }).or(dialog.getByRole("combobox", { name: "Template" })).first();
                await s.type(template, "aggiorn");
                await s.click(s.app.getByRole("option", { name: /aggiornamenti/ }).first());
                await s.choose(dialog.getByRole("combobox", { name: s.t("Inventory") }), "aula-2");
                await s.choose(dialog.getByRole("combobox", { name: s.t("Repeat") }), s.t("Every week"));
                await s.click(dialog.getByRole("button", { name: s.t("Save"), exact: true }));
                await s.see(s.app.getByRole("grid", { name: s.t("Schedules") }).getByRole("row").filter({ hasText: "aggiornamenti aula-2" }));
            },
        },
        {
            name: "the workflow launched on aula-2, which it asks for",
            run: async (s) => {
                await s.section("Automation", "Workflows");
                const row = s.app.getByRole("row").filter({ hasText: "manutenzione" });
                await s.click(row.getByRole("button", { name: s.t("Launch"), exact: true }));
                const dialog = s.app.getByRole("dialog");
                await s.choose(dialog.getByRole("combobox").first(), "aula-2");
                await s.click(dialog.getByRole("button", { name: s.t("Launch"), exact: true }));
                await s.click(s.app.getByRole("button", { name: s.t("Open the run") })
                    .or(s.app.getByRole("link", { name: s.t("Open the run") })).first());
                await s.see(runStatus(s));
                await untilEnded(s);
            },
        },
    ],
};
