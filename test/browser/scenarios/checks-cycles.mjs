// Phase 22, check cycles in view (docs/checks.md, "Following the cycles"):
// the overview of a classroom's checks, a check run now, the cycle that holds
// it and what it found, the recent cycles of a check, cycles among the runs.
// Data: 'browser.sh setup checks'.

const row = (s, name) => s.app.getByRole("row").filter({ has: s.app.getByRole("gridcell", { name, exact: true }) });
const card = (s, title) => s.app.locator(".pf-v6-c-card").filter({ has: s.app.getByRole("heading", { name: s.t(title) }) });

export default {
    title: "Check cycles in view",
    start: "#/overview",
    steps: [
        {
            name: "the overview, with the state of the checks",
            run: async (s) => {
                await s.see(card(s, "State of the checks"));
                await s.see(card(s, "Problems and recoveries"));
            },
        },
        {
            name: "the checks, the data disk in warning",
            run: async (s) => {
                await s.section("Nodes", "Checks");
                await s.see(row(s, "disco-dati"));
            },
        },
        {
            name: "Check now queues a cycle",
            run: async (s) => {
                const line = row(s, "disco-dati");
                await s.click(line.getByRole("button", { name: s.t("Check now") }));
                // "Checking" lasts as long as the cycle, a few seconds: what
                // stays is the notice.
                await s.see(s.app.getByText(s.t("Started: {{title}}", { title: "disco-dati" })));
            },
        },
        {
            name: "the last cycle's page, what it found",
            run: async (s) => {
                await s.click(row(s, "disco-dati").getByRole("button").filter({ hasText: /\d\d:\d\d/ }));
                const found = s.app.getByRole("grid", { name: s.t("What the cycle found") });
                await s.see(found.getByRole("gridcell", { name: "srv-dati" }), 120_000);
            },
        },
        {
            name: "the recent cycles of a check",
            run: async (s) => {
                await s.section("Nodes", "Checks");
                await s.click(row(s, "disco-dati").getByRole("button", { name: s.t("Actions") }));
                await s.click(s.app.getByRole("menuitem", { name: s.t("Recent cycles"), exact: true }));
                const dialog = s.app.getByRole("dialog");
                await s.see(dialog.getByRole("grid"));
                await s.click(dialog.getByRole("button", { name: s.t("Close") }).first());
            },
        },
        {
            name: "cycles among the runs, through the filter by type",
            run: async (s) => {
                await s.section("Automation", "Runs");
                const filter = s.app.getByRole("combobox", { name: s.t("Filter by type") });
                await s.choose(filter, s.t("Check cycle"));
                await s.see(s.app.getByRole("gridcell", { name: s.t("Check cycle") }).first());
            },
        },
    ],
};
