import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

export async function resolve(specifier, context, nextResolve) {
    if (specifier === "cockpit") {
        return { url: new URL("./mocks/cockpit.ts", import.meta.url).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
}

// What esbuild takes care of in the bundle, for the tests of components:
// JSX, which Node does not strip; JSON imported without attributes, as
// src/i18n.ts does; style sheets, which a test does without (PatternFly's
// components require theirs).
export async function load(url, context, nextLoad) {
    if (!url.startsWith("file:")) return nextLoad(url, context);
    if (/\.s?css$/.test(url)) return { format: context.format === "commonjs" ? "commonjs" : "module", source: "", shortCircuit: true };
    if (url.includes("/node_modules/")) return nextLoad(url, context);
    const path = fileURLToPath(url);
    if (path.endsWith(".tsx")) {
        const { code } = await transform(await readFile(path, "utf8"), {
            loader: "tsx", jsx: "automatic", format: "esm", sourcefile: path, sourcemap: "inline",
        });
        return { format: "module", source: code, shortCircuit: true };
    }
    if (path.endsWith(".json") && !context.importAttributes?.type) {
        return { format: "module", source: `export default ${await readFile(path, "utf8")};`, shortCircuit: true };
    }
    return nextLoad(url, context);
}
