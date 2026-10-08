/** @type {import('i18next-parser').UserConfig} */
export default {
    // Use the English string itself as the key (no symbolic dot-notation keys).
    // When keySeparator/namespaceSeparator are false, the full string is treated
    // as a flat key — avoids maintaining a separate key catalog.
    keySeparator: false,
    namespaceSeparator: false,

    defaultNamespace: "translation",
    // For English, the default value IS the key (the string itself).
    // For other locales, leave empty so missing translations fall back to English.
    defaultValue: (locale, _ns, key) => (locale === "en" ? key : ""),

    locales: ["en", "it"],
    input: ["src/**/*.{ts,tsx}"],
    output: "src/locales/$LOCALE/translation.json",

    createOldCatalogs: false, // don't create _old.json files with removed keys
    keepRemoved: false,       // purge keys no longer in source
    sort: true,
    indentation: 2,
    verbose: false,
};
