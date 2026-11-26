// Phase 19, network discovery (docs/architecture/network.md): the scan of
// a part of the lab's subnet from the page, the Windows node found with its
// names and MAC address, that address written into an inventory, the
// inventory's hosts probed. On a VM of the lab with win11 up:
// 'BROWSER_LAB=deb13 browser.sh setup network'.

export default {
    title: "Network discovery",
    start: "#/network",
    steps: [
        {
            name: "a part of the lab's subnet scanned",
            run: async (s) => {
                const net = s.subnet;
                await s.click(s.app.getByRole("button", { name: s.t("Scan a subnet") }));
                const dialog = s.app.getByRole("dialog");
                await s.choose(dialog.getByRole("combobox"), { value: `${net}.0/24` });
                const field = dialog.locator("#ns-subnet");
                await s.click(field);
                await field.fill("");
                await s.type(field, `${net}.16/28`);
                await s.click(dialog.getByRole("button", { name: s.t("Scan"), exact: true }));
            },
        },
        {
            name: "win11 found and known as the host of the inventory",
            run: async (s) => {
                const found = s.app.getByRole("grid", { name: s.t("Hosts found") });
                const win = found.getByRole("row").filter({ hasText: `${s.subnet}.20` });
                await s.see(win, 180_000);
                await s.see(win.filter({ hasText: "pc-docente" }));
            },
        },
        {
            name: "its MAC address written into the inventory, after the preview",
            run: async (s) => {
                const found = s.app.getByRole("grid", { name: s.t("Hosts found") });
                await s.click(found.getByRole("row").filter({ hasText: `${s.subnet}.20` }).getByRole("checkbox"));
                await s.click(s.app.getByRole("button", { name: s.t("Write the MAC addresses into an inventory") }));
                const dialog = s.app.getByRole("dialog");
                await s.choose(dialog.getByRole("combobox").first(), "aula-win");
                const preview = dialog.getByRole("grid", { name: s.t("What would be written") });
                await s.see(preview.getByRole("row").filter({ hasText: "pc-docente" }));
                await s.click(dialog.getByRole("button", { name: s.t("Write"), exact: true }));
                await s.see(s.app.getByRole("button", { name: s.t("Scan a subnet") }).or(found).first());
            },
        },
        {
            name: "the inventory's hosts probed",
            run: async (s) => {
                await s.section("Resources", "Inventories");
                const line = s.app.getByRole("row").filter({ hasText: "aula-win" });
                await s.click(line.getByRole("button", { name: s.t("Actions") }));
                await s.click(s.app.getByRole("menuitem", { name: s.t("Probe the hosts"), exact: true }));
                const dialog = s.app.getByRole("dialog");
                const probed = dialog.getByRole("grid", { name: s.t("Hosts probed") });
                await s.see(probed.getByRole("row").filter({ hasText: "pc-01" }), 60_000);
                await s.click(dialog.getByRole("button", { name: s.t("Close") }).first());
            },
        },
    ],
};
