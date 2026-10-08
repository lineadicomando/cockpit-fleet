#!/usr/bin/env node
/**
 * Compiles po/it.po → src/locales/it/translation.json
 *
 * PO plural entries (msgid_plural) are expanded into i18next _one/_other suffixes.
 * Only translated entries (non-empty msgstr) are written; untranslated strings
 * fall back to English at runtime via i18next fallbackLng.
 */
import { readFileSync, writeFileSync } from "fs";
import { po } from "gettext-parser";

const LOCALE = process.argv[2] || "it";
const poPath = `po/${LOCALE}.po`;
const outPath = `src/locales/${LOCALE}/translation.json`;

const catalog = po.parse(readFileSync(poPath)).translations[""];
const result = {};

for (const [msgid, entry] of Object.entries(catalog)) {
    if (!msgid) continue; // skip the PO header entry

    const [singular, plural] = entry.msgstr;

    if (entry.msgid_plural) {
        // Plural form: expand into i18next _one / _many / _other keys
        // Italian CLDR needs _many for numbers ending in 11-19 (e.g. 1.000.011)
        if (singular) result[`${msgid}_one`] = singular;
        if (plural)   result[`${msgid}_many`] = plural;
        if (plural)   result[`${msgid}_other`] = plural;
    } else {
        if (singular) result[msgid] = singular;
    }
}

writeFileSync(outPath, JSON.stringify(result, null, 2) + "\n");
console.log(`po2json: wrote ${Object.keys(result).length} strings → ${outPath}`);
