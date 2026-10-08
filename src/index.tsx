import React from "react";
import { createRoot } from "react-dom/client";
import "@patternfly/patternfly/patternfly.css";
import "@patternfly/patternfly/patternfly-addons.css";
import "./app.scss";
import "./i18n.ts";
import { App } from "./app.tsx";

// Mirror pf-v6-theme-dark from the Cockpit shell parent frame onto this iframe
// so PatternFly dark-mode tokens activate when the user switches theme.
function syncDarkTheme() {
    const html = document.documentElement;
    const apply = (dark: boolean) => html.classList.toggle("pf-v6-theme-dark", dark);
    try {
        const parentHtml = window.parent?.document?.documentElement;
        if (parentHtml && parentHtml !== html) {
            const sync = () => apply(parentHtml.classList.contains("pf-v6-theme-dark"));
            sync();
            new MutationObserver(sync).observe(parentHtml, { attributes: true, attributeFilter: ["class"] });
            return;
        }
    } catch { /* cross-origin: skip */ }
    apply(html.classList.contains("pf-v6-theme-dark"));
}

syncDarkTheme();

document.addEventListener("DOMContentLoaded", () => {
    const appEl = document.getElementById("app");
    if (!appEl) throw new Error("Missing #app element");
    createRoot(appEl).render(<App />);
});
