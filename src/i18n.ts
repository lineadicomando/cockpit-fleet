import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en/translation.json";
import it from "./locales/it/translation.json";
import { cookieValue, pickLanguage } from "./lib/language.ts";

const resources = {
    en: { translation: en },
    it: { translation: it },
};

function shellLanguage(): string | undefined {
    try {
        return window.parent?.document?.documentElement?.lang || undefined;
    } catch {
        return undefined; // cross-origin parent
    }
}

i18n.use(initReactI18next).init({
    resources,
    // The language chosen in the Cockpit session menu is in the CockpitLang
    // cookie; without it Cockpit follows the browser, and the shell's <html lang>
    // says which one it picked. cockpit.language is no use here: it stays "en"
    // for a package that ships no Cockpit translation catalog, and this frame's
    // own <html lang> is only updated after this module has run.
    lng: pickLanguage(
        [cookieValue(document.cookie, "CockpitLang"), shellLanguage(), navigator.language],
        Object.keys(resources),
        "en",
    ),
    fallbackLng: "en",
    interpolation: {
        escapeValue: false, // React escapes JSX output
    },
});

export default i18n;
