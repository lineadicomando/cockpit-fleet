// Goes through a scenario of scenarios/ in the container of
// browser.Containerfile, started by browser.sh: headless, quick, stopping at
// the first step whose page does not show what it expects, with a
// screenshot.
//
// A scenario is a module whose default export is
//   { title, start: "#/route", steps: [{ name, run: async (s) => {} }] }
// Labels of the interface are written in English and translated with
// po/LANG.po, as the interface does: s.t().

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = createRequire("/opt/browser/")("playwright");

const env = process.env;
const URL = env.BROWSER_URL.replace(/\/$/, "");
const LANG = env.BROWSER_LANG ?? "it";
const [WIDTH, HEIGHT] = (env.BROWSER_SIZE ?? "1920x1080").split("x").map(Number);
const OUT = "/out";
const NAME = env.BROWSER_SCENARIO;
const TIMEOUT_MS = 30_000;

// --- translations ------------------------------------------------------------

function readPo(path) {
    const map = new Map();
    let id = null, str = null, field = null;
    const flush = () => { if (id && str) map.set(id, str); id = str = field = null; };
    const unquote = (s) => JSON.parse(s);
    for (const line of readFileSync(path, "utf8").split("\n")) {
        if (line.startsWith("msgid ")) { flush(); id = unquote(line.slice(6)); field = "id"; }
        else if (line.startsWith("msgstr ")) { str = unquote(line.slice(7)); field = "str"; }
        else if (line.startsWith('"') && field === "id") id += unquote(line);
        else if (line.startsWith('"') && field === "str") str += unquote(line);
        else if (!line.trim()) flush();
    }
    flush();
    return map;
}

const catalog = LANG === "en" ? new Map() : readPo(`/po/${LANG}.po`);
function t(text, vars = {}) {
    const found = catalog.get(text) ?? text;
    return found.replace(/\{\{(\w+)\}\}/g, (_, key) => String(vars[key] ?? ""));
}

// --- the stage a scenario acts on ----------------------------------------------

class Stage {
    constructor(page) {
        this.page = page;
        this.app = page.frameLocator('iframe[name="cockpit1:localhost/fleet"]');
        this.t = t;
        this.subnet = env.BROWSER_SUBNET ?? "192.168.140";
    }

    // The page of the plugin at a route ("#/checks"), ready.
    async open(route = "") {
        await this.page.goto(`${URL}/fleet${route}`);
        await this.app.locator("main, .pf-v6-c-page__main-section").first().waitFor({ timeout: TIMEOUT_MS });
    }

    // The plugin's frame loaded again, for what a page does not poll for.
    async refresh() {
        const frame = this.page.frame({ name: "cockpit1:localhost/fleet" });
        await frame.evaluate(() => location.reload());
        await this.page.waitForTimeout(500);
        await this.app.locator("main, .pf-v6-c-page__main-section").first().waitFor({ timeout: TIMEOUT_MS });
    }

    async click(locator) {
        await this.see(locator);
        await locator.click();
    }

    async hover(locator) {
        await this.see(locator);
        await locator.hover();
    }

    // A native select. A string chooses the first option whose text holds
    // it, once there is one.
    async choose(locator, option) {
        await this.see(locator);
        if (typeof option === "string") {
            const wanted = locator.locator("option").filter({ hasText: option }).first();
            await wanted.waitFor({ state: "attached", timeout: TIMEOUT_MS });
            option = { value: await wanted.getAttribute("value") };
        }
        await locator.selectOption(option);
    }

    async type(locator, text) {
        await this.click(locator);
        await locator.pressSequentially(text);
    }

    // Waits until the element is visible: what a step expects.
    async see(locator, timeout = TIMEOUT_MS) {
        await locator.first().waitFor({ state: "visible", timeout });
        await locator.first().scrollIntoViewIfNeeded();
    }

    // A tab of the plugin's top menu, then one of its sections.
    async section(group, name) {
        await this.click(this.app.getByRole("tab", { name: t(group), exact: true }));
        if (name) await this.click(this.app.getByRole("tab", { name: t(name), exact: true }));
    }
}

// --- the run ---------------------------------------------------------------------

async function main() {
    const scenario = (await import(`/browser/scenarios/${NAME}.mjs`)).default;
    const browser = await chromium.launch();
    const context = await browser.newContext({
        viewport: { width: WIDTH, height: HEIGHT },
        locale: LANG === "it" ? "it-IT" : "en-US",
        timezoneId: "Europe/Rome",
        bypassCSP: true,
    });
    await context.addCookies([{ name: "CockpitLang", value: LANG === "it" ? "it-it" : "en-us", url: URL }]);
    const page = await context.newPage();
    const stage = new Stage(page);
    let failed = null;
    try {
        // The login page draws its form again once its script has run: fill
        // it only when idle, and let the shell load before going on.
        await page.goto(`${URL}/`, { waitUntil: "networkidle" });
        await page.locator("#login-user-input").fill(env.BROWSER_USER);
        await page.locator("#login-password-input").fill(env.BROWSER_PASSWORD);
        await page.locator("#login-button").click();
        await page.locator("#login-user-input").waitFor({ state: "detached", timeout: TIMEOUT_MS });
        await page.waitForLoadState("networkidle");
        await stage.open(scenario.start ?? "");
        // BROWSER_STEPS=3-5 (or 4): only those, to try a part of a long
        // scenario; each step must then start from a page of its own.
        const [first, last] = (env.BROWSER_STEPS ?? "").split("-").map(Number);
        for (const [i, step] of scenario.steps.entries()) {
            if (first && (i + 1 < first || i + 1 > (last || first))) continue;
            process.stdout.write(`step ${i + 1}/${scenario.steps.length}: ${step.name}\n`);
            try {
                await step.run(stage);
            } catch (error) {
                failed = { step: i + 1, error };
                break;
            }
        }
    } catch (error) {
        failed = { step: 0, error };
    }
    if (failed) await page.screenshot({ path: `${OUT}/failure.png` }).catch(() => {});
    await browser.close();
    if (failed) {
        console.error(`FAILED at step ${failed.step}: ${failed.error.message.split("\n")[0]}`);
        console.error(`screenshot: it-results/browser/${NAME}/failure.png`);
        process.exit(1);
    }
    console.log(`PASS  ${NAME}: ${scenario.steps.length} steps`);
}

main().catch((error) => { console.error(error); process.exit(1); });
